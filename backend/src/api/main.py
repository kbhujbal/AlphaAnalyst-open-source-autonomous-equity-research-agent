from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src import cache
from src.api.routes import router
from src.db import engine
from src.settings import settings

logger = logging.getLogger(__name__)


def _parse_cors(value: str) -> list[str]:
    return [o.strip() for o in (value or "").split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting AlphaAnalyst API; CORS origins=%s", settings.cors_origins)
    yield
    try:
        await cache.close()
    except Exception as exc:  # pragma: no cover — best-effort shutdown
        logger.warning("redis close failed: %s", exc)
    try:
        await engine.dispose()
    except Exception as exc:  # pragma: no cover
        logger.warning("db engine dispose failed: %s", exc)


app = FastAPI(
    title="AlphaAnalyst API",
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "analysis", "description": "Submit and track analyses."},
        {"name": "memos", "description": "Read completed memos and exports."},
        {"name": "health", "description": "Service health probes."},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def _http_exc_handler(request: Request, exc: HTTPException) -> JSONResponse:
    request_id = str(uuid4())
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "code": f"HTTP_{exc.status_code}",
            "request_id": request_id,
        },
    )


@app.exception_handler(RequestValidationError)
async def _validation_exc_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    request_id = str(uuid4())
    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors(),
            "code": "VALIDATION_ERROR",
            "request_id": request_id,
        },
    )


@app.exception_handler(Exception)
async def _global_exc_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = str(uuid4())
    logger.error(
        "unhandled exception (request_id=%s) on %s %s: %s",
        request_id,
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "internal server error",
            "code": "INTERNAL_ERROR",
            "request_id": request_id,
        },
    )


app.include_router(router)
