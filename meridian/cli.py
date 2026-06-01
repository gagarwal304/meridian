"""
Meridian CLI — Phase 0 + Phase 1.

Commands:
  setup                    One-command install: env vars, slash command, background service
  ingest   <file>          Import a Grafana CSV or OTLP JSON file
  collect                  Start a local OTEL/HTTP collector (port 4318)
  report                   Session summary table
  report --session <id>    Per-step waterfall for one session
  analyse                  Detect inefficiency patterns across all sessions
  analyse --session <id>   Analyse a single session
  generate                 Output a CLAUDE.md patch
  generate --apply         Append patch directly to ./CLAUDE.md
  status                   Show DB location and span counts
  uninstall                Remove the background collector service
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from . import __version__
from .display import console, err_console, print_findings, print_session_table, print_session_waterfall
from .patterns import Finding as _Finding


def _print_findings_compact(findings: list[_Finding]) -> None:
    """One-line-per-finding format — fits in Claude Code's bash output window."""
    if not findings:
        from rich.panel import Panel
        console.print(Panel("[green]No inefficiency patterns detected.[/green]",
                            border_style="green"))
        return
    high = sum(1 for f in findings if f.severity == "high")
    med  = sum(1 for f in findings if f.severity == "medium")
    low  = sum(1 for f in findings if f.severity == "low")
    console.print(f"\n[bold]{len(findings)} finding{'s' if len(findings)!=1 else ''}[/bold]"
                  f"  [red]{high}h[/red] [yellow]{med}m[/yellow] [cyan]{low}l[/cyan]\n")
    _c = {"high": "red", "medium": "yellow", "low": "cyan"}
    for i, f in enumerate(findings, 1):
        col = _c.get(f.severity, "white")
        waste = f" ~{f.waste_ms/1000:.0f}s" if f.waste_ms else ""
        extra = f" +{f.wasted_tokens:,}tok" if f.wasted_tokens else ""
        console.print(f"[{col}]{i}.[/{col}] [bold]{f.title}[/bold]  "
                      f"[dim]({f.trace_id[:8]}{waste}{extra})[/dim]")
        console.print(f"   {f.detail}")
        if f.evidence:
            console.print(f"   [dim]↳ {f.evidence[0][:80]}[/dim]")
        console.print(f"   [green]Fix:[/green] {f.recommendation}\n")
from .generator import generate_patch
from .ingest import from_file, from_grafana_csv
from .patterns import run_all
from .store import connect, db_path, session_spans, session_summary, span_counts, resolve_window_session
from .setup import (
    install_env_vars, install_slash_command,
    install_launchd_service, uninstall_launchd_service,
    remove_zshrc_block, remove_slash_command, purge_data,
)
from . import config as _config


# --------------------------------------------------------------------------- #
# Root
# --------------------------------------------------------------------------- #

@click.group()
@click.version_option(__version__, prog_name="meridian")
def main() -> None:
    """Meridian — telemetry-driven optimisation for Claude Code."""


# --------------------------------------------------------------------------- #
# setup
# --------------------------------------------------------------------------- #

@main.command()
@click.option("--no-service", is_flag=True, default=False,
              help="Skip installing the background collector service.")
def setup(no_service: bool) -> None:
    """One-command install: configure shell, install /meridian slash command,
    and start the background OTEL collector.

    \b
    After running setup:
      1. Run:  source ~/.zshrc   (or open a new terminal)
      2. Open any Claude Code session
      3. Type: /meridian
    """
    console.print(f"\n[bold]Meridian setup[/bold] v{__version__}\n")

    # 1. Env vars ------------------------------------------------------------ #
    written, rc = install_env_vars()
    if written:
        console.print(f"[green]✓[/green] OTEL env vars written to [dim]{rc}[/dim]")
    else:
        console.print(f"[dim]·[/dim] Env vars already in [dim]{rc}[/dim] — skipped")

    # 2. Slash commands ------------------------------------------------------ #
    written, cmd_dir = install_slash_command()
    if written:
        console.print(f"[green]✓[/green] {written} slash command{'s' if written != 1 else ''} installed → [dim]{cmd_dir}[/dim]")
        console.print("  [dim]/meridian[/dim]        analyse your sessions")
        console.print("  [dim]/meridian-report[/dim] session overview table")
        console.print("  [dim]/meridian-fix[/dim]    generate + apply CLAUDE.md patch")
    else:
        console.print(f"[dim]·[/dim] Slash commands already exist → [dim]{cmd_dir}[/dim]")

    # 3. Background service -------------------------------------------------- #
    if no_service:
        console.print("[dim]·[/dim] Background service skipped (--no-service)")
    else:
        status, plist_path = install_launchd_service()
        if status == "installed":
            console.print(f"[green]✓[/green] Collector service installed and started")
            console.print(f"  [dim]plist:  {plist_path}[/dim]")
            console.print(f"  [dim]log:    ~/.meridian/collector.log[/dim]")
        elif status == "already_running":
            console.print(f"[dim]·[/dim] Collector service already running")
        elif status == "unsupported":
            console.print(
                "[yellow]![/yellow] Auto-service not supported on this OS. "
                "Run [bold]meridian collect[/bold] manually."
            )
        else:
            console.print(f"[yellow]![/yellow] Service install issue: {status}")
            console.print(
                "  Run [bold]meridian collect[/bold] manually to start the collector."
            )

    # Summary ---------------------------------------------------------------- #
    console.print()
    if written:
        console.print("[bold]Next steps:[/bold]")
        console.print(f"  1. [cyan]source {rc}[/cyan]   (or open a new terminal)")
        console.print("  2. Start a Claude Code session")
        console.print("  3. Type [bold cyan]/meridian[/bold cyan]")
    else:
        console.print("[bold]You're already set up.[/bold]")
        console.print("  Start a Claude Code session and type [bold cyan]/meridian[/bold cyan]")
    console.print()


# --------------------------------------------------------------------------- #
# uninstall
# --------------------------------------------------------------------------- #

@main.command()
def uninstall() -> None:
    """Stop and remove the background collector service."""
    was_running = uninstall_launchd_service()
    if was_running:
        console.print("[green]✓[/green] Collector service stopped and removed")
    else:
        console.print("[dim]Collector service was not running[/dim]")
    console.print("[dim]Run [bold]meridian purge[/bold] to also remove data and shell config.[/dim]")


# --------------------------------------------------------------------------- #
# purge
# --------------------------------------------------------------------------- #

@main.command()
@click.option("--yes", is_flag=True, default=False,
              help="Skip confirmation prompt.")
def purge(yes: bool) -> None:
    """Remove ALL Meridian data and configuration (full reset).

    Removes:
      • Telemetry database, config, and log  (~/.meridian/)
      • Background collector service
      • Shell env vars from ~/.zshrc
      • /meridian slash command

    Use this to test a clean installation, or to fully uninstall Meridian.
    Run `meridian setup` again to re-install from scratch.
    """
    if not yes:
        click.confirm(
            "\n[bold red]This removes all Meridian data and config.[/bold red] Continue?",
            abort=True,
        )

    console.print()

    # 1. Service
    was_running = uninstall_launchd_service()
    if was_running:
        console.print("[green]✓[/green] Collector service stopped and removed")
    else:
        console.print("[dim]·[/dim] Collector service was not running")

    # 2. Data files
    removed = purge_data()
    if removed:
        for p in removed:
            console.print(f"[green]✓[/green] Deleted [dim]{p}[/dim]")
    else:
        console.print("[dim]·[/dim] No data files found")

    # 3. Shell RC block
    if remove_zshrc_block():
        console.print("[green]✓[/green] Removed OTEL block from ~/.zshrc")
    else:
        console.print("[dim]·[/dim] No OTEL block found in ~/.zshrc")

    # 4. Slash commands
    n = remove_slash_command()
    if n:
        console.print(f"[green]✓[/green] Removed {n} slash command{'s' if n != 1 else ''}")
    else:
        console.print("[dim]·[/dim] No slash commands found")

    console.print("\n[dim]All clean. To reinstall: pipx install --editable . && meridian setup[/dim]")

    # pipx uninstall is last — it removes rich, so no console.print after this
    import subprocess, shutil
    if shutil.which("pipx"):
        result = subprocess.run(
            ["pipx", "uninstall", "meridian-cc"],
            capture_output=True, text=True,
        )
        if "uninstalled" in result.stdout:
            click.echo("✓ Uninstalled meridian-cc from pipx")
        else:
            click.echo("· pipx: nothing to uninstall")


# --------------------------------------------------------------------------- #
# ingest
# --------------------------------------------------------------------------- #

@main.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
def ingest(file: Path) -> None:
    """Import telemetry from a Grafana CSV export or OTLP JSON file.

    \b
    Examples:
      meridian ingest ~/Downloads/Explore-data-2026-06-01.csv
      meridian ingest traces.json

    Note: if the background collector service is running, stop it first:
      meridian uninstall   (then re-run `meridian setup` afterwards)
    """
    conn = connect()
    n = from_file(file, conn)
    conn.close()
    console.print(f"[green]✓ Imported {n} spans[/green] from [dim]{file.name}[/dim]")


# --------------------------------------------------------------------------- #
# collect
# --------------------------------------------------------------------------- #

@main.command()
@click.option("--port", default=4318, show_default=True, help="OTLP HTTP port.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
def collect(port: int, host: str) -> None:
    """Start a local OTEL collector that receives spans from Claude Code.

    \b
    Configure Claude Code (add to ~/.zshrc or ~/.bashrc):

      export CLAUDE_CODE_ENABLE_TELEMETRY=1
      export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
      export OTEL_EXPORTER_OTLP_PROTOCOL=http/json

    Then restart your shell and run Claude Code normally.
    Spans are written to the local DuckDB store automatically.
    """
    console.print(f"[bold]Meridian collector[/bold] → [cyan]http://{host}:{port}[/cyan]")
    console.print(f"[dim]DB: {db_path()}[/dim]")
    console.print("\nConfigure Claude Code:")
    console.print(f"  [dim]export CLAUDE_CODE_ENABLE_TELEMETRY=1[/dim]")
    console.print(f"  [dim]export OTEL_EXPORTER_OTLP_ENDPOINT=http://{host}:{port}[/dim]")
    console.print(f"  [dim]export OTEL_EXPORTER_OTLP_PROTOCOL=http/json[/dim]")
    console.print("\nPress [bold]Ctrl+C[/bold] to stop.\n")

    from .collector import run
    run(port=port, host=host)


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #

@main.command()
@click.option("--days", default=7, show_default=True, help="Days of history to show.")
@click.option("--current", is_flag=True, default=False,
              help="Show only sessions active in the last 30 minutes (this window).")
@click.option(
    "--session", "trace_prefix", default=None,
    help="Trace ID prefix — shows per-step waterfall for that session.",
)
def report(days: int, current: bool, trace_prefix: str | None) -> None:
    """Show session summary table or per-step waterfall.

    \b
    Examples:
      meridian report
      meridian report --current        # this terminal's active session only
      meridian report --days 30
      meridian report --session 8643ce7a
    """
    conn = connect()

    if trace_prefix:
        all_sess = session_summary(conn, days=365)
        matches  = [s for s in all_sess if s["trace_id"].startswith(trace_prefix)]
        if not matches:
            err_console.print(f"[red]No session matching prefix '{trace_prefix}'[/red]")
            conn.close()
            sys.exit(1)
        spans = session_spans(conn, matches[0]["trace_id"])
        print_session_waterfall(spans, matches[0]["trace_id"])
    elif current:
        ids = resolve_window_session(conn)
        if not ids:
            console.print("[dim]No active sessions in the last 30 minutes.[/dim]")
        else:
            all_sess = session_summary(conn, days=1)
            sessions = [s for s in all_sess if s["trace_id"] in ids]
            print_session_table(sessions, days=0)
    else:
        sessions = session_summary(conn, days=days)
        print_session_table(sessions, days)

    conn.close()


# --------------------------------------------------------------------------- #
# analyse
# --------------------------------------------------------------------------- #

@main.command()
@click.option("--days", default=30, show_default=True, help="Days of history to analyse.")
@click.option("--current", is_flag=True, default=False,
              help="Analyse only sessions active in the last 30 minutes (this window).")
@click.option("--compact", is_flag=True, default=False,
              help="One line per finding (used by /meridian).")
@click.option(
    "--session", "trace_prefix", default=None,
    help="Restrict analysis to one session (trace ID prefix).",
)
def analyse(days: int, current: bool, compact: bool, trace_prefix: str | None) -> None:
    """Detect inefficiency patterns: retry spirals, expensive agents, context bloat.

    \b
    Examples:
      meridian analyse
      meridian analyse --current       # this terminal's active session only
      meridian analyse --days 7
      meridian analyse --session 8643ce7a
    """
    conn = connect()

    if trace_prefix:
        all_sess = session_summary(conn, days=365)
        matches  = [s for s in all_sess if s["trace_id"].startswith(trace_prefix)]
        if not matches:
            err_console.print(f"[red]No session matching prefix '{trace_prefix}'[/red]")
            conn.close()
            sys.exit(1)
        full_id       = matches[0]["trace_id"]
        sessions_data = {full_id: session_spans(conn, full_id)}
    elif current:
        ids = resolve_window_session(conn)
        if not ids:
            console.print("[dim]No active sessions in the last 30 minutes.[/dim]")
            conn.close()
            return
        sessions_data = {tid: session_spans(conn, tid) for tid in ids}
    else:
        all_sess      = session_summary(conn, days=days)
        sessions_data = {s["trace_id"]: session_spans(conn, s["trace_id"]) for s in all_sess}

    findings = run_all(sessions_data)

    if compact:
        _print_findings_compact(findings)
    else:
        print_findings(findings)
    conn.close()


# --------------------------------------------------------------------------- #
# generate
# --------------------------------------------------------------------------- #

@main.command()
@click.option("--days", default=30, show_default=True)
@click.option("--apply", is_flag=True, default=False,
              help="Append patch directly to ./CLAUDE.md.")
def generate(days: int, apply: bool) -> None:
    """Show proposed CLAUDE.md additions based on detected patterns.

    Outputs only the patch text — pipe to Claude or apply directly.

    \b
    Examples:
      meridian generate               # print proposed additions
      meridian generate --apply       # append to ./CLAUDE.md
    """
    conn = connect()
    all_sess = session_summary(conn, days=days)
    sessions = {s["trace_id"]: session_spans(conn, s["trace_id"]) for s in all_sess}
    findings = run_all(sessions)
    conn.close()

    current_md = Path("CLAUDE.md") if Path("CLAUDE.md").exists() else None
    from .generator import generate_patch
    patch = generate_patch(findings, current_md)

    if not patch:
        console.print("[green]Nothing to add — CLAUDE.md already covers known patterns.[/green]")
        return

    if apply:
        target   = Path("CLAUDE.md")
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        with target.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n\n"):
                f.write("\n\n")
            f.write(patch)
        console.print(f"[green]✓ Appended to {target.resolve()}[/green]")
        return

    console.print("\n[bold]Proposed CLAUDE.md additions:[/bold]\n")
    console.print(patch)


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #

@main.command("config")
@click.argument("key", required=False)
@click.argument("value", required=False)
@click.option("--reset", "do_reset", is_flag=True, default=False,
              help="Reset KEY to its default (omit KEY to reset all).")
def config_cmd(key: str | None, value: str | None, do_reset: bool) -> None:
    """View or change pattern detection thresholds.

    \b
    Usage:
      meridian config                             # show all settings
      meridian config retry_spiral.min_repeats    # show one setting
      meridian config retry_spiral.min_repeats 5  # change a setting
      meridian config --reset retry_spiral.min_repeats
      meridian config --reset                     # reset everything
    """
    from rich.table import Table
    from rich import box

    if do_reset:
        _config.reset(key)
        if key:
            console.print(f"[green]✓[/green] Reset [bold]{key}[/bold] to default")
        else:
            console.print("[green]✓[/green] All settings reset to defaults")
        return

    if key and value:
        try:
            parsed = _config.set_value(key, value)
        except KeyError as e:
            err_console.print(f"[red]{e}[/red]")
            sys.exit(1)
        except ValueError as e:
            err_console.print(f"[red]{e}[/red]")
            sys.exit(1)
        console.print(f"[green]✓[/green] [bold]{key}[/bold] = {parsed!r}")
        return

    # Show table
    cfg      = _config.load()
    descs    = _config.descriptions()
    raw_user = {}
    if _config.config_path().exists():
        import json
        try:
            raw_user = json.loads(_config.config_path().read_text())
        except Exception:
            pass

    if key:
        if key not in _config.DEFAULTS:
            err_console.print(f"[red]Unknown key: {key!r}[/red]")
            sys.exit(1)
        console.print(f"[bold]{key}[/bold] = {cfg[key]!r}  [dim](default: {_config.DEFAULTS[key]!r})[/dim]")
        console.print(f"[dim]{descs.get(key, '')}[/dim]")
        return

    t = Table(box=box.SIMPLE_HEAD, title="Meridian config", padding=(0, 1))
    t.add_column("Key",         style="bold",  min_width=38)
    t.add_column("Value",       justify="right", width=10)
    t.add_column("Default",     justify="right", style="dim", width=10)
    t.add_column("Description", style="dim",   max_width=42)

    for k, default in _config.DEFAULTS.items():
        current = cfg[k]
        modified = k in raw_user
        val_text = f"[yellow]{current}[/yellow]" if modified else str(current)
        t.add_row(k, val_text, str(default), descs.get(k, ""))

    console.print(t)
    console.print(f"[dim]Config file: {_config.config_path()}[/dim]")
    console.print(f"[dim]Modified values shown in yellow.[/dim]")


# --------------------------------------------------------------------------- #
# scan  (report + analyse + generate in one shot)
# --------------------------------------------------------------------------- #

@main.command()
@click.option("--days", default=7, show_default=True, help="Days of history to include.")
def scan(days: int) -> None:
    """Full scan: session table + findings + proposed CLAUDE.md additions.

    Single command, single output block. This is what /meridian calls.

    \b
    Examples:
      meridian scan
      meridian scan --days 30
    """
    from .generator import generate_patch
    from rich.rule import Rule

    conn = connect()
    sessions = session_summary(conn, days=days)
    sessions_data = {s["trace_id"]: session_spans(conn, s["trace_id"]) for s in sessions}
    conn.close()

    findings = run_all(sessions_data)

    # ── Section 1: Session summary (one line) ───────────────────────────── #
    if sessions:
        peak_sess  = max(sessions, key=lambda s: s.get("peak_cache_tokens") or 0)
        heavy_sess = max(sessions, key=lambda s: s.get("total_cache_tokens") or 0)
        above_80k  = sum(1 for s in sessions if (s.get("peak_cache_tokens") or 0) >= 80_000)
        console.print(
            f"\n[bold]{len(sessions)} sessions[/bold]"
            f"  last {days}d · all windows · all projects"
            f"  [dim]|[/dim]  {above_80k}/{len(sessions)} above 80k peak cache"
            f"  [dim]|[/dim]  costliest: [dim]{heavy_sess['trace_id'][:12]}[/dim]"
            f" ({heavy_sess.get('total_cache_tokens', 0):,} cache reads,"
            f" {heavy_sess.get('total_duration_s', 0):.0f}s)"
            f"\n[dim]Full table: !meridian report[/dim]\n"
        )
    else:
        console.print("\n[dim]No sessions found.[/dim]\n")
        return

    # ── Section 2: Findings (compact) ───────────────────────────────────── #
    if not findings:
        console.print("[green]No inefficiency patterns detected.[/green]\n")
        return

    _print_findings_compact(findings)


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #

@main.command()
def status() -> None:
    """Show DB path and ingested span counts."""
    conn   = connect()
    counts = span_counts(conn)
    conn.close()

    console.print(f"[bold]Meridian[/bold] v{__version__}")
    console.print(f"  DB:       [dim]{db_path()}[/dim]")
    console.print(f"  Spans:    {counts['total_spans']:,}")
    console.print(f"  Sessions: {counts['total_sessions']:,}")
