from __future__ import annotations

from pydantic import BaseModel, Field


class FixOutput(BaseModel):
    """Structured output requested from the model once it stops calling tools."""

    diff: str = Field(description="A unified diff (git apply compatible) implementing the fix")
    explanation: str = Field(description="Why this change addresses the root cause")
    files_touched: list[str] = Field(description="Repo-relative paths changed by the diff")
