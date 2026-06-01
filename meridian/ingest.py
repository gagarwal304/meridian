"""
Telemetry ingestion from two sources:
  1. Grafana Tempo CSV export  (meridian ingest file.csv)
  2. OTLP/HTTP JSON payload    (live collector or meridian ingest file.json)
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import sqlite3

from dateutil import parser as dateparser

from .store import connect, upsert_spans


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _float(val: str) -> float | None:
    try:
        return float(val) if val and val.strip() else None
    except (ValueError, TypeError):
        return None


def _int(val: str) -> int | None:
    try:
        return int(val) if val and val.strip() else None
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Grafana CSV
# --------------------------------------------------------------------------- #

def from_grafana_csv(
    path: Path,
    conn: sqlite3.Connection | None = None,
) -> int:
    """
    Parse a Grafana Tempo CSV export.

    Expected columns (in any order):
        traceIdHidden, Span ID, Start time, span.type,
        tool_name, duration_ms, input_tokens, output_tokens,
        cache_read_tokens, full_command, service.name
    """
    if conn is None:
        conn = connect()

    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            span_type = raw.get("span.type", "").strip()
            if span_type not in ("tool", "llm_request"):
                continue

            try:
                start_time = dateparser.parse(raw["Start time"])
            except Exception:
                continue

            rows.append({
                "trace_id":          raw.get("traceIdHidden", "").strip(),
                "span_id":           raw.get("Span ID", "").strip(),
                "start_time":        start_time,
                "span_type":         span_type,
                "tool_name":         raw.get("tool_name", "").strip() or None,
                "duration_ms":       _float(raw.get("duration_ms", "")),
                "input_tokens":      _int(raw.get("input_tokens", "")),
                "output_tokens":     _int(raw.get("output_tokens", "")),
                "cache_read_tokens": _int(raw.get("cache_read_tokens", "")),
                "full_command":      raw.get("full_command", "").strip() or None,
                "service_name":      raw.get("service.name", "claude-code").strip(),
            })

    return upsert_spans(conn, rows)


# --------------------------------------------------------------------------- #
# OTLP/HTTP JSON
# --------------------------------------------------------------------------- #

def from_otlp_json(
    payload: dict,
    conn: sqlite3.Connection | None = None,
) -> int:
    """
    Parse an OTLP/HTTP JSON payload as sent by Claude Code.

    Format: { "resourceSpans": [ { "resource": {...}, "scopeSpans": [...] } ] }
    """
    if conn is None:
        conn = connect()

    from datetime import datetime

    rows: list[dict] = []

    for resource_span in payload.get("resourceSpans", []):
        service_name = "claude-code"
        for attr in resource_span.get("resource", {}).get("attributes", []):
            if attr["key"] == "service.name":
                service_name = attr["value"].get("stringValue", "claude-code")

        for scope_span in resource_span.get("scopeSpans", []):
            for span in scope_span.get("spans", []):
                attrs = {a["key"]: a["value"] for a in span.get("attributes", [])}

                def s(key: str) -> str | None:
                    v = attrs.get(key, {})
                    return v.get("stringValue") or None

                def n(key: str) -> float | None:
                    v = attrs.get(key, {})
                    if "doubleValue" in v:
                        return float(v["doubleValue"])
                    if "intValue" in v:
                        return float(v["intValue"])
                    return None

                span_type = s("span.type")
                if span_type not in ("tool", "llm_request"):
                    continue

                start_ns = int(span.get("startTimeUnixNano", 0))
                start_time = datetime.fromtimestamp(start_ns / 1e9)

                rows.append({
                    "trace_id":          span.get("traceId", ""),
                    "span_id":           span.get("spanId", ""),
                    "start_time":        start_time,
                    "span_type":         span_type,
                    "tool_name":         s("tool_name"),
                    "duration_ms":       n("duration_ms"),
                    "input_tokens":      int(n("input_tokens") or 0) or None,
                    "output_tokens":     int(n("output_tokens") or 0) or None,
                    "cache_read_tokens": int(n("cache_read_tokens") or 0) or None,
                    "full_command":      s("full_command"),
                    "service_name":      service_name,
                })

    return upsert_spans(conn, rows)


def from_file(path: Path, conn: duckdb.DuckDBPyConnection | None = None) -> int:
    """Auto-detect format from file extension and ingest."""
    if path.suffix.lower() == ".csv":
        return from_grafana_csv(path, conn)
    payload = json.loads(path.read_text())
    return from_otlp_json(payload, conn)
