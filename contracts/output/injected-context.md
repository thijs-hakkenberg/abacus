# Contract: injected context (primer and statusline) — output

## MCP binding

None (adr/004). Hook envelope: JSON on stdout, read by the Claude Code runtime and
placed into the model's context.

- **Emitted by:** `hooks/lib/hook_io.additional_context()`
- **Callers:** `hooks/scripts/session_start.py` (SessionStart and PreCompact),
  `hooks/scripts/prompt_statusline.py` (UserPromptSubmit)

This is the plugin's **only token cost**. Enforcement itself is mechanical and
costs nothing until it fires, so every character here is the one place where "spend
minimal tokens to enforce tracking" can actually be violated.

## Output schema

```json
{
  "type": "object",
  "properties": {
    "hookSpecificOutput": {
      "type": "object",
      "properties": {
        "hookEventName": { "type": "string", "enum": ["SessionStart", "UserPromptSubmit"], "description": "PreCompact declares SessionStart, not PreCompact — the context lands in the post-compaction session, which is what Claude Code reads it as." },
        "additionalContext": { "type": "string", "minLength": 1, "description": "Plain text. Never emitted empty: additional_context() returns early on falsy input rather than writing an envelope with nothing in it." }
      },
      "required": ["hookEventName", "additionalContext"]
    }
  },
  "required": ["hookSpecificOutput"]
}
```

Silence — empty stdout — is a valid and frequent output. It is what a
non-beads directory produces, what `prime.mode: "off"` produces, what a session
with nothing claimed produces on UserPromptSubmit, and what any failure produces.

## The three shapes

### Compact primer (SessionStart / PreCompact, default)

~450 characters: what the gate does, and the two commands that satisfy it. That is
the entire actionable content of `bd prime --hook-json`'s 4,854 characters
(~1,200 tokens), at under a tenth of the size (adr/009). The gate does not need any
of the rest — enforcement is mechanical, and an agent that has read no beads
documentation still cannot edit without a claimed task.

### Active-task primer (SessionStart / PreCompact, when something is in progress)

Names the task, its title, and `bd close <id>`. Replaces the compact primer rather
than adding to it: an agent that already has work to attribute to does not need to
be told how to claim.

### Statusline (UserPromptSubmit)

**Exactly one line, no trailing newline.** `[abacus] tracking <id> — <title> (<n>m)`,
with the title truncated at 60 characters. This is prepended to the user's own
prompt on every turn, so the line length is the contract — a multi-line status
would be a per-turn tax. It is read entirely from the cached state file; this hook
spawns no subprocess at all.

Silent when nothing is claimed. The gate speaks up the moment an edit is attempted,
so nagging on every prompt would buy a warning the user is about to get anyway,
from the one place it can be acted on.

### Full mode (opt-in)

`prime.mode: "full"` passes `bd prime`'s own manual through **verbatim** — the
plugin does not reformat, summarise or annotate it, so the mode means what it says.
If `bd prime` fails, it falls back to the compact primer rather than to silence.

### Deferral

When a `beads@*` plugin is enabled in the user's settings, **nothing is injected at
all**. beads ships its own SessionStart primer, and two overlapping instruction
sets in one context is worse than either alone, so this context defers entirely
rather than trying to merge them.

## SemVer

- **Contract version:** 1.0.0
- **Deprecation policy:** Editing the primer's wording is a minor bump; the
  behavioural guarantees are its size budget (compact primer under 600 characters,
  statusline exactly one line) and the presence of the claim commands, and those
  are what the feature space asserts rather than the exact string. Changing the
  default `prime.mode` from `compact` to `full` would be a **major** bump — it
  multiplies every session's token cost by roughly ten (adr/009). Making the
  statusline multi-line, or making it speak when nothing is claimed, would also be
  major.

## SLA + telemetry

- **Latency (p99):** `latency_p99_ms: 5000`, taken from the tightest emitting
  event rather than the loosest. The three are `latency_p99_ms: 20000` for
  SessionStart, `10000` for PreCompact and **`5000` for UserPromptSubmit**, which
  sits between the user pressing return and the agent starting. The statusline's
  measured path is ~0.10s and has no subprocess by construction.
- **Availability:** best-effort. Every failure degrades to silence, never to a
  partial or malformed envelope.
- **Telemetry:** none. The injected text is visible in the session transcript,
  which is where its cost is auditable — by `ccusage`, as it happens.
