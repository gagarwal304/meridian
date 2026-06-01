"""
Local OTEL/HTTP collector.

Receives OTLP traces from Claude Code and writes them to the local SQLite store.
Supports OTLP/HTTP JSON (the recommended protocol for local dev).

Usage:
    meridian collect                  # port 4318, localhost only
    meridian collect --port 4319
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response

from .ingest import from_otlp_json
from .store import connect  # SQLite WAL — safe for long-lived connections

log = logging.getLogger("meridian.collector")

_conn = None
_last_resource_attrs: dict = {}  # for /debug/attrs endpoint


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _conn
    _conn = connect()
    yield
    if _conn:
        _conn.close()


app = FastAPI(lifespan=_lifespan, docs_url=None, redoc_url=None)


@app.post("/v1/traces")
async def receive_traces(request: Request) -> Response:
    global _last_resource_attrs
    content_type = request.headers.get("content-type", "")
    body = await request.body()

    if "application/x-protobuf" in content_type:
        return Response(
            content='{"partialSuccess":{}}',
            media_type="application/json",
            status_code=200,
        )

    try:
        payload = json.loads(body)
        # Capture resource attributes for /debug/attrs
        for rs in payload.get("resourceSpans", []):
            for attr in rs.get("resource", {}).get("attributes", []):
                _last_resource_attrs[attr["key"]] = attr["value"]
        count = from_otlp_json(payload, _conn)
        log.debug("ingested %d spans", count)
    except Exception as exc:
        log.warning("parse error: %s", exc)
        return Response(content=str(exc), status_code=400)

    return Response(
        content='{"partialSuccess":{}}',
        media_type="application/json",
        status_code=200,
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/debug/attrs")
async def debug_attrs() -> dict:
    """Show the resource attributes from the last received OTEL payload."""
    return _last_resource_attrs


def run(port: int = 4318, host: str = "127.0.0.1") -> None:
    uvicorn.run(
        "meridian.collector:app",
        host=host,
        port=port,
        log_level="warning",
    )
