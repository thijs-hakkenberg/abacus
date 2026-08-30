# Contract: `PreToolUse` permission decision — output

## MCP binding

None (adr/004). This is a **hook envelope**: JSON on stdout, read by the Claude
Code runtime.

- **Emitted by:** `hooks/lib/hook_io.deny()`, called from
  `hooks/scripts/gate_edits.py`
- **Corresponding input:** `contracts/input/pre-tool-use.md`

This is the only output in the plugin that *decides* something rather than
reporting something. Every other outbound message informs a reader; this one stops
a tool call.

## Output schema

### Deny

```json
{
  "type": "object",
  "properties": {
    "hookSpecificOutput": {
      "type": "object",
      "properties": {
        "hookEventName": { "const": "PreToolUse" },
        "permissionDecision": { "const": "deny" },
        "permissionDecisionReason": { "type": "string", "description": "Shown to the model and the user. Always names the commands that fix the situation and the environment variable that bypasses the gate." }
      },
      "required": ["hookEventName", "permissionDecision", "permissionDecisionReason"]
    }
  },
  "required": ["hookSpecificOutput"]
}
```

### Allow

**Empty stdout.** There is no allow envelope: saying nothing is the allow. This
matters for a consumer, because it means the absence of output and a crash before
any output are indistinguishable — which is deliberate. A gate whose own tooling
broke must not block the user's edits, so "no answer" resolves to "allow" by
construction rather than by an error handler someone has to remember to write.

### The exit code is not the decision

The process exits **0** on a deny. The JSON on stdout is the decision. This is the
one place in the plugin where a hook is permitted to influence whether a tool runs,
and it does so through the documented envelope rather than through a non-zero exit
(adr/002).

## When a deny is emitted

Exactly one situation, reached only after every cheaper disqualifier has passed:

1. The plugin is not disabled (`ABACUS_DISABLE`, marker file, config).
2. `gate.enabled` is true.
3. A beads workspace exists at the payload's `cwd`.
4. No fresh gate-allow memo covers this session and workspace.
5. `bd list --status in_progress --json` **succeeded** — `available: true`.
6. …and returned **zero** issues.

Two of those steps carry a distinction worth stating. Step 5 relies on `bd`
exiting non-zero when no database resolves, which is categorically different from
returning `[]`; conflating them would turn every tooling failure into a blocked
edit. And step 4's memo is **one-sided** — it caches allows for 3s but never
caches a deny (adr/008). A stale allow smears attribution by a second or two; a
stale deny would refuse an edit *after* the user correctly claimed a task, telling
them to do the thing they just did. That failure is designed out rather than
tuned.

A second deny reason (`DENY_NO_WORKSPACE`) fires only under the opt-in
`gate.non_beads_project: "block"` setting. The default is `warn`, because a plugin
installed user-wide must not make unrelated repositories un-editable.

## Reason text

The reason is remediation, not a scolding. Its guaranteed content:

- the two command sequences that satisfy the gate — `bd ready --json` then
  `bd update <id> --claim --json` for existing work, `bd create "<title>" --silent`
  then the same claim for new work;
- one sentence stating what attribution actually depends on ("cost and duration
  are attributed to whatever task is claimed when the edit lands"), so the rule
  is learnable rather than arbitrary;
- the documented bypass, `export ABACUS_DISABLE=1`.

An enforcement mechanism with no stated escape hatch is a trap, and one with no
stated remediation costs the agent a round of guessing. Recovery from a deny costs
Claude one Bash call.

## SemVer

- **Contract version:** 1.0.0
- **Deprecation policy:** Broadening the deny condition — any change that denies
  an edit which this version allows — is a **major** bump, because it can stop an
  existing workflow. Narrowing it, or editing the reason's wording while keeping
  the commands and the bypass present, is a minor bump. Beginning to exit non-zero
  instead of emitting the envelope would be major.
- **Upstream drift:** Conformist. If Claude Code changes the envelope's shape,
  this output stops being understood and every edit is allowed — the plugin
  degrades to inert, never to blocking.

## SLA + telemetry

- **Latency (p99):** `latency_p99_ms: 10000`, inherited from the `PreToolUse`
  timeout, since the decision is what that budget is spent on. The user's edit is
  blocked for the whole of it, which is why the decision path spawns `bd` at most
  once and never spawns `npx`.
- **Availability:** 100% by construction, in the sense that a non-answer is a
  valid allow. There is no failure mode in which this contract produces a deny it
  did not mean to.
- **Telemetry:** none emitted. Denials appear in Claude Code's own hook logs and,
  where the collector is running, in the OTEL event stream the plugin only ever
  reads.
