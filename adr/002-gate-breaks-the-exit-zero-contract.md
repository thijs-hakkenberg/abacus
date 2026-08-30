# ADR 002: The gate may deny; the other six hooks always exit 0

## Status

Accepted

## Date

2026-08-05

## Context

The rule this plugin's hook discipline starts from is: *every hook exits 0 on every
code path* — a failure in the plugin's own tooling degrades to "no data for this
call", never a broken session. That rule is right, and six of this plugin's seven
hooks follow it exactly, via a single `hook_io.guard()` wrapper that catches
everything.

But this plugin's central requirement is enforcement: all work must be tracked
as a task. A hook that always allows the tool call enforces nothing. The
requirement and the inherited rule are in direct conflict for exactly one hook.

Claude Code's `PreToolUse` event provides the mechanism — `hookSpecificOutput`
with `permissionDecision: "deny"` and a `permissionDecisionReason` that is fed
back to the agent. Two things about it shaped the decision:

- **The deny is data on stdout, not an exit code.** The hook still exits 0; the
  JSON *is* the decision. So the "always exit 0" rule is preserved literally,
  and what actually breaks is the weaker property the rule was standing in for:
  *no hook ever changes whether a tool runs*.
- **The reason text is the entire recovery mechanism.** The agent sees only that
  string. A deny with a vague reason costs a round of guessing; a deny with the
  exact commands to run costs one Bash call.

The alternative shape of enforcement — telling the agent in a SessionStart
primer to always claim a task first — was the starting assumption and was
rejected on cost and reliability. It spends tokens on every single session
whether or not it is needed, it is forgettable halfway through a long session,
and it degrades exactly when the context is fullest.

## Decision

`hooks/scripts/gate_edits.py` is the one script in this plugin permitted to emit
a permission decision. The other six never do.

- Matched on `Edit|Write|NotebookEdit|MultiEdit`. When a beads workspace exists
  and `bd list --status in_progress --json` returns `[]`, the gate denies with
  remediation text naming the exact commands (`bd ready --json`,
  `bd update <id> --claim --json`, `bd create "<title>" --silent`) and the
  bypass (`export ABACUS_DISABLE=1`).
- **Every path except that one genuine case fails open.** bd missing from PATH,
  bd timing out, bd exiting non-zero, no beads workspace, a malformed payload,
  an unexpected exception: all allow. A gate that blocks a user's edits because
  its own tooling broke is worse than no gate at all.
- The decision ladder is ordered so cheap disqualifiers come first and the only
  subprocess spawn happens when a real decision depends on it: kill switch →
  config → workspace presence → recent-allow cache → `bd list`.
- **Enforcement costs zero tokens until it fires.** The gate is mechanical. A
  session where the user claims tasks properly never sees a single token of
  gate output, which is the requirement that motivated the whole design.
- The distinction between `bd list` returning `[]` and exiting non-zero is
  load-bearing and is encoded in `beads.in_progress`'s `available` flag. Both
  mean "no issue came back", but `[]` means *nothing is claimed* (deny) while
  rc=1 means *there is no database here* (allow). Collapsing them would make the
  plugin unusable outside beads projects.
- Directories with no beads workspace at all default to `warn`, not `block`
  (`gate.non_beads_project`). A plugin installed user-wide must not make
  unrelated repos un-editable. `block` is available for users who want it.

## Consequences

### Positive
- Enforcement is mechanical rather than prompted: it cannot be forgotten, cannot
  be argued with, and costs no tokens until it actually fires. Measured
  alternative: a `bd prime` primer is 4,854 characters (~1,200 tokens) *every
  session* — see adr/009.
- The deny text is the whole recovery path, so the cost of hitting the gate is
  one Bash call rather than a negotiation.
- Because the gate reads bd rather than its own state, a claim made in another
  terminal or by a subagent opens it. Read-only Explore/Plan subagents are
  unaffected: they never trigger the Edit/Write matcher.
- Measured hot path in the normal flow: **0.69s** on the first gated edit of a
  task (one-time lazy-snapshot repair), then **0.08–0.11s**. No `npx` spawn on
  the gate path in steady state.

### Negative
- This is a real divergence from the inherited house rule, and anyone reading
  both plugins will find them contradicting each other on the point. Hence this
  ADR: the divergence is deliberate and scoped to one script.
- A bug in `gate_edits.py` can block a user's work in a way a bug in the other
  six cannot. Mitigated by (a) failing open on every path except the one genuine
  deny, (b) the largest test file in the suite covering the deny/allow matrix
  including bd-missing, bd-error, disabled, and non-beads modes, and (c) two
  independent kill switches (env var and marker file).
- **Bash file writes bypass the gate entirely** — `sed -i`, heredocs, `tee`,
  `python -c 'open(...)'`. Regex-gating Bash would false-positive on a large
  fraction of ordinary commands, so this is accepted as a known limitation
  rather than papered over. The gate covers the tool-call path Claude actually
  uses for edits.
- The gate's allow decision is cached for 3s (`DEFAULT_GATE_CACHE_TTL_S`), so an
  edit can slip through a second or two after the last task closed. Denies are
  never cached — see adr/008 for why the cache is deliberately one-sided.

### Neutral
- The exit code stays 0 even on a deny; the JSON on stdout is the decision. So
  the letter of the inherited rule holds and only its intent is diverged from.
- `hook_io.guard()` wraps `main()` in every script including this one, so an
  unexpected exception in the gate is an allow, not a traceback.

## Alternatives Considered

### Alternative 1: A SessionStart primer instead of a gate

Rejected — this was the original assumption. It spends ~1,200 tokens per session
against an explicit minimal-token requirement, is forgettable mid-session, and
degrades precisely when context is fullest. A mechanical gate costs nothing until
it fires. A minimal primer is still shipped, but as *orientation* so a denied
agent already knows the vocabulary, not as the enforcement mechanism (adr/009).

### Alternative 2: Gate on Stop instead of PreToolUse

Rejected for v1. Blocking Stop until a task is claimed catches the omission after
all the work is done, when the useful moment to attribute it has passed, and it
argues with the user at the least welcome time. `stop_reconcile.py` exists but
only repairs attribution; it never blocks.

### Alternative 3: `ask` instead of `deny`

Rejected. `ask` puts the decision in front of the human on every untracked edit,
which is an interruption where the agent could have fixed it itself. `deny` with
remediation text is a machine-recoverable failure; `ask` is a human-recoverable
one, and the whole point is to not spend the human's attention on this.
