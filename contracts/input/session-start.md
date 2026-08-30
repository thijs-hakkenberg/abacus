# Contract: `SessionStart` hook event — input

## MCP binding

None (adr/004). Claude Code hook event, JSON on stdin.

- **Event:** `SessionStart`
- **Matcher:** none — every session
- **Script:** `hooks/scripts/session_start.py`
- **Timeout:** 20s (`hooks/hooks.json`)
- **Entrypoint:** `main()` under `hook_io.guard`

The same script also serves `PreCompact` — see
`contracts/input/pre-compact.md`, which differs only in what it is permitted to
do to session state.

This is the only hook in the plugin that spends tokens, via
`contracts/output/injected-context.md`.

## Input schema

```json
{
  "type": "object",
  "properties": {
    "session_id": { "type": "string", "description": "Keys the state file (session-<id>.json) and is the ccusage --id argument." },
    "hook_event_name": { "const": "SessionStart" },
    "source": {
      "type": "string",
      "enum": ["startup", "resume", "compact", "clear"],
      "description": "Read and recorded, but does NOT gate whether a baseline is created — see below. Absent/unrecognised is treated as \"startup\"."
    },
    "cwd": { "type": "string", "description": "Where the workspace lookup happens. No .beads/ here means the hook returns 0 in silence: spending tokens describing a tracker this repo does not use is pure waste." }
  },
  "required": []
}
```

### Why `source` does not gate the baseline

A baseline is **created but never overwritten**, on every source. That is a
stronger invariant than branching on `source`, and it is deliberate: ccusage
totals are cumulative per session id, so an existing baseline still diffs
correctly after a resume or a compaction, and replacing it would silently discard
everything the current task had spent. `resume` needing preservation and `startup`
needing creation collapse into one rule rather than two code paths that could
disagree.

An in-progress task with no baseline is adopted here with
`snapshot_source: "session-start-adopt"` — a task claimed in another terminal or
an earlier session would otherwise only start counting at the first edit this
session happens to make.

## SemVer

- **Contract version:** 1.0.0
- **Deprecation policy:** Adding a recognised `source` value is a minor bump.
  Changing the injected primer's *content* is a minor bump; changing it from
  compact to full by default is a **major** bump, because it changes every
  session's token cost (adr/009).

## SLA + telemetry

- **Latency (p99):** `latency_p99_ms: 20000` — the `hooks.json` timeout. Measured
  path: one `bd list` (~0.45s), plus one `npx ccusage` (~1.9s cold) only when a
  task is being adopted, plus a state-directory prune. Non-blocking; the session
  is not held on it.
- **Availability:** best-effort. `bd` absent or non-zero means no adoption and the
  compact primer still injects; every failure path exits 0.
- **Telemetry:** none.
