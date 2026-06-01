# Meridian

Collects Claude Code telemetry, detects inefficiency patterns, and generates
`CLAUDE.md` additions that fix them. Phases 0 and 1 complete.

---

## Install

```bash
git clone <repo> && cd MERIDIAN
pipx install --editable .
meridian setup
source ~/.zshrc
```

`meridian setup` does three things:
1. Writes OTEL env vars to `~/.zshrc`
2. Installs `/meridian` as a Claude Code slash command
3. Starts a background collector (launchd on macOS)

Open Claude Code and type `/meridian`.

---

## CLI commands

| Command | What it does |
|---|---|
| `meridian setup` | One-command install |
| `meridian scan` | Session summary + findings + CLAUDE.md patch in one shot |
| `meridian report` | Session table (last 7 days) |
| `meridian report --session <id>` | Per-step waterfall for one session |
| `meridian analyse` | Detect patterns across all sessions |
| `meridian generate` | Print proposed CLAUDE.md additions |
| `meridian generate --apply` | Append additions to `./CLAUDE.md` |
| `meridian ingest <file>` | Import Grafana CSV or OTLP JSON |
| `meridian config` | Show thresholds |
| `meridian config <key> <value>` | Change a threshold |
| `meridian status` | DB path and span counts |
| `meridian uninstall` | Stop background service |
| `meridian purge` | Full reset (data, config, service, shell vars) |

---

## /meridian slash command

Installed to `~/.claude/commands/meridian.md` by `meridian setup`.

| Invocation | What runs |
|---|---|
| `/meridian` | `meridian scan` → findings → CLAUDE.md fix offer |
| `/meridian --days 30` | Same, 30-day window |
| `/meridian report` | `meridian report` |
| `/meridian session <id>` | `meridian report --session <id>` |
| `/meridian config` | `meridian config` + threshold explanations |
| `/meridian status` | `meridian status` |
| `/meridian help` | Print this table |

When findings exist, Claude writes targeted CLAUDE.md additions based on actual
evidence (real file paths, real commands) and asks before applying.

---

## Detected patterns

| Pattern | Trigger | Fix |
|---|---|---|
| Retry spiral | Same Bash command ≥3× in a session | Add correct invocation to CLAUDE.md |
| Probe spiral | ≥4 different searches on same file | Document what lives in that file |
| Agent spawn cost | Agent call > 30s | Prefer grep/Read over open-ended Agent |
| Context bloat | cache_read_tokens grows 2× or crosses 80k | `/compact` or fresh session |
| WebFetch chain | 3+ consecutive fetches | List all URLs upfront |
| ToolSearch overhead | 2+ schema lookups in a session | Remove unused MCP servers |
| Heavy baseline | ≥2 sessions start above 80k | Trim global CLAUDE.md, remove unused MCPs |

---

## Roadmap

| Phase | Status |
|---|---|
| Phase 0 — OTEL ingestion, session viewer, CLI | ✅ Done |
| Phase 1 — Pattern detection, CLAUDE.md generator | ✅ Done |
| Phase 2 — Team aggregation, cross-engineer patterns | Planned |
| Phase 3 — Auto-updating CLAUDE.md, community benchmarks | Planned |
