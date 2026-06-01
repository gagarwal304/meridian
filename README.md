# Meridian

> Continuous telemetry-driven optimisation for Claude Code.

Meridian ingests the OpenTelemetry spans that Claude Code already emits, detects
the patterns that waste tokens — retry spirals, expensive Agent spawns, context
bloat — and generates the `CLAUDE.md` additions that fix them.

---

## Install

```bash
pipx install meridian-cc
meridian setup
source ~/.zshrc
```

> `pipx` installs CLI tools into isolated environments and puts them on your
> `$PATH` globally. If you don't have it: `brew install pipx && pipx ensurepath`.
>
> **For development** (to edit the source and see changes immediately):
> ```bash
> git clone <repo>
> cd MERIDIAN
> pipx install --editable .
> ```

That's it. The collector starts automatically on login. Open Claude Code and
type `/meridian` to see your analysis.

---

## How it works

```
Claude Code session
      │
      │  OTEL spans (tool_name, tokens, duration, full_command)
      ▼
Local collector (port 4318)
      │
      ▼
~/.meridian/telemetry.db  (SQLite, WAL mode)
      │
      ├── meridian report    → session table / waterfall
      ├── meridian analyse   → pattern findings
      └── meridian generate  → CLAUDE.md patch
```

`meridian setup` writes the required env vars to `~/.zshrc`, installs a
launchd agent so the collector starts on login, and registers `/meridian`
as a global Claude Code slash command.

---

## Commands

| Command | What it does |
|---|---|
| `meridian setup` | One-command install (env vars, slash command, background service) |
| `meridian report` | Session summary table — tokens, tools, wall time |
| `meridian report --session <id>` | Per-step waterfall for one session |
| `meridian analyse` | Detect retry spirals, Agent spawns, context bloat |
| `meridian generate` | Print CLAUDE.md additions based on findings |
| `meridian generate --apply` | Append additions directly to `./CLAUDE.md` |
| `meridian ingest <file>` | Import a Grafana CSV or OTLP JSON export |
| `meridian config` | Show all thresholds |
| `meridian config <key> <value>` | Change a threshold |
| `meridian status` | DB location and span counts |
| `meridian uninstall` | Stop the background collector service |
| `meridian purge` | Remove all data, config, env vars, and service |

All commands work inside a Claude Code session via the Bash tool.

---

## Configuration

Thresholds are stored in `~/.meridian/config.json`. Defaults apply automatically
if the file doesn't exist — no config required to get started.

```bash
meridian config                                    # show all settings
meridian config retry_spiral.min_repeats 2         # change a value
meridian config context_bloat.absolute_threshold 60000
meridian config --reset retry_spiral.min_repeats   # restore one default
meridian config --reset                            # restore all defaults
```

| Key | Default | What it controls |
|---|---|---|
| `retry_spiral.min_repeats` | `3` | Bash command repeats before flagging a spiral |
| `agent_spawn.threshold_ms` | `30000` | Agent call duration (ms) to flag as expensive |
| `context_bloat.growth_threshold` | `2.0` | cache_read_tokens growth ratio to flag |
| `context_bloat.absolute_threshold` | `80000` | Cache level that always flags bloat |
| `context_bloat.min_growth` | `10000` | Min token growth required (suppresses noise) |
| `webfetch_chain.min_length` | `3` | Consecutive WebFetch calls to flag as a chain |
| `tool_search.min_calls` | `2` | ToolSearch calls per session to flag overhead |
| `analyse.default_days` | `30` | Default window for `meridian analyse` |
| `report.default_days` | `7` | Default window for `meridian report` |

---

## Detected patterns

**Retry spiral** — the same Bash command runs 3+ times in one session.
Classic cause: Claude probing the environment (venv, PYTHONPATH, missing deps)
instead of having that context upfront.

**Agent spawn cost** — an Agent subagent runs for more than 30 seconds.
Agent spawns inherit the full parent context window and pay model startup cost.

**Context bloat** — `cache_read_tokens` grows more than 2× or crosses 80k.
Every subsequent LLM turn pays the full accumulated cache cost.

**WebFetch chain** — 3+ consecutive WebFetch calls. Each fetch triggers a
separate LLM turn; batching them costs one.

**ToolSearch overhead** — repeated deferred tool schema lookups. Indicates
MCP servers are loaded but not used in most sessions.

---

## The /meridian slash command

After setup, typing `/meridian` in any Claude Code session:

1. Runs `meridian analyse` via the Bash tool
2. Summarises HIGH severity issues with specific `CLAUDE.md` lines to add
3. Groups MEDIUM issues by pattern with one combined recommendation
4. Keeps the response under 200 words

---

## Testing a clean installation

```bash
# Wipe everything
meridian purge --yes

# Fresh install
meridian setup
source ~/.zshrc

# Inject test data (optional — collector handles live sessions automatically)
meridian ingest ~/Downloads/your-grafana-export.csv

# Run the full pipeline
meridian report
meridian analyse
meridian generate
```

See [TESTING.md](TESTING.md) for the full test guide including expected outputs
and edge cases.

---

## Data & privacy

All telemetry stays local. The collector runs on `127.0.0.1` and writes only to
`~/.meridian/telemetry.db`. Nothing is sent anywhere.

---

## Roadmap

| Phase | Status |
|---|---|
| Phase 0 — OTEL ingestion, session viewer, CLI | ✅ Done |
| Phase 1 — Pattern detection, CLAUDE.md generator | ✅ Done |
| Phase 2 — Team aggregation, cross-engineer patterns | Planned |
| Phase 3 — Auto-updating CLAUDE.md, community benchmarks | Planned |
