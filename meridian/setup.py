"""
meridian setup -- one-command install.

1. Writes OTEL env vars to ~/.zshrc / ~/.bashrc
2. Installs ~/.claude/commands/meridian.md (slash command)
3. Installs and loads a launchd agent (macOS) so the collector
   starts automatically on login
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent


# --------------------------------------------------------------------------- #
# Env vars written to the shell RC file
# --------------------------------------------------------------------------- #

_OTEL_BLOCK = dedent("""\
    # --- Meridian: Claude Code telemetry (added by `meridian setup`) ---
    export CLAUDE_CODE_ENABLE_TELEMETRY=1
    export CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1
    export OTEL_SERVICE_NAME="claude-code"
    export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
    export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
    export OTEL_TRACES_EXPORTER=otlp
    export OTEL_METRICS_EXPORTER=otlp
    export OTEL_LOGS_EXPORTER=otlp
    export OTEL_LOG_TOOL_DETAILS=1
    export OTEL_METRIC_EXPORT_INTERVAL=5000
    export OTEL_LOGS_EXPORT_INTERVAL=2000
    # --- end Meridian ---
""")

_MERIDIAN_MARKER = "# --- Meridian: Claude Code telemetry"


# --------------------------------------------------------------------------- #
# Slash commands written to ~/.claude/commands/
# --------------------------------------------------------------------------- #

_SLASH_COMMANDS: dict[str, str] = {
    "meridian.md": dedent("""\
        The user ran: /meridian $ARGUMENTS

        Parse $ARGUMENTS:
        - SUBCOMMAND = first word (empty if no arguments)
        - FLAGS = everything after the first word

        --- Route ---

        SUBCOMMAND is empty, "scan", "report", "analyse", "analyze", or "fix":

            Run ONE bash command: `meridian scan FLAGS`

            After it:
            - If no findings: say "Sessions look efficient."
            - If there are findings: immediately write targeted CLAUDE.md additions
              based on the ACTUAL evidence in the output. Use real file paths, real
              commands, real patterns. No preamble, no summary, no repetition.
              BAD:  "## Project Structure - document your layout"
              GOOD: "## Discount UI\\n`createDiscount.$id.jsx` uses `<SketchPicker>` from `react-color`."
              Then ask: "Apply to ./CLAUDE.md? (yes/no)"
              If yes: append using Edit or Write tool. Never apply without asking.

        SUBCOMMAND is "session":
            Run: `meridian report --session FLAGS`
            One sentence on what stands out. Nothing else unless asked.

        SUBCOMMAND is "report":
            Run: `meridian report FLAGS`

        SUBCOMMAND is "config":
            Run: `meridian config FLAGS`
            If FLAGS is empty, explain what each threshold controls after the table.

        SUBCOMMAND is "status":
            Run: `meridian status`

        SUBCOMMAND is "help":
            Print this (no bash):

            /meridian              - scan + CLAUDE.md fix offer
            /meridian --days 30    - same, wider window
            /meridian report       - full session table
            /meridian session <id> - step-by-step waterfall
            /meridian config       - show/change thresholds
            /meridian status       - DB stats

            Raw CLI: !meridian scan / !meridian report / !meridian analyse

        Anything else:
            Say: "Unknown subcommand. Type /meridian help."

        Always use the Bash tool. Never fabricate command output.
    """),
}


# --------------------------------------------------------------------------- #
# launchd plist (macOS only)
# --------------------------------------------------------------------------- #

_PLIST_LABEL = "com.meridian.collector"
_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{_PLIST_LABEL}.plist"

_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{meridian_bin}</string>
        <string>collect</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{bin_dir}:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _meridian_bin() -> str:
    found = shutil.which("meridian")
    if found:
        return found
    return str(Path(sys.executable).parent / "meridian")


def _detect_rc() -> Path:
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return Path.home() / ".zshrc"
    if "bash" in shell:
        rc = Path.home() / ".bash_profile"
        return rc if rc.exists() else Path.home() / ".bashrc"
    return Path.home() / ".zshrc"


def _already_in_rc(rc: Path) -> bool:
    return rc.exists() and _MERIDIAN_MARKER in rc.read_text()


def _service_running() -> bool:
    try:
        result = subprocess.run(
            ["launchctl", "list", _PLIST_LABEL],
            capture_output=True, text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


# --------------------------------------------------------------------------- #
# Public install steps
# --------------------------------------------------------------------------- #

def install_env_vars(rc: Path | None = None) -> tuple[bool, Path]:
    """Write OTEL env vars to the shell RC file. Returns (was_written, rc_path)."""
    rc = rc or _detect_rc()
    if _already_in_rc(rc):
        return False, rc
    with rc.open("a") as f:
        f.write(f"\n{_OTEL_BLOCK}")
    return True, rc


def install_slash_command() -> tuple[int, Path]:
    """Write all Meridian slash commands (always overwrites). Returns (count_written, commands_dir)."""
    cmd_dir = Path.home() / ".claude" / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in _SLASH_COMMANDS.items():
        (cmd_dir / filename).write_text(content, encoding="utf-8")
    return len(_SLASH_COMMANDS), cmd_dir


def install_launchd_service() -> tuple[str, Path]:
    if platform.system() != "Darwin":
        return "unsupported", _PLIST_PATH

    if _service_running():
        return "already_running", _PLIST_PATH

    bin_path = _meridian_bin()
    bin_dir  = str(Path(bin_path).parent)
    log_path = str(Path.home() / ".meridian" / "collector.log")
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    plist = _PLIST_TEMPLATE.format(
        label=_PLIST_LABEL,
        meridian_bin=bin_path,
        log=log_path,
        bin_dir=bin_dir,
    )

    _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PLIST_PATH.write_text(plist, encoding="utf-8")

    try:
        subprocess.run(
            ["launchctl", "load", "-w", str(_PLIST_PATH)],
            check=True,
            capture_output=True,
        )
        return "installed", _PLIST_PATH
    except subprocess.CalledProcessError as e:
        return f"load_failed: {e.stderr.decode().strip()}", _PLIST_PATH


def uninstall_launchd_service() -> bool:
    if platform.system() != "Darwin":
        return False
    was_running = _service_running()
    if was_running:
        subprocess.run(
            ["launchctl", "unload", "-w", str(_PLIST_PATH)],
            capture_output=True,
        )
    if _PLIST_PATH.exists():
        _PLIST_PATH.unlink()
    return was_running


def remove_zshrc_block(rc: Path | None = None) -> bool:
    rc = rc or _detect_rc()
    if not rc.exists() or not _already_in_rc(rc):
        return False
    import re
    text = rc.read_text(encoding="utf-8")
    cleaned = re.sub(
        r"\n?# --- Meridian: Claude Code telemetry.*?# --- end Meridian ---\n?",
        "",
        text,
        flags=re.DOTALL,
    )
    rc.write_text(cleaned, encoding="utf-8")
    return True


def remove_slash_command() -> int:
    cmd_dir = Path.home() / ".claude" / "commands"
    removed = 0
    for filename in _SLASH_COMMANDS:
        target = cmd_dir / filename
        if target.exists():
            target.unlink()
            removed += 1
    return removed


def purge_data() -> list[Path]:
    removed = []
    for name in ("telemetry.db", "config.json", "collector.log"):
        p = Path.home() / ".meridian" / name
        if p.exists():
            p.unlink()
            removed.append(p)
    return removed
