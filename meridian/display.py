"""Rich terminal display helpers."""
from __future__ import annotations

from datetime import datetime

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .patterns import Finding

console = Console()
err_console = Console(stderr=True)


def _sev_color(sev: str) -> str:
    return {"high": "red", "medium": "yellow", "low": "cyan"}.get(sev, "white")


# --------------------------------------------------------------------------- #
# Session table (meridian report)
# --------------------------------------------------------------------------- #

def print_session_table(sessions: list[dict], days: int) -> None:
    if not sessions:
        console.print(f"[dim]No sessions found in the last {days} days.[/dim]")
        return

    t = Table(
        title=f"Sessions — last {days} day{'s' if days != 1 else ''}",
        box=box.SIMPLE_HEAD,
        show_footer=False,
        padding=(0, 1),
    )
    t.add_column("Session",        style="dim",     width=16)
    t.add_column("Started",                        width=16)
    t.add_column("Turns",          justify="right", width=6)
    t.add_column("Tools",          justify="right", width=6)
    t.add_column("I+O tokens",     justify="right", width=11)
    t.add_column("Cache read",     justify="right", width=13)
    t.add_column("Peak cache",     justify="right", width=11)
    t.add_column("Wall time",      justify="right", width=9)

    for s in sessions:
        started = s["started_at"]
        started_str = (
            started.strftime("%m-%d %H:%M")
            if isinstance(started, datetime)
            else str(started)[:16]
        )
        peak       = s["peak_cache_tokens"] or 0
        cache_total = s.get("total_cache_tokens") or 0
        peak_style  = "red" if peak > 120_000 else "yellow" if peak > 80_000 else ""
        peak_cell   = Text(f"{peak:,}" if peak else "-", style=peak_style)
        cache_cell  = Text(f"{cache_total:,}" if cache_total else "-",
                           style="yellow" if cache_total > 500_000 else "")

        t.add_row(
            s["trace_id"][:16],
            started_str,
            str(s["llm_turns"]),
            str(s["tool_calls"]),
            f"{s['io_tokens']:,}" if s.get("io_tokens") else "-",
            cache_cell,
            peak_cell,
            f"{s['total_duration_s']:.0f}s" if s["total_duration_s"] else "-",
        )

    console.print(t)


# --------------------------------------------------------------------------- #
# Session waterfall (meridian report --session)
# --------------------------------------------------------------------------- #

def print_session_waterfall(spans: list[dict], trace_id: str) -> None:
    if not spans:
        console.print("[dim]No spans found for this session.[/dim]")
        return

    t = Table(
        title=f"Waterfall — {trace_id[:20]}",
        box=box.SIMPLE,
        show_footer=False,
        padding=(0, 1),
    )
    t.add_column("#",           justify="right", style="dim", width=3)
    t.add_column("+time",                        width=7)
    t.add_column("type",                         width=12)
    t.add_column("detail",                       max_width=55)
    t.add_column("ms",          justify="right", width=8)
    t.add_column("cache tok",   justify="right", width=10)

    start_ref: datetime | None = None
    for i, s in enumerate(spans, 1):
        ts = s.get("start_time")
        if start_ref is None and isinstance(ts, datetime):
            start_ref = ts

        elapsed = ""
        if isinstance(ts, datetime) and isinstance(start_ref, datetime):
            elapsed = f"+{(ts - start_ref).total_seconds():.0f}s"

        if s["span_type"] == "llm_request":
            inp   = s.get("input_tokens") or 0
            out   = s.get("output_tokens") or 0
            cache = s.get("cache_read_tokens") or 0
            detail = f"in={inp} out={out} cache={cache:,}"
            type_cell = Text("llm_request", style="blue")
        else:
            tool = s.get("tool_name") or "?"
            cmd  = (s.get("full_command") or "").replace("\n", " ")[:48]
            detail = f"{tool}: {cmd}" if cmd else tool
            type_cell = Text("tool", style="green")

        dur   = s.get("duration_ms")
        cache = s.get("cache_read_tokens")

        cache_text = Text(
            f"{cache:,}" if cache is not None else "-",
            style="yellow" if (cache or 0) > 80_000 else "",
        )

        t.add_row(
            str(i),
            elapsed,
            type_cell,
            detail,
            f"{dur:.0f}" if dur is not None else "-",
            cache_text,
        )

    console.print(t)


# --------------------------------------------------------------------------- #
# Findings (meridian analyse)
# --------------------------------------------------------------------------- #

def print_findings(findings: list[Finding]) -> None:
    if not findings:
        console.print(Panel(
            "[green]No significant inefficiency patterns detected.[/green]\n"
            "Your recent sessions look clean.",
            title="meridian analyse",
            border_style="green",
        ))
        return

    high   = sum(1 for f in findings if f.severity == "high")
    medium = sum(1 for f in findings if f.severity == "medium")
    low    = sum(1 for f in findings if f.severity == "low")

    console.print(
        f"\n[bold]Found {len(findings)} issue{'s' if len(findings) != 1 else ''}[/bold]"
        f"  [red]{high} high[/red]  [yellow]{medium} medium[/yellow]  [cyan]{low} low[/cyan]\n"
    )

    for i, f in enumerate(findings, 1):
        col   = _sev_color(f.severity)
        badge = f"[{col}][{f.severity.upper()}][/{col}]"

        console.print(f"[bold]{i}.[/bold] {badge}  [bold]{f.title}[/bold]")
        console.print(f"   [dim]session:[/dim] {f.trace_id[:16]}")
        console.print(f"   {f.detail}")

        if f.waste_ms > 0:
            console.print(f"   [dim]estimated waste:[/dim] {f.waste_ms/1000:.1f}s")
        if f.wasted_tokens > 0:
            console.print(f"   [dim]extra tokens:[/dim] {f.wasted_tokens:,}")

        console.print(f"\n   [bold green]Recommendation:[/bold green] {f.recommendation}")

        if f.evidence:
            console.print("   [dim]Evidence:[/dim]")
            for e in f.evidence[:3]:
                console.print(f"     [dim]• {e[:100]}[/dim]")

        console.print()
