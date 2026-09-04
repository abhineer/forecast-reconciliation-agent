"""Gradio front end for the Forecast Reconciliation Agent."""

from __future__ import annotations

import os
import tempfile

import gradio as gr
import pandas as pd
from dotenv import load_dotenv

from forecast_reconciliation_agent.agent import ForecastReconciliationAgent
from forecast_reconciliation_agent.data import load_plans
from forecast_reconciliation_agent import db

load_dotenv()

VARIANCE_COLUMNS = [
    "class",
    "category",
    "top_down_target",
    "bottom_up_consensus",
    "gap_abs",
    "gap_pct",
    "flags",
]

APPROVED_COLUMNS = [
    "class",
    "top_down_target",
    "bottom_up_consensus",
    "reconciled_number",
    "approved_value",
    "decision",
    "role",
    "confidence",
]

LINEAGE_COLUMNS = [
    "created_at",
    "event_type",
    "role",
    "previous_value",
    "new_value",
    "confidence",
    "justification",
]


def _lineage_view(class_name: str | None) -> pd.DataFrame:
    if not class_name:
        return pd.DataFrame(columns=LINEAGE_COLUMNS)
    events = db.get_lineage(class_name)
    if not events:
        return pd.DataFrame(columns=LINEAGE_COLUMNS)
    view = pd.DataFrame.from_records(events)
    return view[LINEAGE_COLUMNS]


def _build_agent() -> ForecastReconciliationAgent:
    return ForecastReconciliationAgent(df=load_plans())


def _variance_view(df: pd.DataFrame) -> pd.DataFrame:
    from forecast_reconciliation_agent.reconciliation import build_variance_table

    table = build_variance_table(df)
    view = table[VARIANCE_COLUMNS].copy()
    view["flags"] = view["flags"].apply(lambda f: ", ".join(f) if f else "-")
    return view


def _format_recommendation(result: dict) -> str:
    s = result["signals"]
    r = result["recommendation"]
    flags = ", ".join(s["flags"]) if s["flags"] else "none"
    return f"""### {s['class']} — {s['category']} ({s['season']})

| | Value |
|---|---|
| Top-down target | {s['top_down_target']:,.0f} |
| Bottom-up consensus | {s['bottom_up_consensus']:,.0f} |
| Store Plan | {s['store_plan_total']:,.0f} |
| Line Plan | {s['line_plan_total']:,.0f} |
| Class Plan | {s['class_plan_total']:,.0f} |
| Gap | {s['gap_abs']:,.0f} ({s['gap_pct']:.1f}%) |
| Plan divergence | {s['plan_divergence_pct']:.1f}% |
| Flags | {flags} |

**Root cause:** {r['root_cause']}

**Recommended reconciled number:** {r['reconciled_number']:,.0f}

**Rationale:** {r['rationale']}

**Confidence:** {r['confidence'].upper()}  ·  **Suggested approver:** {r['recommended_owner'].replace('_', ' ').title()}
"""


def build_demo() -> gr.Blocks:
    db.init_db()
    with gr.Blocks(title="Forecast Reconciliation Agent") as demo:
        gr.Markdown(
            "# Forecast Reconciliation Agent\n"
            "Reconciles the top-down financial target against each planner's bottom-up "
            "build (Store Plan / Line Plan / Class Plan), surfaces every material gap, "
            "explains why it exists, and proposes a reconciled number both sides can "
            "approve. Powered by LangChain + Groq (`openai/gpt-oss-20b`)."
        )

        agent_state = gr.State(None)
        approved_state = gr.State(pd.DataFrame(columns=APPROVED_COLUMNS))
        last_result_state = gr.State(None)

        with gr.Row():
            role_dropdown = gr.Dropdown(
                label="Your role",
                choices=db.ROLES,
                value=db.ROLES[0],
                info="Attached to every proposal or approval you record below.",
            )

        with gr.Tabs():
            # ---------------- Dashboard tab ----------------
            with gr.Tab("Reconciliation Dashboard"):
                with gr.Row():
                    csv_upload = gr.File(
                        label="Optional: upload your own plan CSV (same columns as sample_data/plans.csv)",
                        file_types=[".csv"],
                    )
                    load_btn = gr.Button("Load sample data", variant="secondary")

                variance_df = gr.Dataframe(label="Top-down vs. bottom-up variance, all classes", interactive=False)

                with gr.Row():
                    class_dropdown = gr.Dropdown(label="Select a class to reconcile", choices=[])
                    analyze_btn = gr.Button("Analyze gap", variant="primary")

                recommendation_md = gr.Markdown()

                with gr.Row():
                    accept_topdown_btn = gr.Button("Approve: use top-down target")
                    accept_bottomup_btn = gr.Button("Approve: use bottom-up consensus")
                    accept_reconciled_btn = gr.Button("Approve: use reconciled number", variant="primary")
                    accept_proposed_btn = gr.Button("Approve: use proposed forecast")

                gr.Markdown("### Approved reconciliations")
                approved_df = gr.Dataframe(label="Approved plan (this session)", interactive=False)
                download_btn = gr.DownloadButton(label="Download approved plan as CSV", visible=False)

                gr.Markdown(
                    "### Propose a different forecast\n"
                    "Disagree with the agent's number? Propose your own value with a "
                    "justification — it's recorded against your role and chained to "
                    "the prior number in the lineage below."
                )
                with gr.Row():
                    proposal_value = gr.Number(label="Your proposed forecast")
                    proposal_justification = gr.Textbox(
                        label="Justification", placeholder="Why should the forecast change?", scale=2
                    )
                    propose_btn = gr.Button("Submit proposal")
                proposal_status_md = gr.Markdown()

                gr.Markdown("### Forecast lineage")
                lineage_df = gr.Dataframe(label="Full history for the selected class", interactive=False)

            # ---------------- Copilot tab ----------------
            with gr.Tab("Ask the Copilot"):
                gr.Markdown(
                    "Ask questions like *\"why is Denim Best behind plan?\"* or "
                    "*\"which classes have the biggest gap?\"*. The copilot calls the "
                    "same reconciliation tools as the dashboard."
                )
                chatbot = gr.Chatbot(height=420)
                chat_input = gr.Textbox(placeholder="Ask about a class or the overall reconciliation...", label="")
                chat_clear = gr.Button("Clear chat")

        # ---------------- callbacks ----------------

        def _ensure_agent(agent):
            if agent is None:
                agent = _build_agent()
            return agent

        def on_load_sample(agent):
            agent = _ensure_agent(agent)
            agent.load_data(load_plans())
            table = _variance_view(agent.df)
            choices = sorted(agent.df["class"].unique().tolist())
            return agent, table, gr.update(choices=choices, value=choices[0] if choices else None)

        def on_upload(file, agent):
            agent = _ensure_agent(agent)
            if file is None:
                table = _variance_view(agent.df)
                choices = sorted(agent.df["class"].unique().tolist())
                return agent, table, gr.update(choices=choices)
            df = load_plans(file.name)
            agent.load_data(df)
            table = _variance_view(agent.df)
            choices = sorted(agent.df["class"].unique().tolist())
            return agent, table, gr.update(choices=choices, value=choices[0] if choices else None)

        def on_analyze(class_name, agent):
            agent = _ensure_agent(agent)
            if not class_name:
                return "Select a class first.", None, agent, _lineage_view(class_name)
            result = agent.reconcile_class(class_name)
            s, r = result["signals"], result["recommendation"]
            db.record_event(
                class_name=s["class"],
                event_type="agent_recommendation",
                new_value=r["reconciled_number"],
                category=s["category"],
                season=s["season"],
                justification=r["rationale"],
                top_down_target=s["top_down_target"],
                bottom_up_consensus=s["bottom_up_consensus"],
                confidence=r["confidence"],
            )
            return _format_recommendation(result), result, agent, _lineage_view(class_name)

        def _record_decision(decision_label, value, justification, result, approved, role):
            s, r = result["signals"], result["recommendation"]
            db.record_event(
                class_name=s["class"],
                event_type="approval",
                new_value=value,
                category=s["category"],
                season=s["season"],
                role=role,
                justification=justification,
                top_down_target=s["top_down_target"],
                bottom_up_consensus=s["bottom_up_consensus"],
                confidence=r["confidence"],
            )
            row = {
                "class": s["class"],
                "top_down_target": s["top_down_target"],
                "bottom_up_consensus": s["bottom_up_consensus"],
                "reconciled_number": r["reconciled_number"],
                "approved_value": value,
                "decision": decision_label,
                "role": role,
                "confidence": r["confidence"],
            }
            approved = approved[approved["class"] != s["class"]] if not approved.empty else approved
            approved = pd.concat([approved, pd.DataFrame([row])], ignore_index=True)
            csv_path = os.path.join(tempfile.gettempdir(), "approved_reconciliation_plan.csv")
            approved.to_csv(csv_path, index=False)
            return approved, gr.update(value=csv_path, visible=True), _lineage_view(s["class"])

        def on_accept_topdown(result, approved, role):
            if result is None:
                return approved, gr.update(), _lineage_view(None)
            s = result["signals"]
            return _record_decision(
                "top_down", s["top_down_target"], "Approved via 'top_down' decision.", result, approved, role
            )

        def on_accept_bottomup(result, approved, role):
            if result is None:
                return approved, gr.update(), _lineage_view(None)
            s = result["signals"]
            return _record_decision(
                "bottom_up", s["bottom_up_consensus"], "Approved via 'bottom_up' decision.", result, approved, role
            )

        def on_accept_reconciled(result, approved, role):
            if result is None:
                return approved, gr.update(), _lineage_view(None)
            r = result["recommendation"]
            return _record_decision(
                "reconciled", r["reconciled_number"], "Approved via 'reconciled' decision.", result, approved, role
            )

        def on_accept_proposed(result, approved, role):
            if result is None:
                return approved, gr.update(), _lineage_view(None), "Select and analyze a class first."
            s = result["signals"]
            proposal = db.get_latest_proposal(s["class"])
            if proposal is None:
                return (
                    approved,
                    gr.update(),
                    _lineage_view(s["class"]),
                    f"No proposal has been submitted yet for **{s['class']}**.",
                )
            value = proposal["new_value"]
            justification = (
                f"Approved via 'proposed_forecast' decision "
                f"(originally proposed by {proposal['role']}: {proposal['justification']})."
            )
            approved, download_update, lineage = _record_decision(
                "proposed_forecast", value, justification, result, approved, role
            )
            return (
                approved,
                download_update,
                lineage,
                f"Approved **{s['class']}** using the proposed forecast: {value:,.0f}.",
            )

        def on_propose(class_name, value, justification, role):
            if not class_name:
                return "Select and analyze a class first.", _lineage_view(class_name)
            if value is None:
                return "Enter a proposed forecast value.", _lineage_view(class_name)
            if not justification or not justification.strip():
                return "A justification is required for a proposal.", _lineage_view(class_name)
            db.record_event(
                class_name=class_name,
                event_type="user_proposal",
                new_value=float(value),
                role=role,
                justification=justification.strip(),
            )
            return (
                f"Proposal recorded for **{class_name}** by {role}: {value:,.0f}.",
                _lineage_view(class_name),
            )

        def on_chat(message, history, agent):
            agent = _ensure_agent(agent)
            history = history or []
            history = history + [{"role": "user", "content": message}]
            reply = agent.chat(message, history[:-1])
            history = history + [{"role": "assistant", "content": reply}]
            return history, "", agent

        demo.load(on_load_sample, inputs=[agent_state], outputs=[agent_state, variance_df, class_dropdown])
        load_btn.click(on_load_sample, inputs=[agent_state], outputs=[agent_state, variance_df, class_dropdown])
        csv_upload.change(on_upload, inputs=[csv_upload, agent_state], outputs=[agent_state, variance_df, class_dropdown])

        analyze_btn.click(
            on_analyze,
            inputs=[class_dropdown, agent_state],
            outputs=[recommendation_md, last_result_state, agent_state, lineage_df],
        )

        accept_topdown_btn.click(
            on_accept_topdown,
            inputs=[last_result_state, approved_state, role_dropdown],
            outputs=[approved_state, download_btn, lineage_df],
        )
        accept_bottomup_btn.click(
            on_accept_bottomup,
            inputs=[last_result_state, approved_state, role_dropdown],
            outputs=[approved_state, download_btn, lineage_df],
        )
        accept_reconciled_btn.click(
            on_accept_reconciled,
            inputs=[last_result_state, approved_state, role_dropdown],
            outputs=[approved_state, download_btn, lineage_df],
        )
        accept_proposed_btn.click(
            on_accept_proposed,
            inputs=[last_result_state, approved_state, role_dropdown],
            outputs=[approved_state, download_btn, lineage_df, proposal_status_md],
        )

        propose_btn.click(
            on_propose,
            inputs=[class_dropdown, proposal_value, proposal_justification, role_dropdown],
            outputs=[proposal_status_md, lineage_df],
        )

        approved_state.change(lambda a: a, inputs=[approved_state], outputs=[approved_df])

        chat_input.submit(on_chat, inputs=[chat_input, chatbot, agent_state], outputs=[chatbot, chat_input, agent_state])
        chat_clear.click(lambda: [], outputs=[chatbot])

    return demo


def main():
    if not os.environ.get("GROQ_API_KEY"):
        raise SystemExit(
            "GROQ_API_KEY is not set. Copy .env.example to .env, add your Groq API key, and retry."
        )
    demo = build_demo()
    demo.launch(theme=gr.themes.Soft())


if __name__ == "__main__":
    main()
