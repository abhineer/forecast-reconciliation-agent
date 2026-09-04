"""Structured output schema the LLM must fill in for each reconciliation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ReconciliationRecommendation(BaseModel):
    root_cause: str = Field(
        description=(
            "2-4 sentence plain-English explanation of why the top-down target "
            "and bottom-up build diverge for this class, grounded in the "
            "supplied signals (rate-of-sale trend, carryover assumption, "
            "new-store ramp, planner disagreement)."
        )
    )
    reconciled_number: float = Field(
        description=(
            "The recommended reconciled plan value, in the same currency units "
            "as the inputs. Should sit between the top-down target and the "
            "bottom-up consensus unless the signals clearly justify otherwise, "
            "and must be explained by the rationale."
        )
    )
    rationale: str = Field(
        description="1-3 sentence justification for why the reconciled number was chosen."
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description="Confidence that the reconciled number is right, given the available signals."
    )
    recommended_owner: Literal["planner", "finance", "joint_review"] = Field(
        description="Who should approve this reconciliation next."
    )
