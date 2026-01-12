from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    source_type: Literal[
        "filing", "transcript", "news", "fact", "price", "macro", "estimates"
    ]
    source_id: str
    snippet: str


class Finding(BaseModel):
    claim: str
    evidence: list[Citation] = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]
