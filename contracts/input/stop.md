# Contract: `Stop` hook event — input

## MCP binding

None (adr/004). Claude Code hook event, JSON on stdin.

- **Event:** `Stop`
- **Matcher:** none — the end of every agent turn
- **Script:** `hooks/scripts/stop_reconcile.py`
- **Timeout:** 10s (`hooks/hooks.json`)
- **Entrypoint:** `main()` under `hook_io.guard`

## Input schema

```json
{
  "type": "object",
  "properties": {
    "session_id": { "type": "string" },
    "hook_event_name": { "const": "Stop" },
    "stop_hook_active": {
      "type": "boolean",
      "description": "Loop guard. When true this hook returns 0 before doing anything at all — we are already inside a Stop invocation, and going further risks re-triggering the same hook chain."
    },
    "cwd": { "type": "string", "description": "The workspace the bd reconciliation query runs against." }
  },
  "required": []
}
```

### What this hook exists to catch, and what it refuses to do

The Bash watcher sees `bd close` only when Claude ran it as a Bash tool call. A
close typed by the user in another terminal, run inside a script, or spelled in a
form the tokeniser does not recognise leaves the session still believing the task
is open — and every subsequent turn keeps accruing cost against an issue that
finished an hour ago. Stop is where that is noticed, because it fires at the end
of every turn and the check is one `bd list` the plugin already makes elsewhere.

Two deliberate non-behaviours:

- **It never finalises a task that is still in progress.** Stop fires on every
  turn, so doing that would scatter a dozen partial figures across one
  afternoon's work.
- **It never blocks.** The gate already enforces at the edit boundary (adr/002),
  so there is nothing here worth interrupting the user for. This hook emits
  nothing on stdout at all.

When the tracked task has left `in_progress` unseen, whether the figures are
final depends on *why*: `bd show` is consulted, and `abacus_partial=false` is written
only if the issue is genuinely `closed`. Writing `false` on a task someone merely
moved back to open would claim it was completed.

There is also an early exit when nothing is tracked, so a session that has claimed
no task never spawns `bd` at the end of a turn.

## SemVer

- **Contract version:** 1.0.0
- **Deprecation policy:** Ceasing to honour `stop_hook_active` would be a
  **major** bump. Beginning to block Stop (a `decision: "block"` response) would
  also be major — this contract's guarantee is that the event is observed, never
  vetoed.

## SLA + telemetry

- **Latency (p99):** `latency_p99_ms: 10000` — the `hooks.json` timeout. The
  common path (nothing tracked) is ~0.10s with no subprocess. When a task is
  tracked: one `bd list` (~0.45s), plus — only in the rare repair case — one
  `bd show`, one `npx ccusage` and one metadata write.
- **Availability:** best-effort. `bd` unavailable means "cannot tell", which is
  explicitly not treated as "was closed": state is left alone and the check
  repeats next turn.
- **Telemetry:** none.
