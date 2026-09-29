"""Versioned text evidence. Offsets are Unicode code points in the captured text."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

Scope = Literal["normalized_filing", "transcript", "feed_excerpt", "provider_record"]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CapturedDocument(BaseModel):
    ticker: str
    source_id: str
    source_url: str
    scope: Scope
    text: str = Field(min_length=1)
    retrieved_at: datetime

    @field_validator("source_url")
    @classmethod
    def http_source(cls, value):
        if urlsplit(value).scheme not in {"https", "http"} or not urlsplit(value).hostname:
            raise ValueError("Evidence requires an HTTP(S) source URL")
        return value

    @field_validator("retrieved_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Evidence retrieval timestamps must be timezone-aware")
        return value

    @computed_field
    @property
    def content_hash(self) -> str:
        return text_hash(self.text)

    @computed_field
    @property
    def document_id(self) -> str:
        return text_hash("\0".join((self.ticker, self.source_id, self.source_url, self.scope, self.content_hash)))


class SourcePassage(BaseModel):
    document_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    document_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_id: str
    source_url: str
    scope: Scope
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)
    text_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    retrieved_at: datetime
    snapshot_url: str | None = None

    @model_validator(mode="after")
    def coherent_span(self):
        if self.end - self.start != len(self.text) or text_hash(self.text) != self.text_hash:
            raise ValueError("Passage offsets and text hash do not match the quoted text")
        return self


def passage_at(document: CapturedDocument, start: int, end: int) -> SourcePassage:
    if not 0 <= start < end <= len(document.text):
        raise ValueError("Passage lies outside the captured document")
    quote = document.text[start:end]
    return SourcePassage(
        document_id=document.document_id, document_hash=document.content_hash,
        source_id=document.source_id, source_url=document.source_url, scope=document.scope,
        start=start, end=end, text=quote, text_hash=text_hash(quote), retrieved_at=document.retrieved_at,
    )


def passage_matches(passage: SourcePassage, document: CapturedDocument, ticker: str) -> bool:
    return (
        document.ticker == ticker and passage.document_id == document.document_id
        and passage.document_hash == document.content_hash and passage.source_id == document.source_id
        and passage.source_url == document.source_url and passage.scope == document.scope
        and passage.retrieved_at == document.retrieved_at
        and 0 <= passage.start < passage.end <= len(document.text)
        and document.text[passage.start:passage.end] == passage.text
        and text_hash(passage.text) == passage.text_hash
    )
