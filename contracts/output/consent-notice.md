# Contract: consent notice and acknowledgement record — output

## MCP binding

None (adr/004). Two surfaces, neither of which can block:

- **The notice** — hook envelope, `hookSpecificOutput.additionalContext` on stdout,
  emitted by `hooks/scripts/session_start.py` (SessionStart) and
  `hooks/scripts/prompt_statusline.py` (UserPromptSubmit). A special case of
  `contracts/output/injected-context.md`, subject to the same envelope rules.
- **The record** — a file, `$ABACUS_STATE_DIR/acknowledged.json`, written only by
  `hooks/scripts/acknowledge.py --accept` and read by `hooks/lib/consent.py`.

Constructed in exactly one place: `hooks/lib/consent.py`. Nothing else builds a
fingerprint or writes the record, for the same reason `attribution.py` is the only
constructor of `abacus_*` keys — a second writer is free to drift from the reader.

**Why this contract exists at all.** Until the record says otherwise, abacus performs
no write and no denial (adr/014). So this is the interface that decides whether every
*other* interface in this repo is live. Claude Code has no plugin-install hook, which
is why the notice is emitted from the two earliest available surfaces rather than once
at install time.

## Output schema — the notice

```json
{
  "type": "object",
  "properties": {
    "hookSpecificOutput": {
      "type": "object",
      "properties": {
        "hookEventName": { "type": "string", "enum": ["SessionStart", "UserPromptSubmit"] },
        "additionalContext": { "type": "string", "minLength": 1, "description": "Rendered from the live config, so it names the actual roots, the actual non-beads mode, and whether a remote push is configured. Never a generic template." }
      },
      "required": ["hookEventName", "additionalContext"]
    }
  },
  "required": ["hookSpecificOutput"]
}
```

Three guarantees about the text, which are what the feature space asserts rather than
the exact wording:

1. It states that abacus is **not governing anything** — that no tool call has been
   denied, no workspace created, no issue written to.
2. It lists what acknowledging would switch on, derived from the live settings.
3. It names `/abacus:acknowledge` and `/abacus:status`.

**Empty once acknowledged.** `consent.notice()` returns `""` in the steady state, and
`additional_context()` writes no envelope for falsy input — so the per-turn token cost
of this feature after the first session is zero.

**Emitted at most once per session.** Both surfaces write `consent_asked_at` into the
per-session state; whichever runs second sees it and stays silent. The notice replaces
the statusline rather than joining it, and is emitted regardless of `statusline:
false` — that setting governs a label for work in progress, not whether abacus may
introduce itself.

**Never emitted when `ABACUS_DISABLE=1`.** The kill switch outranks asking to be
switched on.

## Output schema — the acknowledgement record

`$ABACUS_STATE_DIR/acknowledged.json`, mode `0600`, written with the same
temp-file-fsync-replace discipline as the session state:

```json
{
  "type": "object",
  "properties": {
    "schema": { "const": 1 },
    "fingerprint": { "type": "string", "description": "sha256 of the governing settings, canonically serialised. Required; a record without it reads as never acknowledged." },
    "settings": { "type": "object", "description": "The six governing values as agreed to, dotted keys. Present so a later change can be reported as a diff rather than as a bare hash mismatch — and so the record is legible to a human who opens it." },
    "acknowledged_at": { "type": "string", "description": "ISO 8601 UTC." }
  },
  "required": ["schema", "fingerprint"]
}
```

**The six governing keys, and only these:** `gate.enabled`,
`gate.non_beads_project`, `auto_init.enabled`, `auto_init.roots`,
`auto_init.stealth`, `sync_on_session_end`. Each decides whether abacus denies
something, writes somewhere, or reaches a remote. `statusline`, `ccusage_version`,
`prime.mode`, timeouts and cache TTLs are deliberately excluded — they change what
abacus says, never what it does (adr/014).

Read through `abacus_config`'s accessors, not off the raw dict, so what was agreed to
is the value the plugin will act on. `auto_init.roots` is therefore `null` when the
configured value is unreadable — adr/012 rail 5's "no scope at all", which is a
distinct thing to have consented to — and is compared as a **sorted set**, since
reordering a list grants no new scope.

**Three states.** `acknowledged` (fingerprint matches), `changed` (a record exists and
does not match), `never` (no record). A missing, unparseable, non-object,
fingerprint-less, or future-schema record all resolve to `never`. This interface fails
**closed**: "I cannot tell whether you agreed" must never resolve to "yes". It is the
second such path in the plugin, after `auto_init` (adr/012).

## Output schema — `acknowledge.py --json`

```json
{
  "type": "object",
  "properties": {
    "acknowledged": { "type": "boolean" },
    "status": { "type": "string", "enum": ["never", "changed", "acknowledged"] },
    "settings": { "type": "object", "description": "The live governing settings, dotted keys." },
    "record_path": { "type": "string" },
    "changed": { "type": "array", "items": { "type": "string" }, "description": "Present only when status is `changed`: which governing keys moved. A bare hash mismatch is not actionable." },
    "action": { "type": "string", "enum": ["accept", "revoke"], "description": "Present only when one was requested." },
    "ok": { "type": "boolean", "description": "Whether that action succeeded. A failed write reports `acknowledged: false` and changes nothing." }
  },
  "required": ["acknowledged", "status", "settings"]
}
```

`--show` is the default, so a bare invocation is safe to run out of curiosity and
records nothing. Consent that can be given by mistyping is not consent.

## What this contract does not gate

Anything the user invoked. `/abacus:audit fix` repairs metadata while unacknowledged,
and `/abacus:task-start` claims a task. Gating those would make the notice
self-defeating: it names `/abacus:status` as the way to inspect before agreeing.

## SemVer

- **Contract version:** 1.0.0
- **Deprecation policy:** Rewording the notice is a **patch**; the guarantees above,
  not the string, are the contract. Adding a key to `GOVERNING_KEYS` is a **minor**
  bump and invalidates every existing acknowledgement by construction — that is the
  intended effect, and it is why additions are made deliberately rather than
  incidentally. *Removing* a key, so that a setting which governs behaviour no longer
  re-asks, is **major**. Changing the unacknowledged default from inert to governing
  would be major and would need a new ADR; adr/014 exists to say it is not a
  configuration choice.
- **Schema field:** bumping `schema` past 1 invalidates existing records (they read as
  `never`), so a bump must come with a reason worth re-asking every user for.

## SLA + telemetry

- **Latency (p99):** `latency_p99_ms: 5000`, taken from the tightest emitting event —
  `UserPromptSubmit`, which sits between the user pressing return and the agent
  starting. The check on the read path is one small JSON parse with no subprocess, so
  it is well inside every event's budget; the point of stating the tightest is that
  this check is now a precondition on the hot `PreToolUse` path too, where it must
  cost nothing measurable.
- **Availability:** best-effort on the notice (any failure degrades to silence),
  fail-closed on the record (any failure degrades to inert).
- **Telemetry:** none. The record is a local file; the notice is visible in the
  session transcript. A consent mechanism that phoned home about consent would be
  answering the wrong question.
