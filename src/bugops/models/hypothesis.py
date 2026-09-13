from __future__ import annotations

from pydantic import BaseModel, Field


class HypothesisOutput(BaseModel):
    summary: str = Field(description="One-sentence description of the suspected root cause")
    suspected_files: list[str] = Field(description="Repo-relative paths most likely responsible")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence this is the root cause")
    reasoning: str = Field(description="Why the evidence supports this hypothesis")


class InvestigationConclusion(BaseModel):
    """Structured output requested from the model once it stops calling tools."""

    hypotheses: list[HypothesisOutput] = Field(description="One or more ranked root-cause hypotheses")
