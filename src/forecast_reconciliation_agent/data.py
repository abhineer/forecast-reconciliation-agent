"""Loading and preparation of the plan-vs-plan dataset.

The dataset represents, per merchandise class, the top-down financial
target set by the finance/merchandising leadership and the three
bottom-up builds planners assemble independently: Store Plan, Line Plan
and Class Plan. Real deployments would source these from o9 MFP /
Assortment Planning; here we read a flat CSV so the agent logic can be
demoed without a live planning system.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DEFAULT_DATA_PATH = Path(__file__).resolve().parents[2] / "sample_data" / "plans.csv"

REQUIRED_COLUMNS = [
    "class",
    "category",
    "season",
    "top_down_target",
    "store_plan_total",
    "line_plan_total",
    "class_plan_total",
    "rate_of_sale_trend_pct",
    "carryover_pct",
    "new_store_units",
]


def load_plans(path: str | Path | None = None) -> pd.DataFrame:
    """Load the plan-vs-plan dataset from CSV.

    Falls back to the bundled sample dataset when no path is given.
    """
    csv_path = Path(path) if path else DEFAULT_DATA_PATH
    df = pd.read_csv(csv_path)

    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Plan dataset is missing required columns: {sorted(missing)}")

    return df
