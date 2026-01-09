from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis

from src.settings import settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def get_json(key: str) -> Any | None:
    raw = await get_redis().get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def set_json(key: str, value: Any, ttl: int | None = None) -> None:
    payload = json.dumps(value, default=str)
    if ttl is None:
        await get_redis().set(key, payload)
    else:
        await get_redis().set(key, payload, ex=ttl)


async def delete(key: str) -> None:
    await get_redis().delete(key)


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
