from __future__ import annotations

import logging
import math

from src.clients.voyage import (
    VOYAGE_EMBEDDING_DIM,
    VOYAGE_MAX_BATCH,
    VoyageClient,
    VoyageError,
)

logger = logging.getLogger(__name__)

EMBEDDING_DIM = VOYAGE_EMBEDDING_DIM
BATCH_SIZE = VOYAGE_MAX_BATCH


class EmbeddingError(RuntimeError):
    """Raised when the embedding API fails."""


async def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    out: list[list[float]] = []
    try:
        async with VoyageClient() as client:
            for start in range(0, len(texts), BATCH_SIZE):
                batch = texts[start : start + BATCH_SIZE]
                vectors = await client.embed(batch)
                for v in vectors:
                    if len(v) != EMBEDDING_DIM:
                        raise EmbeddingError(
                            f"Voyage returned {len(v)}-dim vector; "
                            f"expected {EMBEDDING_DIM}"
                        )
                out.extend(vectors)
    except EmbeddingError:
        raise
    except VoyageError as exc:
        raise EmbeddingError(f"Voyage embedding failed: {exc}") from exc
    except Exception as exc:
        raise EmbeddingError(f"Unexpected embedding failure: {exc}") from exc
    return out


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))
