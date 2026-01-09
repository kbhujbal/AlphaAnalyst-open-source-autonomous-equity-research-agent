from __future__ import annotations

from pydantic import BaseModel, Field

from .filing import Source


class Transcript(BaseModel):
    ticker: str
    quarter: int = Field(ge=1, le=4)
    year: int
    content: str
    source: Source
