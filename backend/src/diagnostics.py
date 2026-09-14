"""Sanitize provider diagnostics before including them in reports or API errors."""
from __future__ import annotations

import re

from src.settings import settings

_QUERY_SECRET = re.compile(r"([?&](?:api[_-]?key|token|access_token)=)[^&\s\"'<>]+", re.IGNORECASE)


def public_diagnostic(message: str) -> str:
    for name in type(settings).model_fields:
        if name.endswith("_api_key"):
            secret = getattr(settings, name)
            if secret:
                message = message.replace(secret, "[redacted]")
    return _QUERY_SECRET.sub(r"\1[redacted]", message)
