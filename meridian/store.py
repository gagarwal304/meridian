"""
SQLite storage layer — WAL mode allows concurrent reads while the collector writes.
Database lives at ~/.meridian/telemetry.db
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

_DEFAULT_DB = Path.home() / ".meridian" / "telemetry.db"


def db_path() -> Path:
    return _DEFAULT_DB


def connect(path: Path = _DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL mode: allows multiple readers + one writer across processes
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _init(conn)
    return conn


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS spans (
            trace_id          TEXT NOT NULL,
            span_id           TEXT NOT NULL,
            start_time        TEXT NOT NULL,
            span_type         TEXT NOT NULL,
            tool_name         TEXT,
            duration_ms       REAL,
            input_tokens      INTEGER,
            output_tokens     INTEGER,
            cache_read_tokens INTEGER,
            full_command      TEXT,
            service_name      TEXT DEFAULT 'claude-code',
            PRIMARY KEY (span_id)
        );
        CREATE INDEX IF NOT EXISTS idx_spans_trace
            ON spans (trace_id, start_time);
        CREATE INDEX IF NOT EXISTS idx_spans_time
            ON spans (start_time);
    """)
    conn.commit()


def upsert_spans(conn: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    inserted = 0
    for row in rows:
        try:
            start = row["start_time"]
            start_str = start.isoformat() if isinstance(start, datetime) else str(start)
            conn.execute("""
                INSERT OR IGNORE INTO spans
                    (trace_id, span_id, start_time, span_type, tool_name,
                     duration_ms, input_tokens, output_tokens, cache_read_tokens,
                     full_command, service_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                row["trace_id"], row["span_id"], start_str,
                row["span_type"], row.get("tool_name"),
                row.get("duration_ms"), row.get("input_tokens"),
                row.get("output_tokens"), row.get("cache_read_tokens"),
                row.get("full_command"), row.get("service_name", "claude-code"),
            ])
            inserted += 1
        except Exception:
            pass
    conn.commit()
    return inserted


def current_session_ids(conn: sqlite3.Connection, minutes: int = 30) -> list[str]:
    """Return trace_ids that have had spans in the last N minutes (fallback)."""
    cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat()
    rows = conn.execute(
        "SELECT DISTINCT trace_id FROM spans WHERE start_time >= ? ORDER BY start_time DESC",
        [cutoff],
    ).fetchall()
    return [r[0] for r in rows]


def resolve_window_session(
    conn: sqlite3.Connection,
    claude_session_id: str | None = None,
) -> list[str]:
    """
    Return the trace_id for the current Claude Code window.

    Always re-anchors to the most recent trace in the DB, so a new
    conversation within the same window is picked up automatically.
    Writes ~/.meridian/windows/<CLAUDE_CODE_SESSION_ID> for diagnostics.
    """
    import os
    import json as _json

    windows_dir = Path.home() / ".meridian" / "windows"
    windows_dir.mkdir(parents=True, exist_ok=True)

    sid = claude_session_id or os.environ.get("CLAUDE_CODE_SESSION_ID", "")

    # Always use the most recent trace_id — re-anchors on every call
    row = conn.execute(
        "SELECT trace_id FROM spans ORDER BY start_time DESC LIMIT 1"
    ).fetchone()
    if not row:
        return []

    trace_id = row[0]

    if sid:
        (windows_dir / sid).write_text(
            _json.dumps({"trace_id": trace_id, "session_id": sid})
        )

    return [trace_id]


def session_summary(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT
            trace_id,
            MIN(start_time)                                                 AS started_at,
            SUM(CASE WHEN span_type = 'tool' THEN 1 ELSE 0 END)           AS tool_calls,
            SUM(CASE WHEN span_type = 'llm_request' THEN 1 ELSE 0 END)    AS llm_turns,
            COALESCE(SUM(input_tokens), 0)
              + COALESCE(SUM(output_tokens), 0)                            AS io_tokens,
            COALESCE(SUM(cache_read_tokens), 0)                            AS total_cache_tokens,
            COALESCE(MAX(cache_read_tokens), 0)                            AS peak_cache_tokens,
            ROUND(SUM(duration_ms) / 1000.0, 1)                           AS total_duration_s,
            GROUP_CONCAT(DISTINCT tool_name)                               AS tools_used
        FROM spans
        WHERE start_time >= ?
        GROUP BY trace_id
        ORDER BY started_at DESC
    """, [cutoff]).fetchall()
    return [dict(r) for r in rows]


def session_spans(conn: sqlite3.Connection, trace_id: str) -> list[dict]:
    rows = conn.execute("""
        SELECT trace_id, span_id, start_time, span_type, tool_name,
               duration_ms, input_tokens, output_tokens, cache_read_tokens, full_command
        FROM spans
        WHERE trace_id = ?
        ORDER BY start_time ASC
    """, [trace_id]).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        if d.get("start_time"):
            try:
                d["start_time"] = datetime.fromisoformat(d["start_time"])
            except Exception:
                pass
        result.append(d)
    return result


def span_counts(conn: sqlite3.Connection) -> dict:
    r = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT trace_id) FROM spans"
    ).fetchone()
    return {"total_spans": r[0], "total_sessions": r[1]}
