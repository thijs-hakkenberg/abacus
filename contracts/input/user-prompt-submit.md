# Contract: `UserPromptSubmit` hook event — input

## MCP binding

None (adr/004). Claude Code hook event, JSON on stdin.

- **Event:** `UserPromptSubmit`
- **Matcher:** none — every prompt the user types
- **Script:** `hooks/scripts/prompt_statusline.py`
- **Timeout:** 5s (`hooks/hooks.json`) — the tightest budget in the plugin
- **Entrypoint:** `main()` under `hook_io.guard`

## Input schema

```json
{
  "type": "object",
  "properties": {
    "session_id": { "type": "string", "description": "The only field this hook actually uses: it keys the state file read." },
    "hook_event_name": { "const": "UserPromptSubmit" },
    "prompt": { "type": "string", "description": "Present in the payload and deliberately never read. The statusline reports what cost is being charged to; it has no business inspecting what the user typed." },
    "cwd": { "type": "string", "description": "Not used — no workspace lookup happens here, because that would mean a filesystem walk on every turn." }
  },
  "required": []
}
```

### Why this hook spawns nothing

It reads the cached state file and nothing else. A `bd list` here would put ~0.45s
between pressing return and the agent starting, on **every single turn** — which
would make the plugin's cost as a latency tax exceed its value as a label. The
elapsed minutes in the line come from the `claimed_at` timestamp already in state.

It is also silent when nothing is claimed. The gate speaks up the moment an edit
is attempted, so nagging on every prompt would buy a warning the user is about to
get anyway, from the one place it can be acted on, at the price of a token on
every turn.

## SemVer

- **Contract version:** 1.0.0
- **Deprecation policy:** Beginning to spawn a subprocess on this event would be a
  **major** bump; it changes the per-turn latency characteristic that this
  contract exists to guarantee.

## SLA + telemetry

- **Latency (p99):** `latency_p99_ms: 5000` — the `hooks.json` timeout. The
  measured path is interpreter startup plus one small JSON read: ~0.10s, and
  bounded by construction because there is no subprocess and no network call. The
  5s budget is slack for a cold filesystem, not an expectation.
- **Availability:** best-effort; exits 0 and injects nothing on any failure.
- **Telemetry:** none.
