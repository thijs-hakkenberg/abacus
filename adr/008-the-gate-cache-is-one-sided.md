# ADR 008: The gate caches allows but never denies

## Status

Accepted

## Date

2026-08-05

## Context

`gate_edits.py` runs before every `Edit`, `Write`, `NotebookEdit` and `MultiEdit`
call, and its decision requires asking bd what is claimed. Measured on bd 1.1.2:
a real `bd list --status in_progress --json` against the embedded Dolt database
costs **~0.45s**, which is roughly 85% of the hook's total runtime (interpreter
startup accounts for most of the rest).

Claude edits in bursts. A refactor touching eight files pays that 0.45s eight
times over, and the answer is the same every time — nothing about the claimed-task
set changes between two edits a second apart. A cache is obviously worth having.

But a cache has two directions, and they are not symmetric in consequence:

- **A stale *allow*** means an edit slips through a second or two after the last
  task closed. The cost is a negligible attribution smear: a few seconds of work
  charged to a task that had just finished. Nobody is harmed and nothing is
  mis-stated.
- **A stale *deny*** means refusing an edit *after* the user has correctly claimed
  a task — telling them to do the exact thing they just did. That is the plugin
  actively obstructing correct behaviour while giving misleading advice. It would
  also be maddening to diagnose: the remediation text names commands that have
  already been run successfully.

Those are not two settings of the same dial. One is a rounding error, the other is
a bug the user would reasonably file.

## Decision

The gate caches the *allow* decision only. Denies always re-query bd.

- `gate_allow` is written into session state on every allow, holding `{cwd, at}`.
  `_fresh_allow()` returns True only if the entry is for the same workspace and
  within the TTL.
- **`DEFAULT_GATE_CACHE_TTL_S = 3`**, overridable via `gate.cache_ttl_s` in config
  or `$ABACUS_GATE_CACHE_TTL_S`. Short enough that the attribution smear is bounded
  at a few seconds; long enough to cover a burst of edits.
- **The deny path is not cached and never will be.** The code comment at
  `DEFAULT_GATE_CACHE_TTL_S` states this as a designed property rather than a
  tuning choice, and the deny branch carries a pointer back to it.
- **The cache key includes the workspace, not just the session.** A claim in one
  repo says nothing about whether work in another repo is tracked, and a session
  can move between repos. Keying on session alone would let a claim in a tracked
  repo silently authorise edits in an untracked one.
- A TTL of `0` disables the cache entirely, which is what the tests use when they
  need to assert that bd is actually consulted.

## Consequences

### Positive
- A burst of edits pays `bd list` once rather than once per file. Measured in the
  normal flow: **0.69s** on the first gated edit of a task (which also does the
  one-time lazy-snapshot repair), then **0.08–0.11s** for subsequent edits.
- The one genuinely user-hostile failure mode — being told to claim a task you
  already claimed — is designed out rather than tuned down. No TTL value can
  produce it, so no future tuning can reintroduce it.
- Because a claim is picked up on the very next gated edit with no cache to expire
  first, the recovery loop after a deny is as tight as possible: deny → claim →
  retry succeeds immediately.

### Negative
- Attribution can smear by up to the TTL: work done in the seconds after a close,
  before the next claim, is charged to whichever task was current. Bounded at 3s
  and accepted. It is the same class of imprecision as the parallel-agent smearing
  in adr/003, several orders of magnitude smaller.
- The cache lives in the session state file, so it participates in the same
  atomic-write discipline as everything else there, and a state-file write happens
  on every allow. That write is a temp-file-plus-`os.replace` and is cheap, but it
  is not free.
- Denies remain the slow path (~0.45s + interpreter startup). Deliberate: the
  moment the user is being obstructed is the moment to be certain, and the deny is
  followed by a Bash round trip anyway, so 0.45s is not the bottleneck.

### Neutral
- The ccusage snapshot cache is also asymmetric, for a structurally similar reason
  in a different direction: reads at a task's *close* bypass it because a cache hit
  there would be recorded as a measured zero. See adr/003's addendum. In both
  cases one direction of staleness is harmless and the other produces a confident
  falsehood.

## Addendum: 2026-08-06 — the gate's ccusage timeout is capped at 4s

Found by timing the gate against a real workspace, not by any test.

The gate has one path that spawns `npx`: `_track()`'s lazy-snapshot repair, which
takes a cost baseline for a claim the PostToolUse watcher never saw. It was
inheriting the plugin-wide `ccusage_timeout_s` of 25s. The gate's own hook timeout
in `hooks.json` is **10s**.

So on a slow `npx`, Claude Code would kill the gate mid-write at 10 seconds —
losing the baseline the repair exists to create — after having stalled the user's
edit for the full 10 seconds first. The repair would fail *and* cost the maximum.
A RED test confirmed the gate sitting for **6.9s** against a hung ccusage.

Resolution: `GATE_SNAPSHOT_TIMEOUT_S = 4`, applied as a per-call config override
in `_track()` (`min(configured, 4)` — it only ever lowers). A cold `npx ccusage`
measures ~1.9s, so 4s is a comfortable ceiling for the normal case and a hard stop
well inside the hook's budget for the abnormal one.

The reasoning generalises and is worth stating: **the gate is the only caller that
reads ccusage with the user waiting**, so it is the only one that overrides the
timeout downwards. Attribution is nice to have; an unblocked edit is not
negotiable. When those two conflict, attribution gives up. Every other ccusage
caller (the PostToolUse watcher, Stop, SessionEnd) runs after or beside the user's
work rather than in front of it, and keeps the full 25s.

## Alternatives Considered

### Alternative 1: Cache both directions symmetrically

Rejected — this is the decision. A stale deny obstructs correct behaviour while
giving advice the user has already followed, which is categorically worse than a
few seconds of attribution smear.

### Alternative 2: No cache at all

Rejected. It costs ~0.45s per edit for an answer that cannot have changed, on the
one hook that sits directly between the agent and its work. The asymmetric cache
gets the benefit with no user-visible downside.

### Alternative 3: A long TTL (30s+) to match the ccusage cache

Rejected. The attribution smear scales directly with the TTL, and the marginal
benefit past a few seconds is small because edit bursts are short. 3s covers a
burst; 30s would let a whole task's opening work land on the previous task.
