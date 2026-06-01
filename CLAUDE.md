## Baseline token cost
Every new session opens at ~103k cache tokens (system prompt + MCP schemas + CLAUDE.md) before any work begins. To reduce:
- Run `!meridian analyse --session <id>` on a 1-turn session to measure your true baseline
- Remove unused MCP servers from settings
- Move project-specific rules to per-project CLAUDE.md instead of global
