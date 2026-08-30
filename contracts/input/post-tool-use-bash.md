# Contract: `PostToolUse` (Bash) hook event — input

## MCP binding

None (adr/004). Claude Code hook event, JSON on stdin.

- **Event:** `PostToolUse`
- **Matcher:** `Bash`
- **Script:** `hooks/scripts/watch_bd_commands.py`
- **Timeout:** 30s (`hooks/hooks.json`)
- **Entrypoint:** `main()` under `hook_io.guard`

This is the attribution engine's only trigger. It writes
`contracts/output/bd-metadata-write.md` and emits nothing on stdout.

## Input schema

```json
{
  "type": "object",
  "properties": {
    "session_id": { "type": "string", "description": "Attribution key and ccusage --id argument." },
    "hook_event_name": { "const": "PostToolUse" },
    "tool_name": { "type": "string", "description": "Anything other than \"Bash\" returns 0 immediately." },
    "tool_input": {
      "type": "object",
      "properties": {
        "command": { "type": "string", "description": "The shell command that ran. Prefiltered on the substring \"bd\" before anything else, so the ~95% of Bash calls that are not bd cost a string scan rather than a subprocess. Then tokenised with shlex(punctuation_chars=True) — never regex-matched (adr/010)." }
      },
      "required": ["command"]
    },
    "tool_response": { "type": "object", "description": "Present in the payload but deliberately not read. Whether bd succeeded is verified by asking bd, not by parsing its stdout." },
    "cwd": { "type": "string", "description": "The workspace the bd invocation targeted." }
  },
  "required": ["tool_name", "tool_input"]
}
```

### Commands recognised as boundaries

Verified against bd 1.1.2. Both spellings of each boundary are watched because
agents genuinely use both, and watching only the dedicated subcommand loses those
tasks' cost silently:

| Command shape | Boundary |
|---|---|
| `bd update <id> --claim` | claim |
| `bd update <id> --status in_progress` | claim |
| `bd close <id>` (accepts several ids) | close |
| `bd done <id>` | close |
| `bd update <id> --status closed` | close |

Chained and prefixed forms are handled: `&&`, `\|\|`, `;`, `\|` and `&` split into
segments; leading `VAR=value` assignments are stripped; `/opt/homebrew/bin/bd`
matches on basename while `bdiff` and `sbd` do not. `bd list`/`show`/`ready`/
`prime` and the other read-only subcommands are skipped explicitly, so an
unfamiliar subcommand falls through to the harmless no-match path rather than
being acted on.

`echo "bd close x"` is **not** a close: the quoted string survives tokenising as
a single token. A regex over the raw command string false-positives there and
would mis-attribute an entire task.

**Not recognised** (documented limits, covered by the gate's lazy snapshot and the
Stop/SessionEnd repair passes): variable expansion, `bash -c "…"`, shell
aliases, and `bd` invoked from inside a script file.

## SemVer

- **Contract version:** 1.0.0
- **Deprecation policy:** Recognising an additional command shape as a boundary
  is a minor bump. Ceasing to recognise one is a **major** bump — tasks that
  used to be attributed would silently stop being.

## SLA + telemetry

- **Latency (p99):** `latency_p99_ms: 30000` — the `hooks.json` timeout, sized for
  the boundary path, not the common one. A non-bd command exits in ~0.10s
  (interpreter startup plus a substring scan). A close spawns one `npx ccusage`
  (~1.9s cold, measured 2026-08-06, bounded at `ccusage_timeout_s` = 25) plus one
  `bd update --set-metadata`. This hook is non-blocking, so its latency delays
  nothing the user is waiting on.
- **Availability:** best-effort. A failed metadata write is logged to stderr and
  retried at the next boundary (Stop, or SessionEnd) rather than raised.
- **Telemetry:** none written. `~/.claude/logs/claude-code-events.jsonl` is read
  for optional enrichment and never appended to.
