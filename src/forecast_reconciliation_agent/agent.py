"""LangChain + Groq agent layer.

Two things live here:

1. A structured-output chain (`reconcile_class`) that turns the
   deterministic signals for one class into a narrated root cause,
   a recommended reconciled number, and a confidence level.
2. A tool-calling conversational agent (`chat`) that lets a planner ask
   free-form questions ("why is Denim Best behind?") and have the LLM
   pull the relevant class/variance data itself before answering -
   a small stand-in for the "Conversational Planning Copilot" idea
   in the same research note, scoped down to this agent's data.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from langchain_groq import ChatGroq

from forecast_reconciliation_agent.models import ReconciliationRecommendation
from forecast_reconciliation_agent.reconciliation import (
    build_variance_table,
    get_class_signals,
)

load_dotenv()

DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")

RECONCILE_SYSTEM_PROMPT = """You are a merchandise planning reconciliation analyst for a retail \
merchandising organization. You sit between the top-down financial target (set by finance / \
merchandising leadership) and the bottom-up build assembled independently by planners across \
three views: Store Plan, Line Plan, and Class Plan.

You are given, for one merchandise class:
- the top-down target
- the three bottom-up totals and their consensus (average)
- the absolute and percentage gap between top-down and bottom-up
- supporting signals: rate-of-sale trend, carryover assumption %, new-store unit exposure, and \
how much the three bottom-up views disagree with each other (plan divergence %)
- which signals crossed a materiality threshold (the "flags")

Your job: explain the most likely root cause of the gap in plain English a planner and a \
finance lead would both accept, then propose a single reconciled number both sides could sign \
off on. Do not just split the difference by default - reason about which signals actually \
explain the gap and weight the reconciled number accordingly. If no flags are set but the gap \
is still material, say so plainly and recommend a joint review rather than guessing.

Only use the numbers given to you. Do not invent data."""

RECONCILE_USER_TEMPLATE = """Class: {class_name} ({category}, {season})

Top-down target: {top_down_target:,.0f}
Bottom-up consensus: {bottom_up_consensus:,.0f}
  - Store Plan total: {store_plan_total:,.0f}
  - Line Plan total: {line_plan_total:,.0f}
  - Class Plan total: {class_plan_total:,.0f}

Gap (top-down minus bottom-up): {gap_abs:,.0f} ({gap_pct:.1f}%)
Plan divergence across the three bottom-up views: {plan_divergence_pct:.1f}%

Supporting signals:
  - Rate-of-sale trend: {rate_of_sale_trend_pct:+.1f}%
  - Carryover assumption: {carryover_pct:.1f}%
  - New-store unit exposure: {new_store_units}

Flags triggered: {flags}

Propose the reconciliation."""


class ForecastReconciliationAgent:
    def __init__(self, df: pd.DataFrame, model: str = DEFAULT_MODEL, temperature: float = 0.2):
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Copy .env.example to .env and add your Groq API key."
            )

        self.df = df
        self.model = model
        self.llm = ChatGroq(model=model, temperature=temperature, api_key=api_key)
        self._reconcile_chain = self._build_reconcile_chain()
        self._chat_executor = self._build_chat_executor()

    # -- structured per-class reconciliation -----------------------------

    def _build_reconcile_chain(self):
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", RECONCILE_SYSTEM_PROMPT),
                ("human", RECONCILE_USER_TEMPLATE),
            ]
        )
        structured_llm = self.llm.with_structured_output(ReconciliationRecommendation)
        return prompt | structured_llm

    def load_data(self, df: pd.DataFrame) -> None:
        """Swap in a new plan dataset (e.g. from an uploaded CSV)."""
        self.df = df

    def get_variance_table(self) -> pd.DataFrame:
        return build_variance_table(self.df)

    def reconcile_class(self, class_name: str) -> dict[str, Any]:
        signals = get_class_signals(self.df, class_name)
        signal_dict = signals.to_dict()
        flags_text = ", ".join(signal_dict["flags"]) if signal_dict["flags"] else "none"

        recommendation: ReconciliationRecommendation = self._reconcile_chain.invoke(
            {**signal_dict, "class_name": signal_dict["class"], "flags": flags_text}
        )

        return {
            "signals": signal_dict,
            "recommendation": recommendation.model_dump(),
        }

    # -- conversational copilot ------------------------------------------

    def _build_chat_executor(self):
        # Tools close over `self` (not a snapshot of self.df) so that
        # swapping in a new dataset via `load_data` is picked up by the
        # already-built chat agent without rebuilding it.

        @tool
        def list_classes() -> str:
            """List every merchandise class available in the current plan dataset."""
            return ", ".join(sorted(self.df["class"].unique()))

        @tool
        def get_class_variance(class_name: str) -> str:
            """Get the top-down vs bottom-up variance and supporting signals for one
            merchandise class. Use this before explaining why a class is ahead or
            behind plan."""
            signals = get_class_signals(self.df, class_name)
            return str(signals.to_dict())

        @tool
        def get_reconciliation(class_name: str) -> str:
            """Get the full reconciliation recommendation (root cause, reconciled
            number, confidence, recommended approver) for one merchandise class."""
            result = self.reconcile_class(class_name)
            return str(result)

        @tool
        def rank_classes_by_gap(top_n: int = 5) -> str:
            """List the classes with the largest absolute top-down vs bottom-up gap,
            largest first. Useful for 'what should I look at first' questions."""
            table = self.get_variance_table().head(top_n)
            cols = ["class", "top_down_target", "bottom_up_consensus", "gap_abs", "gap_pct", "flags"]
            return table[cols].to_string(index=False)

        tools = [list_classes, get_class_variance, get_reconciliation, rank_classes_by_gap]

        system_prompt = (
            "You are the Forecast Reconciliation Copilot, a conversational assistant "
            "for merchandise planners and finance leads. Answer questions about "
            "plan-vs-plan gaps for the current season using the tools available to "
            "you - always call a tool to get real numbers before answering; never "
            "invent figures. Keep answers concise and planner-friendly."
        )
        return create_agent(self.llm, tools=tools, system_prompt=system_prompt)

    def chat(self, message: str, history: list[dict[str, str]] | None = None) -> str:
        messages = [
            {"role": turn["role"], "content": turn.get("content", "")}
            for turn in (history or [])
            if turn.get("role") in ("user", "assistant")
        ]
        messages.append({"role": "user", "content": message})

        result = self._chat_executor.invoke({"messages": messages})
        return result["messages"][-1].content
