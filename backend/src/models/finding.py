from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from src.models.evidence import SourcePassage


class Citation(BaseModel):
    tag: str | None = Field(default=None, pattern=r"^F[1-9]\d*$")
    source_type: Literal[
        "filing", "transcript", "news", "fact", "price", "macro", "estimates"
    ]
    source_id: str
    snippet: str
    passages: list[SourcePassage] = Field(default_factory=list)
    observation_id: str | None = None


class Finding(BaseModel):
    claim: str
    evidence: list[Citation] = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]
