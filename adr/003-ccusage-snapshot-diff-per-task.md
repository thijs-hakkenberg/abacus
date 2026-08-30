# ADR 003: Per-task cost by snapshot-diffing ccusage at task boundaries

## Status

Accepted

## Date

2026-08-05

## Context

ccusage is the obvious cost source: it reads Claude Code's own transcript files,
maintains its own pricing tables, is local and read-only, and needs no credential.
The author has reimplemented token counting before, in a session-level tracker, and
it produced an $8-versus-$411 discrepancy against ccusage's figure — a gap large
enough that the resulting report had to be withdrawn. That road is closed.

What ccusage does *not* provide is per-task granularity. Its unit is the session.
This plugin's unit is the task, and a session routinely spans several.

ccusage also offers no library API and no MCP server — the MCP server was removed
in commit `d7e6993`. The only interface is the CLI with `--json`, invoked through
`npx` since it is not installed globally on this machine.

**The concern that had to be settled first** was whether session-level totals can
be decomposed per task at all when subagents are involved. The user raised it
directly: cost attribution "may not be possible with subagents within a session".
This was validated empirically rather than assumed. On a real 5-subagent session,
`ccusage claude session --json` was checked against the raw transcript usage
lines: **251 raw usage lines collapsed to exactly 88 unique `(message.id,
requestId)` pairs — 69 from the main transcript, 19 from the `subagents/`
transcripts, zero overlap.** ccusage already walks the subagent transcripts and
deduplicates across them. So a session total *already includes* fan-out work,
which means the difference between two session totals is the true cost of
whatever happened in between, subagents included.

That is the whole basis for snapshot-diffing. Per-message accounting inside a
hook would have to re-implement that dedup and would get it wrong.

Two secondary findings shaped the adapter:

- **Per-entry `costUSD` is unreliable** (frequently `0.0`). Only the aggregate
  `totalCost`/`totalTokens` fields are trustworthy, which suits diffing.
- **A session ccusage has not seen yet legitimately reads as zero.** That must be
  distinguishable from *failing to read ccusage*, or a brand-new session's first
  task gets attributed against a bogus baseline.

## Decision

Cost per task is the difference between two ccusage session readings, taken at
task boundaries. `hooks/lib/ccusage.py` exposes exactly two operations —
`snapshot(session_id)` and `diff(before, after)` — and nothing else in the
codebase knows ccusage's field names.

- **Baseline on claim, closing read on close.** The PostToolUse watcher takes a
  snapshot when it sees `bd update <id> --claim`, and another when it sees
  `bd close <id>`; the diff is the task's cost. `gate_edits.py` carries a lazy
  repair path for a claim the watcher never saw.
- **Version pinned, never `@latest`** (`PINNED_CCUSAGE = "ccusage@20.0.14"`).
  ccusage carries the pricing table, so floating the version silently re-prices
  historical tasks and makes two runs of the same report disagree. Upgrade path:
  bump the pin, re-check a known session's total against the previous version,
  then commit the bump.
- **`--mode calculate`**, so figures are always priced from tokens × rates rather
  than from whatever an individual transcript line happened to record.
- **An explicit timeout** (`ccusage_timeout_s`, default 25s), which the author's
  earlier ccusage adapter lacked. A wedged `npx` would otherwise hang until the
  hook's own timeout killed it, losing the attribution write that was supposed to
  follow. `subprocess.TimeoutExpired` and `OSError` both degrade to
  `ok=False`.
- **`ok=True` with zeros ≠ `ok=False`.** A session ccusage has not seen returns
  `ok=True` and zeros, and diffing against it is safe. Only a genuine read
  failure returns `ok=False`, and that never becomes a dollar figure (adr/005).
- **Failed reads are never cached.** Caching an `ok=False` would freeze a
  transient npx blip in for the whole TTL and mis-attribute the next task to $0.
- **Deltas are clamped at zero.** Snapshots come from an append-only transcript,
  so a cumulative total can only grow. A negative delta means the baseline was
  not what we thought (session-id reuse, a cleared transcript) and clamping is
  honest where a negative dollar figure on a task is not.
- **`stdin=subprocess.DEVNULL` on every spawn.** Learned the hard way: a child that
  inherits a hook's stdin pipe can block forever waiting on a pipe nobody will
  close. The author reproduced this against MCP stdio and watched it time out at
  90s.
- **`shutil.which("npx")` before spawning.** Windows's `CreateProcess` does not
  consult `PATHEXT`, so a bare `"npx"` raises `FileNotFoundError` even when npx
  is genuinely on PATH.
- Every failure path in this module **degrades to a zeroed snapshot with
  `ok=False` rather than raising** — the opposite of the original's
  `raise SystemExit`, which was correct for a user-initiated MCP tool call and is
  wrong for a hook with no supervisor.

## Consequences

### Positive
- Per-task cost is correct under subagent fan-out without this plugin
  implementing any dedup of its own — the 88 = 69 + 19 evidence above is the
  reason to trust it, and it is what makes per-task attribution possible at all
  once subagents are in play.
- Two readings and a subtraction is a small enough mechanism to reason about
  completely. There is no accumulator to drift, no state to migrate.
- Verified end to end against real ccusage and real bd on 2026-08-06: closed
  issue `ab-e2e-ngd` carries `abacus_cost_usd_estimate 0.0952`,
  `abacus_tokens_total 144254`, `abacus_models 'claude-fable-5,claude-opus-5'`.
- ccusage runs only at task boundaries, never on the edit hot path, so its ~1.9s
  cold `npx` spawn lands on PostToolUse's 30s budget rather than in front of a
  user's edit.

### Negative
- **Parallel-agent smearing.** Session-scoped totals attribute whatever happened
  between two readings to the single task that was current. Concurrent subagent
  work on a *different* task inside the same session is charged to the current
  one, and when several tasks are claimed at once, attribution picks the most
  recently updated (`beads.most_recent`). Documented, not solved — solving it
  needs per-subagent-to-task mapping that neither ccusage nor the hook payload
  provides.
- A Node.js/`npx` dependency for cost. A machine without it records tasks with
  duration but no cost (`abacus_cost_basis=unavailable`), which is a degradation of
  the accounting half only — enforcement is unaffected.
- Pinned pricing goes stale by construction. A model released after 20.0.14 may
  price at zero or not at all until the pin is bumped. Deliberate: a wrong-but-
  stable figure is diagnosable, a silently-changing one is not.
- Each uncached snapshot is a full `npx` spawn, and there is no persistent
  ccusage process. Mitigated by a short-TTL cache — with the asymmetry described
  in the addendum below and in adr/008.

### Neutral
- Adapted from an earlier ccusage adapter of the author's, with the timeout and
  cache added. Where the two disagree, the difference is deliberate and noted in
  the module docstring.
- Only the `session` report is used here. ccusage's `blocks` and `daily` reports
  answer a session- or day-level question this plugin does not ask.

## Alternatives Considered

### Alternative 1: Count tokens from the transcript directly

Rejected. The author's earlier tracker did exactly this and produced the $8-vs-$411
gap that caused its fiscal report to be withdrawn. It also means owning a pricing
table, and re-implementing the `(message.id, requestId)` dedup across subagent
transcripts that ccusage already does correctly.

### Alternative 2: Use OTEL's `cost_usd` attribute on `api_request` events

Rejected. The attribute exists and is read by the OTEL log this plugin already
parses, so this was genuinely tempting. But it would create a second cost path
that can disagree with the first, with no pinned pricing table and no
subagent-aware dedup behind it. One authority is better than two that need
reconciling. OTEL contributes counts and durations only (`abacus_tool_calls`,
`abacus_active_min`), never money.

### Alternative 3: Accumulate cost incrementally per tool call

Rejected. It requires a running total in this plugin's own state, which then has
to survive compaction, resume, and concurrent writes, and which drifts from what
ccusage would say if asked fresh. Snapshot-diffing recomputes from the authority
every time.

## Addendum: 2026-08-06 — the closing read must bypass the cache

The 30s snapshot cache introduced above had a failure mode that the integration
suite caught and that no unit test would have: **a task claimed and closed inside
the TTL was recorded as having cost nothing.** Six of eleven integration tests
failed with `assert '0.0' == '1.25'` and similar.

The mechanism: the claim's snapshot populates the cache, the close happens inside
the TTL, the closing read is served the *same* value the claim wrote, and the
diff is exactly `0.0`. Worse than being wrong, it was written with
`abacus_cost_basis=ccusage-local-list-rate` — presenting a cache hit as a
measurement, which is precisely the dishonest zero adr/005 exists to forbid. And
it hit the single most common shape of work: a small fix, claimed and closed in
under a minute.

Resolution: `ccusage.snapshot()` takes a `fresh` parameter.
`attribution.build_metadata` passes `fresh=True` for the closing read.

`fresh=True` **bypasses the read cache but still writes through**. The
write-through is not incidental — it is what makes a claim immediately following
a close share the close's reading, so consecutive tasks abut exactly, with no gap
belonging to neither and no overlap charged to both.

The cache is therefore asymmetric in two independent directions in this plugin:
reads at a task's *close* never use it, and (per adr/008) the gate's *deny*
decision never uses its own. In both cases the asymmetry is because one direction
of staleness is harmless and the other produces a confident falsehood.
