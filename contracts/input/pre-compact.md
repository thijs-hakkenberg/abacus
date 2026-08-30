# Contract: `PreCompact` hook event — input

## MCP binding

None (adr/004). Claude Code hook event, JSON on stdin.

- **Event:** `PreCompact`
- **Matcher:** none
- **Script:** `hooks/scripts/session_start.py --precompact`
- **Timeout:** 10s (`hooks/hooks.json`)
- **Entrypoint:** `main()` under `hook_io.guard`, branching on
  `"--precompact" in sys.argv[1:]`

Shares its script with `SessionStart` because the two jobs overlap almost
entirely — orient the agent — and differ in exactly one respect, recorded below.
A separate script would have duplicated the primer logic and invited the two
copies to drift.

## Input schema

```json
{
  "type": "object",
  "properties": {
    "session_id": { "type": "string" },
    "hook_event_name": { "const": "PreCompact" },
    "source": { "type": "string", "description": "Typically \"compact\". Read but not branched on — the --precompact argv flag is the authority, because it comes from hooks.json and cannot be affected by an upstream payload change." },
    "cwd": { "type": "string" }
  },
  "required": []
}
```

### The one difference from `SessionStart`

`--precompact` **skips `_baseline()` and `state_store.prune()` entirely**. It only
re-primes. Compaction discards the conversation, not the work: the task is still
claimed and its cost is still accruing, so touching the baseline here would reset
attribution mid-task. Pruning is skipped for the same reason — it is a
session-boundary chore, and compaction is not a session boundary.

Note that the injected context declares `hookEventName: "SessionStart"`, not
`"PreCompact"`. The context lands in the *post*-compaction session, which is what
Claude Code reads it as. This is a deliberate mismatch between the event that
triggered the hook and the event the output declares.

## SemVer

- **Contract version:** 1.0.0
- **Deprecation policy:** Beginning to mutate session state on this event would
  be a **major** bump — it would break the invariant that a baseline is created
  once per session and never overwritten.

## SLA + telemetry

- **Latency (p99):** `latency_p99_ms: 10000` — the `hooks.json` timeout, tighter
  than `SessionStart`'s because no baseline is taken and therefore no `npx` can
  be spawned. The measured path is one `bd list` (~0.45s) to name the task in
  progress.
- **Availability:** best-effort; exits 0 on every path.
- **Telemetry:** none.
