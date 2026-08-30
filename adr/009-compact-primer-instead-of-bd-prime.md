# ADR 009: A 342-character compact primer, not `bd prime`'s 4,854

## Status

Accepted

## Date

2026-08-06

## Context

The user's requirement was explicit: enforce task tracking and capture cost
metadata while "spending minimal tokens to enforce this". Every hook in this
plugin is mechanical and costs nothing — except SessionStart, which is the one
place that injects text into the model's context. It is therefore the only place
where the minimal-token requirement can actually be violated.

The obvious implementation was to pass `bd prime --hook-json` through. beads
provides it precisely for this: a SessionStart primer explaining the beads
workflow. Measured on bd 1.1.2, its `additionalContext` is **4,854 characters,
about 1,213 tokens**, injected into *every* session.

Two facts make that a bad trade here.

**The gate does not need the agent to be primed at all.** Enforcement is
mechanical (adr/002). An agent that has never read a word of beads documentation
still cannot edit a file without a claimed task, and when it tries, the deny text
tells it exactly what to run. The primer is not load-bearing for enforcement.

**SessionStart fires more than once per session.** It fires on `startup`,
`resume`, and `compact`, and `PreCompact` is wired to the same script. A long
session with two compactions pays the primer three times. At ~1,200 tokens that
is ~3,600 tokens of workflow manual in a context that is, by definition at
compaction time, already under pressure.

So what is the primer actually for, if not enforcement? One thing: **orientation**.
An agent that already knows the vocabulary — that tasks exist, that edits are
gated, that `bd update --claim` is the move — recovers from a deny in one Bash
call instead of exploring. That is worth a small number of tokens. It is not worth
1,200.

## Decision

SessionStart emits a compact primer by default. Three modes, `prime.mode`:

- **`compact` (default)** — a fixed 4-line block naming what the gate does and the
  two commands that satisfy it. **342 characters** against `bd prime`'s 4,854: a
  **14× reduction**, roughly 85 tokens against 1,213.
- **`full`** — passes `bd prime --hook-json`'s `additionalContext` through verbatim,
  for users who want beads' own manual. Falls through to `compact` if `bd prime`
  fails, so a broken bd never means no orientation at all.
- **`off`** — emits nothing. Enforcement is unaffected, since the gate does not
  depend on the primer.

Two refinements:

- **When a task is already in progress, the primer is replaced, not appended.** The
  `ACTIVE_TEMPLATE` variant (159 characters) names the claimed task, says edits are
  attributed to it, and how to close it. An agent resuming mid-task does not need
  the how-to-claim instructions; it needs to know what it is charged to.
- **When a `beads@*` plugin is enabled, this plugin emits no primer at all.** beads
  ships its own SessionStart primer, and two overlapping instruction sets in one
  context is worse than either alone. Detected by scanning `enabledPlugins` in
  `~/.claude/settings.json` and `settings.local.json` for a key whose name before
  the `@` is exactly `beads`. No such plugin is installed today; this handles a
  future install by deferring rather than by duplicating.

The UserPromptSubmit statusline follows the same budget discipline: **one line, 63
characters**, from cached state only, spawning no subprocess, and silent entirely
when no task is claimed.

## Consequences

### Positive
- The minimal-token requirement is met with a measured figure rather than an
  assertion: 342 characters versus 4,854, per session, and the reduction compounds
  across compactions.
- The agent still arrives oriented, so the deny → claim → retry loop costs one Bash
  call. Dropping the primer entirely would have saved another ~85 tokens and cost
  more than that on the first deny.
- An agent resuming a session mid-task learns which task it is charged to, which
  is the fact most relevant to it and which `bd prime` does not tell it.
- `full` mode means users who prefer beads' manual are not overridden by this
  plugin's opinion, and `off` means the primer can be removed without weakening
  enforcement.

### Negative
- The compact primer is a hand-maintained duplicate of a small part of beads'
  documented workflow. If beads changes its claim/close command syntax, this string
  goes stale and will teach the agent a command that no longer exists. Mitigated
  only by the string being 4 lines in one place, and by `bd`'s CLI surface for these
  two operations being stable across 1.x.
- Detecting a beads plugin by scanning settings files is heuristic. A beads plugin
  installed under a different name will not be detected and both primers will fire.
  Chosen over parsing `installed_plugins.json`, whose shape is less stable, and the
  failure mode is redundant text rather than broken behaviour.
- `full` mode pays the `bd prime` subprocess (~20s timeout budget) on every
  SessionStart. That is the user's choice when they select it, but it is a real cost
  the default avoids.

### Neutral
- `PreCompact` deliberately declares `hookEventName: "SessionStart"` in its output.
  It is the same script and the same injection; the event name in the payload is
  what Claude Code matches on, and SessionStart is the shape it expects for
  `additionalContext`.
- `PreCompact` does *not* baseline or prune — priming must never reset attribution
  (see the module docstring). A baseline is only ever created, never overwritten,
  because this hook fires on resume and compact as well as startup, and replacing
  a baseline would silently discard everything the current task had spent.

## Alternatives Considered

### Alternative 1: Pass `bd prime` through by default

Rejected on the measurement. 1,213 tokens per session, multiplied by every session
and every compaction, for content the enforcement mechanism does not need and the
deny text already contains the actionable part of.

### Alternative 2: No primer at all

Tempting, and it satisfies the letter of the token requirement best. Rejected
because the first deny in an unprimed session costs more than 85 tokens of
exploration — the agent has to discover what beads is, that it is installed, and
what the claim syntax is. `off` remains available for users who disagree.

### Alternative 3: Prime once per session and suppress on resume/compact

Rejected as more complexity than it saves. It requires durable "already primed"
state keyed on a session id that survives compaction, and the compact primer is
small enough that emitting it three times in a long session costs ~255 tokens
total. The active-task variant already handles the case that actually matters on
resume, and does it by being more useful rather than by being absent.
