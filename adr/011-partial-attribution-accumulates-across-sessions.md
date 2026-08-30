# ADR 011: Four boundaries finalise a task; partial figures accumulate, finalised ones are left alone

## Status

Accepted

## Date

2026-08-05

## Context

A task's cost is the difference between two ccusage readings (adr/003), which
requires knowing when the task ended. Four different things can end it, and they
were not designed together — each was added because a real case slipped through
the previous ones:

1. **The Bash watcher sees `bd close`.** The intended path.
2. **The Bash watcher sees a claim while a task is still open.** The user switched
   tasks without closing the first. Its cost so far has to be written before the
   baseline moves, or it is lost.
3. **Stop notices the task is no longer in progress.** The close happened outside
   the watcher's view — another terminal, a script, a spelling the tokeniser missed
   (adr/010). Without this, every subsequent turn keeps accruing against an issue
   that finished an hour ago.
4. **SessionEnd finds a task still claimed.** The session is closing; the ccusage
   baseline in the state file is the only record of where the task started, and
   once state is pruned it is unrecoverable.

Only case 1 is a completed task. Cases 2, 3 and 4 are interruptions, and a task can
be interrupted repeatedly — worked on Monday, resumed Wednesday, finished Friday.
The naive treatments both fail:

- **Overwrite on each finalisation** and a task spanning three sessions reports
  only its last sitting. Two thirds of its cost silently disappears.
- **Always add to whatever is already there** and closing an issue twice doubles
  its cost. `bd close` on an already-closed issue is not an error, and agents do
  re-run it.

There was also a real risk of the four paths drifting. Four call sites writing the
same eleven metadata keys with the same accumulation semantics is four chances to
get one of them subtly wrong, and the wrongness would be invisible — a plausible
number in a metadata field.

One further distinction had to be drawn at Stop. A task that left `in_progress`
might have been *closed*, or might have been moved back to open. Writing
`abacus_partial=false` on an un-claimed task would assert it was completed.

## Decision

**All four paths call one function.** `attribution.finalise()` in
`hooks/lib/attribution.py` is the only place that writes `abacus_*` metadata; the four
hooks differ only in the `partial` flag they pass and how they decide to call it.
There is no second implementation to drift.

Accumulation rules:

- **A task interrupted is written with `abacus_partial=true`.** That is not a lesser
  answer, it is a different claim: *this much was spent, and the task is not
  finished*.
- **On finalisation, existing metadata is read back and added to — but only if it
  is marked `abacus_partial=true`.** `attribution.carried_partial()` returns `{}` for
  anything else. So three sessions of partial work sum to the whole cost, and
  closing an already-finalised issue a second time changes nothing.
- **Every accumulating key participates**: cost, all five token counts, duration,
  and the OTEL counts. `abacus_duration_min` accumulates the same way, so elapsed time
  across sittings is the sum of the sittings, not the wall-clock span between the
  first claim and the last close.
- **Stop distinguishes closed from un-claimed.** It reads the issue's actual status
  and passes `partial = (status != "closed")`. A task moved back to open is
  recorded as unfinished, which is what it is.
- **Stop never finalises a task that is still in progress.** It fires on every turn;
  doing so would scatter a dozen partial writes across one afternoon.
- **Attribution only ever moves to the task that owns it.** Closing an issue this
  session never claimed writes no cost figure at all. The spend accrued under
  whatever *is* claimed, and charging it to an unrelated issue because it happened
  to be closed here would be worse than recording nothing.
- **The read-modify-write needs no race with `bd close`**, because
  `--set-metadata` works on closed issues (adr/001). Attribution follows the close
  rather than trying to get ahead of it.

## Consequences

### Positive
- A task spanning any number of sessions reports its whole cost. This is the case
  that motivated per-task attribution in the first place — real tasks are not
  session-shaped.
- Idempotence is structural rather than defended. Because only `abacus_partial=true`
  is carried forward, a duplicate close, a repaired Stop and a SessionEnd on the
  same task cannot compound. No dedup bookkeeping, no "already finalised" flag to
  keep in sync.
- One finalisation path means a fix or a new key lands in all four boundaries at
  once. Extracting this function is what made the lifecycle scripts land with all
  45 of their tests passing on the first run.
- `abacus_partial` is queryable, so unfinished work is visible: `bd list` plus a
  metadata filter distinguishes tasks with final figures from tasks still in
  flight.

### Negative
- `abacus_partial=true` is a promise the eventual close has to keep. If a task is
  interrupted and then closed in a context where this plugin is disabled or bd is
  unreachable, the partial figure stays partial forever and understates the task.
  Detectable (the flag is right there) but not self-correcting.
- Each finalisation costs a `bd show` to read carried figures, on top of the
  ccusage read and the `bd update`. Acceptable at task boundaries; it is why none
  of this runs on the edit hot path.
- Accumulated duration is the sum of sittings, which is the right number for
  effort but is *not* the calendar span. A reader wanting "how long did this take
  end to end" will misread it. The key is named `abacus_duration_min` rather than
  `abacus_elapsed_min` for that reason, which is a weak defence.
- Four boundaries means four chances to finalise at a moment that is arguably
  wrong. The Stop path in particular fires on every turn and depends on
  `bd list` being readable to conclude anything; when bd is unavailable it
  deliberately does nothing, because "cannot tell" is not "was closed".

### Neutral
- `abacus_schema` accompanies every write, so a future change to the accumulation
  convention is detectable rather than inferred.
- `attribution.clear_current()` follows every finalisation, so the next task starts
  from a clean baseline. Combined with the ccusage cache's write-through on a fresh
  read (adr/003 addendum), consecutive tasks abut exactly.
- SessionEnd's sync runs whether or not a task was left open. An earlier draft put
  it inside the open-task branch, which would have meant only sloppy sessions ever
  pushed their attribution — caught by a test written specifically for the
  clean-session case.

## Alternatives Considered

### Alternative 1: Finalise only on `bd close`, and accept losses elsewhere

Rejected. It loses the cost of every task interrupted by a session ending, which is
common rather than exceptional, and it loses any close the watcher does not see —
including entire classes of invocation the tokeniser cannot follow (adr/010). The
repair paths exist because those cases are ordinary.

### Alternative 2: Keep a running accumulator in plugin state instead of in bd

Rejected. The accumulator would have to survive compaction, resume, concurrent
writes and state pruning, and would be a second source of truth for a figure bd
already holds. Reading carried figures back from bd means the issue itself is the
accumulator, and state stays disposable (adr/001).

### Alternative 3: Write one metadata row per sitting and sum at read time

Rejected for v1. bd metadata is a flat string-keyed namespace with no list or
append semantics, so per-sitting rows would mean synthesising keys like
`abacus_cost_1`, `abacus_cost_2` and teaching every reader to enumerate them. It would
preserve more detail — which sitting cost what — at the price of every consumer
becoming non-trivial. `abacus_session_id` records the most recent session; finer
history is deliberately not kept.
