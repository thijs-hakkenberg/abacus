# Contract: `SessionEnd` hook event — input

## MCP binding

None (adr/004). Claude Code hook event, JSON on stdin.

- **Event:** `SessionEnd`
- **Matcher:** none
- **Script:** `hooks/scripts/session_end.py`
- **Timeout:** 60s (`hooks/hooks.json`) — the loosest budget in the plugin,
  because `bd dolt push` talks to a remote
- **Entrypoint:** `main()` under `hook_io.guard`

## Input schema

```json
{
  "type": "object",
  "properties": {
    "session_id": { "type": "string", "description": "Keys the state file holding the baseline. Once that file is pruned the baseline is unrecoverable, which is why this is the last chance to write anything." },
    "hook_event_name": { "const": "SessionEnd" },
    "reason": { "type": "string", "description": "Present in the payload and not branched on. A task left open is written as partial regardless of whether the session ended cleanly, was cleared, or the process was killed — the figures are equally real either way." },
    "cwd": { "type": "string", "description": "Determines whether a sync runs: `bd dolt push`/`sync` is attempted only when a beads workspace is present." }
  },
  "required": []
}
```

### What is guaranteed to happen, and in what order

1. **An open task is finalised as `abacus_partial=true`** and `current_task` is
   cleared. Partial is not a lesser answer, it is a different claim: "this much
   was spent by this session, and the task is not finished." A later close reads
   those figures back and adds to them (adr/011).
2. **The sync runs, if configured** — *after* the metadata write, never before, or
   the push would ship the repo without the attribution it exists to sync. This is
   deliberately outside the "was a task open" branch: a session that closed all
   its tasks properly has attribution sitting in the local Dolt DB and nothing
   else will ever ship it, so syncing only when a task was left open would sync
   only the sloppy sessions.
3. **The state directory is pruned** (`state_max_age_days`, default 14). This runs
   unconditionally — it is the plugin's only guaranteed cleanup point, including
   for sessions that never claimed anything.

Pushing is opt-in (`sync_on_session_end`, default `off`). `bd dolt push` reaches a
remote, can prompt, and can fail noisily on a session the user has just closed;
doing that by default on their behalf is not appropriate. A failure is logged and
never retried — the session is over, there is no one to prompt, and the metadata
is committed locally either way.

## SemVer

- **Contract version:** 1.0.0
- **Deprecation policy:** Changing `sync_on_session_end`'s default from `off` to
  `push` would be a **major** bump — it would start contacting a remote on
  behalf of users who never asked. Adding a new sync mode is a minor bump.

## SLA + telemetry

- **Latency (p99):** `latency_p99_ms: 60000` — the `hooks.json` timeout, sized for
  the remote call. With `sync_on_session_end: "off"` (the default) the measured
  path is one `npx ccusage` (~1.9s) plus one metadata write plus a prune. With
  `push`, add whatever the remote costs.
- **Availability:** best-effort on every step. A failed metadata write is logged;
  a failed push is logged; neither is raised, and the hook exits 0.
- **Telemetry:** none.
