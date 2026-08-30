# Contract: `PreToolUse` hook event — input

## MCP binding

None. This context ships no MCP server (adr/004) — the `bd` CLI is already a
JSON reader, and a second one would be redundant. Every interface here is a
Claude Code hook event delivered as JSON on stdin.

- **Event:** `PreToolUse`
- **Matcher:** `Edit|Write|NotebookEdit|MultiEdit`
- **Script:** `hooks/scripts/gate_edits.py`
- **Timeout:** 10s (`hooks/hooks.json`)
- **Entrypoint:** `main()` under `hook_io.guard`, reading `hook_io.read_payload()`

This is the only inbound event whose response can prevent a tool from running.
Its output contract is `contracts/output/permission-decision.md`.

## Input schema

```json
{
  "type": "object",
  "properties": {
    "session_id": { "type": "string", "description": "Attribution key. Also the ccusage --id argument. Falls back to sessionId, then the literal \"unknown\"." },
    "hook_event_name": { "const": "PreToolUse" },
    "tool_name": { "type": "string", "description": "Only Edit, Write, NotebookEdit and MultiEdit are acted on; anything else returns 0 immediately, before any config read." },
    "tool_input": { "type": "object", "description": "Read but not inspected — the gate's decision does not depend on which file is being edited, only on whether any task is claimed." },
    "cwd": { "type": "string", "description": "The directory the tool call relates to. Preferred over os.getcwd(): a subagent's hook can be invoked from a different directory than the edit targets, and a workspace lookup against the wrong directory silently mis-decides. Also accepted as project_dir / projectDir." }
  },
  "required": ["tool_name"]
}
```

Nothing is required beyond `tool_name`, and even that may be absent — a missing or
malformed payload parses to `{}`, which fails the `GATED_TOOLS` check and allows.
Every field is defensively read.

## SemVer

- **Contract version:** 1.0.0
- **Deprecation policy:** Adding a tool to the matcher is a minor bump.
  Removing one, or changing the deny condition such that a previously-allowed
  edit is now denied, is a **major** bump — it changes whether a user's existing
  workflow still functions.
- **Upstream drift:** this is a Conformist relationship. If Claude Code renames
  `tool_name` or `cwd`, the gate reads the missing key as absent and allows —
  the plugin degrades to inert rather than to blocking. That is the intended
  failure direction (adr/002).

## SLA + telemetry

- **Latency (p99):** `latency_p99_ms: 10000` — the `hooks.json` timeout. The
  *measured* path is far below it: python3.9 startup ~0.10s plus one
  `bd list --status in_progress --json` at ~0.45s (bd 1.1.2), memoised for 3s
  against the same session and workspace (adr/008). No `npx` spawn on the
  decision path; the lazy-snapshot repair path is separately bounded at 4s
  (`GATE_SNAPSHOT_TIMEOUT_S`) precisely so a slow `npx` cannot consume the
  10s budget while the user's edit is blocked.
- **Availability:** must be treated as 100% by callers, because a failure to
  respond is indistinguishable from an allow. Every error path — `bd` absent,
  `bd` non-zero, no `.beads/`, unexpected exception — allows and logs to stderr.
- **Telemetry:** none emitted by the hook. Denials are observable in Claude
  Code's own hook logs; the OTEL event stream is *read* here, never written.
