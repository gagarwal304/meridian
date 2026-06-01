"""
CLAUDE.md patch generator.

Reads the list of Findings and emits concrete CLAUDE.md additions.
Only adds sections that are not already present in the target file.
"""
from __future__ import annotations

from pathlib import Path

from .patterns import Finding


# --------------------------------------------------------------------------- #
# Template library
# --------------------------------------------------------------------------- #

_TEMPLATES: dict[str, str] = {
    "venv": """\
## Python Environment
Always activate the virtual environment before running Python commands:
```bash
source .venv/bin/activate
```
This prevents import errors and PYTHONPATH probe loops.""",

    "node_build": """\
## Node / npm Commands
Always install dependencies and specify the working directory explicitly:
```bash
cd <project-dir> && npm install && npm run build
```""",

    "file_probe": """\
## Project Structure
Paste a directory snapshot here so Claude doesn't probe it at runtime:
```
# Generated with: tree -L 3 -I 'node_modules|.venv|__pycache__|.git'
<paste output here>
```
Repeated `find` calls to discover file layout are a common token drain.""",

    "agent_exploration": """\
## Subagent Usage
Prefer targeted `grep`/`find`/`Read` calls over spawning a general Agent for
codebase exploration. When an Agent is necessary, use `subagent_type=Explore` —
it is read-only, faster, and does not carry write permissions.""",

    "context_compact": """\
## Session Length
Run `/compact` when a session exceeds 80k cache tokens, or start a fresh session
for unrelated tasks. Long sessions compound token cost on every subsequent turn.""",

    "webfetch_batch": """\
## Web Research
List all URLs you need at the start of a research task so they can be fetched
together. Avoid sequential fetch → wait → fetch → wait patterns.""",

    "tool_audit": """\
## MCP Configuration
Review configured MCP servers and remove any that are not used in the majority
of sessions. Each unused server's schema is loaded into every context window.""",
}


# --------------------------------------------------------------------------- #
# Pattern → template mapping
# --------------------------------------------------------------------------- #

def _select_templates(findings: list[Finding]) -> list[tuple[str, str]]:
    """Return list of (template_key, source_description) for each applicable template."""
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    patterns = {f.pattern for f in findings}

    def add(key: str, source: str) -> None:
        if key not in seen:
            seen.add(key)
            results.append((key, source))

    if "retry_spiral" in patterns:
        for f in findings:
            if f.pattern != "retry_spiral":
                continue
            evidence = " ".join(f.evidence).lower()
            title = f.title
            if "python" in evidence or "venv" in evidence:
                add("venv", title)
            elif "npm " in evidence or "npm\n" in evidence or " node " in evidence:
                # match "npm " or "node " as commands, not "node_modules"
                add("node_build", title)
            elif "find " in evidence or "\nls " in evidence or "tree " in evidence:
                add("file_probe", title)
            # generic retry with no specific match → still note it
            # but no template to add

    if "agent_spawn_cost" in patterns:
        f = next(f for f in findings if f.pattern == "agent_spawn_cost")
        add("agent_exploration", f.title)

    if "context_bloat" in patterns:
        f = next(f for f in findings if f.pattern == "context_bloat")
        add("context_compact", f.title)

    if "webfetch_chain" in patterns:
        f = next(f for f in findings if f.pattern == "webfetch_chain")
        add("webfetch_batch", f.title)

    if "tool_search_overhead" in patterns:
        add("tool_audit", "ToolSearch overhead")

    return results


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def generate_patch(
    findings: list[Finding],
    current_claude_md: Path | None = None,
) -> str:
    """
    Return a string of CLAUDE.md content derived from findings.
    Sections already present in current_claude_md are skipped.
    Returns an empty string if there is nothing to add.
    """
    template_pairs = _select_templates(findings)
    if not template_pairs:
        return ""

    existing = current_claude_md.read_text() if current_claude_md and current_claude_md.exists() else ""

    sections: list[str] = []
    for key, source in template_pairs:
        body = _TEMPLATES[key]
        heading = body.strip().splitlines()[0]
        if heading in existing:
            continue
        # Prepend a comment linking back to the finding that triggered this
        annotated = f"<!-- triggered by: {source} -->\n{body}"
        sections.append(annotated)

    if not sections:
        return ""

    divider = "\n\n---\n\n"
    return "<!-- meridian-generated: review before committing -->\n\n" + divider.join(sections) + "\n"


def write_patch(
    findings: list[Finding],
    output_path: Path,
    current_claude_md: Path | None = None,
) -> bool:
    """Write patch to output_path. Returns True if anything was written."""
    patch = generate_patch(findings, current_claude_md)
    if not patch:
        return False
    output_path.write_text(patch, encoding="utf-8")
    return True
