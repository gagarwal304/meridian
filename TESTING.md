# Meridian — Testing Guide

---

## The intended user flow

```
pip install meridian-cc
meridian setup
source ~/.zshrc          # or open a new terminal
# → use Claude Code normally
# → type /meridian inside any session to see analysis
```

That's it. No Grafana, no manual env var config, no CSV exports.

---

## Prerequisites

```bash
cd /path/to/MERIDIAN

# Install globally via pipx (puts meridian on PATH, no venv activation needed)
pipx install --editable .

meridian --version
# → meridian, version 0.1.0
```

---

## 1. Setup (one-command install)

```bash
meridian setup
```

**Expected output:**
```
Meridian setup v0.1.0

✓ OTEL env vars written to /Users/<you>/.zshrc
✓ Slash command installed → /Users/<you>/.claude/commands/meridian.md
✓ Collector service installed and started
  plist:  ~/Library/LaunchAgents/com.meridian.collector.plist
  log:    ~/.meridian/collector.log

Next steps:
  1. source ~/.zshrc   (or open a new terminal)
  2. Start a Claude Code session
  3. Type /meridian
```

**What to verify:**

```bash
# 1. Collector is running
launchctl list com.meridian.collector
# Should show a PID

# 2. Collector health endpoint responds
curl http://127.0.0.1:4318/health
# → {"status":"ok"}

# 3. Env vars were written
grep -A 12 "Meridian" ~/.zshrc

# 4. Slash command exists
cat ~/.claude/commands/meridian.md
```

**Idempotency** — running setup again should say "skipped / already running":
```bash
meridian setup
# → · Env vars already in ~/.zshrc — skipped
# → · Slash command already exists
# → · Collector service already running
```

---

## 2. Ingesting test data (CSV from Grafana)

The collector auto-ingests live sessions. To test with historical data right away:

```bash
# The collector stays running — SQLite WAL allows concurrent writes
meridian ingest ~/Downloads/Explore-data-2026-06-01.csv
# → ✓ Imported 206 spans

# Idempotent — safe to run again
meridian ingest ~/Downloads/Explore-data-2026-06-01.csv
# → ✓ Imported 0 spans  (no duplicates)
```

---

## 3. Status

```bash
meridian status
```

**Expected:**
```
Meridian v0.1.0
  DB:       /Users/<you>/.meridian/telemetry.db
  Spans:    206
  Sessions: 25
```

---

## 4. Report

```bash
meridian report             # last 7 days
meridian report --days 30   # last 30 days
```

**What to verify:**
- Sessions sorted newest-first
- Peak cache tokens > 80k in yellow, > 120k in red
- `--days 7` shows fewer sessions than `--days 30`

### Session waterfall

```bash
meridian report --session 8643ce7a
```

**What to verify:**
- Steps are time-ordered (`+0s`, `+8s`, ...)
- LLM rows show `in=`, `out=`, `cache=` counts
- Tool rows show name and truncated command
- The Agent span appears at ~+8s with duration 107903ms

---

## 5. Analyse

```bash
meridian analyse            # all sessions, last 30 days
meridian analyse --days 7
meridian analyse --session 8643ce7a
```

Against the sample CSV: **9 issues — 2 high, 7 medium**.

### Retry spiral (session `8643ce7a`)

```bash
meridian analyse --session 8643ce7a
```

**Expected findings:**
- `[HIGH]` Expensive Agent spawn: 108s
- `[MEDIUM]` Retry spiral: same command ran 4×
- `[MEDIUM]` Context bloat: 3.2×

**Verify the retry spiral:**
- Command shown: `python -c "from datasleuth.connectors.woocommerce..."`
- Repeat count: 4
- Waste: ~82s
- Recommendation mentions `source .venv/bin/activate`

### Context bloat — signal vs noise

The `context_bloat.min_growth = 10000` threshold suppresses false positives
from sessions that *started* above 80k but barely changed.

**Noisy (now suppressed):** session `5502b55d` — started at 91k, grew by only 28 tokens.
**Signal (still caught):** session `470f1321` — started at 11k, grew 10.6× to 121k.

```bash
meridian analyse --session 470f1321
# → [HIGH] Context bloat: 11,513 → 121,687 cache tokens (10.6×)

meridian analyse --session 5502b55d
# → No significant inefficiency patterns detected.
```

### WebFetch chain (session `bbbc5979`)

```bash
meridian analyse --session bbbc5979
# → [MEDIUM] WebFetch chain: 6 sequential fetches (120.5s)
```

---

## 6. Generate

```bash
meridian generate                   # print to stdout (safe)
meridian generate -o /tmp/patch.md  # write to file
meridian generate --apply           # append to ./CLAUDE.md
```

**Against the sample data, expected sections:**
- `## Python Environment` — venv retry spiral
- `## Subagent Usage` — Agent spawn cost
- `## Session Length` — context bloat
- `## Web Research` — WebFetch chain

**Idempotency:** run `--apply` twice:
```bash
meridian generate --apply
# → ✓ Appended N chars to ./CLAUDE.md

meridian generate --apply
# → Nothing to add — CLAUDE.md already covers known patterns.
```

---

## 7. Config — adjusting thresholds

```bash
meridian config                              # show all settings
meridian config retry_spiral.min_repeats    # show one
meridian config retry_spiral.min_repeats 2  # lower threshold
meridian config --reset retry_spiral.min_repeats  # restore default
meridian config --reset                     # restore all defaults
```

**Available keys:**

| Key | Default | Effect |
|---|---|---|
| `retry_spiral.min_repeats` | 3 | Repeats before flagging a spiral |
| `agent_spawn.threshold_ms` | 30000 | Agent duration (ms) to flag |
| `context_bloat.growth_threshold` | 2.0 | Cache growth ratio to flag |
| `context_bloat.absolute_threshold` | 80000 | Cache level that always flags |
| `context_bloat.min_growth` | 10000 | Min token growth to suppress noise |
| `webfetch_chain.min_length` | 3 | Consecutive fetches to flag |
| `tool_search.min_calls` | 2 | ToolSearch calls per session to flag |
| `analyse.default_days` | 30 | Default analysis window |
| `report.default_days` | 7 | Default report window |

**Test: make the analyser more sensitive**
```bash
meridian config retry_spiral.min_repeats 2
meridian analyse --session 8643ce7a
# → Should now flag any pair of repeated commands

meridian config --reset retry_spiral.min_repeats
```

---

## 8. /meridian slash command (inside Claude Code)

After `meridian setup` and `source ~/.zshrc`:

1. Open any Claude Code session
2. Type `/meridian`
3. Claude will run `meridian analyse` and summarise:
   - HIGH issues first, with specific CLAUDE.md line to add
   - MEDIUM issues grouped by pattern type
   - Under 200 words

**What to verify:**
- `/meridian` autocompletes (shows in the slash command menu)
- Claude actually runs `meridian analyse` via Bash tool
- The summary references real findings, not generic advice

---

## 9. Live OTEL end-to-end

1. `source ~/.zshrc` (to activate OTEL env vars)
2. Run a Claude Code session — ask it to search for something, run a Bash command
3. `meridian status` — span count should increase
4. `meridian report` — new session at the top of the table
5. `meridian analyse` — new session included in analysis

**Collector log** (should be silent unless errors):
```bash
tail -f ~/.meridian/collector.log
```

---

## 10. Uninstall

```bash
meridian uninstall
# → ✓ Collector service stopped and removed

# Verify it's gone
launchctl list com.meridian.collector
# → Could not find service ...

# Re-setup when ready
meridian setup
```

---

## Known limitations

| Limitation | Detail |
|---|---|
| OTLP protobuf not supported | Set `OTEL_EXPORTER_OTLP_PROTOCOL=http/json`. Protobuf payloads are acknowledged but not parsed. |
| `full_command` only on Bash | Other tool spans (Read, Edit, Write) don't include their arguments in the telemetry. |
| No test suite yet | Unit tests for patterns.py, ingest.py, store.py are pending. Smoke test above covers the happy path. |
