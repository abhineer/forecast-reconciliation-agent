"""Deterministic reconciliation math.

All arithmetic (variances, thresholds, flags) is computed in plain
Python/pandas rather than left to the LLM, since numbers must be exact
and reproducible. The LLM's job (see agent.py) is limited to narrating
*why* a gap likely exists and proposing a reconciled number, grounded
in these pre-computed signals.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Thresholds for flagging a likely root cause. Tuned for a demo, not
# calibrated against real historical data.
ROS_SHIFT_THRESHOLD_PCT = 5.0
CARRYOVER_THRESHOLD_PCT = 15.0
NEW_STORE_UNITS_THRESHOLD = 200
PLAN_DIVERGENCE_THRESHOLD_PCT = 5.0
MATERIAL_GAP_THRESHOLD_PCT = 5.0


@dataclass
class ClassSignals:
    class_name: str
    category: str
    season: str
    top_down_target: float
    store_plan_total: float
    line_plan_total: float
    class_plan_total: float
    bottom_up_consensus: float
    gap_abs: float
    gap_pct: float
    is_material: bool
    rate_of_sale_trend_pct: float
    carryover_pct: float
    new_store_units: int
    plan_divergence_pct: float
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "class": self.class_name,
            "category": self.category,
            "season": self.season,
            "top_down_target": self.top_down_target,
            "store_plan_total": self.store_plan_total,
            "line_plan_total": self.line_plan_total,
            "class_plan_total": self.class_plan_total,
            "bottom_up_consensus": round(self.bottom_up_consensus, 2),
            "gap_abs": round(self.gap_abs, 2),
            "gap_pct": round(self.gap_pct, 2),
            "is_material": self.is_material,
            "rate_of_sale_trend_pct": self.rate_of_sale_trend_pct,
            "carryover_pct": self.carryover_pct,
            "new_store_units": self.new_store_units,
            "plan_divergence_pct": round(self.plan_divergence_pct, 2),
            "flags": self.flags,
        }


def compute_signals(row: pd.Series) -> ClassSignals:
    bottom_up_values = [
        row["store_plan_total"],
        row["line_plan_total"],
        row["class_plan_total"],
    ]
    bottom_up_consensus = sum(bottom_up_values) / len(bottom_up_values)

    top_down = float(row["top_down_target"])
    gap_abs = top_down - bottom_up_consensus
    gap_pct = (gap_abs / top_down * 100) if top_down else 0.0

    plan_divergence_pct = (
        (max(bottom_up_values) - min(bottom_up_values)) / bottom_up_consensus * 100
        if bottom_up_consensus
        else 0.0
    )

    flags: list[str] = []
    if abs(row["rate_of_sale_trend_pct"]) >= ROS_SHIFT_THRESHOLD_PCT:
        flags.append("rate_of_sale_shift")
    if row["carryover_pct"] >= CARRYOVER_THRESHOLD_PCT:
        flags.append("carryover_assumption")
    if row["new_store_units"] >= NEW_STORE_UNITS_THRESHOLD:
        flags.append("new_store_ramp")
    if plan_divergence_pct >= PLAN_DIVERGENCE_THRESHOLD_PCT:
        flags.append("planner_disagreement")
    if not flags and abs(gap_pct) >= MATERIAL_GAP_THRESHOLD_PCT:
        flags.append("unexplained_gap")

    return ClassSignals(
        class_name=row["class"],
        category=row["category"],
        season=row["season"],
        top_down_target=top_down,
        store_plan_total=float(row["store_plan_total"]),
        line_plan_total=float(row["line_plan_total"]),
        class_plan_total=float(row["class_plan_total"]),
        bottom_up_consensus=bottom_up_consensus,
        gap_abs=gap_abs,
        gap_pct=gap_pct,
        is_material=abs(gap_pct) >= MATERIAL_GAP_THRESHOLD_PCT,
        rate_of_sale_trend_pct=float(row["rate_of_sale_trend_pct"]),
        carryover_pct=float(row["carryover_pct"]),
        new_store_units=int(row["new_store_units"]),
        plan_divergence_pct=plan_divergence_pct,
        flags=flags,
    )


def build_variance_table(df: pd.DataFrame) -> pd.DataFrame:
    """Return one row of signals per class, sorted by |gap %| descending."""
    records = [compute_signals(row).to_dict() for _, row in df.iterrows()]
    out = pd.DataFrame.from_records(records)
    return out.sort_values("gap_pct", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)


def get_class_signals(df: pd.DataFrame, class_name: str) -> ClassSignals:
    matches = df[df["class"].str.lower() == class_name.lower()]
    if matches.empty:
        raise KeyError(f"No plan found for class '{class_name}'")
    return compute_signals(matches.iloc[0])
