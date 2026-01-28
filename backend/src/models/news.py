from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .filing import Source


class NewsArticle(BaseModel):
    headline: str
    url: str
    source: str
    published_at: datetime
    raw_text: str | None = None
    provider: Literal["finnhub", "marketaux", "google_news"]
    sentiment_pre_scored: float | None = None
    source_obj: Source
