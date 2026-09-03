# Contract: the audit report — output

## MCP binding

None (adr/004). This is a **CLI** interface: a script invoked deliberately, writing
one JSON document to stdout.

- **Command:** `python3 hooks/scripts/audit.py [--json] [--fix] [--stale-after-h N] [--since "<git date>"]`
- **Emitted by:** `hooks/scripts/audit.py`; the detectors are pure functions in
  `hooks/lib/audit.py`.
- **Callers:** the `/abacus:audit` command, the `task-audit` skill, the
  `abacus-auditor` agent. Not a hook — nothing in `hooks/hooks.json` points here,
  so nothing runs it on an event.
- **Exit code:** always 0. It runs inside an agent turn, where a non-zero exit reads
  as a broken tool call and derails the turn over a condition that is merely
  "nothing to report". An unrecognised flag is ignored rather than fatal, for the
  same reason (`argparse` would exit 2 with usage on stderr).
- **Default output is prose.** `--json` is what an agent passes; a human running it
  directly gets the rendered report.

## Output schema

```json
{
  "type": "object",
  "required": ["ok"],
  "properties": {
    "ok": { "type": "boolean", "description": "false = nothing was concluded. See `reason`. When false, NO other key is present." },
    "reason": { "type": "string", "description": "Present iff ok=false. Why the audit could not look: no beads workspace, bd not on PATH, or `bd list --all --json` unreadable." },
    "checked_at": { "type": "string", "description": "ISO-8601 UTC. The `now` every detector was evaluated against." },
    "issues_seen": { "type": "integer", "description": "Issues returned by the one bd read. The denominator of any 'no gaps' statement." },
    "commits_seen": { "type": "integer", "description": "Commits in the git window. 0 outside a repository, which is not evidence of no untracked work." },
    "gaps": { "type": "array", "items": { "$ref": "#/$defs/gap" } },
    "fixable": { "type": "integer", "description": "How many gaps `--fix` would write to." },
    "fixed": { "type": "array", "items": { "type": "string" }, "description": "Issue ids written successfully. Empty unless --fix was passed." },
    "fix_failed": { "type": "array", "items": { "type": "string" }, "description": "Issue ids whose write bd rejected. A non-empty array must be reported, not swallowed." }
  },
  "$defs": {
    "gap": {
      "type": "object",
      "required": ["kind", "issue_id", "title", "detail", "fixable"],
      "properties": {
        "kind": { "enum": ["unclaimed", "stale-claim", "unfinalised", "unattributed", "untracked-commits"] },
        "issue_id": { "type": ["string", "null"], "description": "null for the two workspace-level kinds (unclaimed, untracked-commits)." },
        "title": { "type": ["string", "null"] },
        "detail": { "type": "string", "description": "One sentence, human-readable, safe to print verbatim." },
        "fixable": { "type": "boolean", "description": "true only for unfinalised and unattributed." },
        "age_hours": { "type": "integer", "description": "stale-claim only." },
        "session_id": { "type": ["string", "null"], "description": "unfinalised/unattributed only. null when unrecoverable." },
        "shas": { "type": "array", "items": { "type": "string" }, "description": "untracked-commits only." },
        "subjects": { "type": "array", "items": { "type": "string" }, "description": "untracked-commits only, positionally aligned with shas." }
      }
    }
  }
}
```

Gaps are ordered by kind — `unclaimed` first, because it is the only finding that
blocks the user right now — then by issue id.

### What `untracked-commits` means, since v0.6.0

A commit is untracked when it falls outside every claim window **and** no issue in the
workspace carries an `abacus_commit_<sha12>` edge for it (adr/015). The second clause
narrows the set: a commit with a written edge was tracked by a mechanism the window
arithmetic cannot see, so reporting it would be a false positive against evidence the
plugin itself recorded.

This is a **narrowing only**. No new `kind` appears, nothing that was `fixable`
becomes unfixable or the reverse, and `fixable` on this kind is still hardcoded
`false` — the repair is `bd create`, which is not reversible bookkeeping. A consumer
that never looked at edges sees strictly fewer gaps of one existing kind.

### The invariant a consumer must respect

**`ok: false` omits `gaps` entirely.** Not `gaps: []`. A workspace that could not be
read must not be reportable as a clean one: `{"ok": true, "gaps": []}` from a failed
read tells someone their tracking is complete and ends their investigation. It is
the same class of wrong answer as a `$0.00` cost estimate (adr/005), and the
consumer's obligation is the mirror image — **read `ok` before reading anything
else**, and never translate a failed read into "no gaps found".

### Ambiguity resolves to "no gap"

Every detector's uncertain case reports nothing: an unreadable timestamp is not
evidence of staleness, an unrecognised `abacus_schema` is not evidence of missing
attribution, a commit that cannot be placed in time is not evidence of untracked
work. The audit is allowed to miss things. It is not allowed to invent them —
because under `--fix` a false positive becomes an unattended write to the store of
record (adr/013).

## The `--fix` write

`--fix` writes only the two `fixable` kinds, through
`hooks/lib/attribution.backfill_metadata()` — a second constructor of `abacus_*`
keys is exactly what `contracts/output/bd-metadata-write.md` exists to prevent. The
shape is that contract's, with one addition:

| Key | Meaning |
|---|---|
| `abacus_backfilled` | `true` on every write `--fix` makes. A reconstruction after the fact is weaker evidence than a measurement taken at the boundary, and without this key a reader cannot tell the two apart — they would average together in any report. |

Three rules, all inherited:

- **A cost is never invented.** A closed issue with no metadata has no
  `abacus_session_id`, so there is no ccusage reading to recover: the write carries
  `abacus_cost_basis=unavailable` with **no** dollar figure and no token counts.
  Not `0`.
- **A cost is never discarded.** An issue left `abacus_partial=true` already has a
  real measured figure; finalising flips the flag and preserves the number,
  including its token counts and `abacus_models`.
- **Duration is written when it can be computed**, from bd's own `started_at` /
  `closed_at`, and **omitted when it cannot** — `abacus_time.minutes_between`
  answers `0` for an unparsable date, and a zero-minute task that ran all afternoon
  is the same lie as a zero-dollar one.

Never written by `--fix`: a `stale-claim` (closing it would mark work done that is
not done; finalising it would bank a figure against a task still running) or an
`untracked-commits` gap (the repair is `bd create`, which is not reversible
bookkeeping). Both are judgements about intent and belong to the agent or the user.

## SemVer

- **Contract version:** 1.1.0 — the `untracked-commits` definition narrowed to
  exclude commits carrying a written edge (adr/015). Minor rather than major: the
  gap set shrinks, no `kind` changed, and nothing became fixable that was not.
  Widening it, or making `--fix` write this kind, would be **major**.
- **Migration:** none yet. `kind` is the field a consumer branches on; an unknown
  `kind` should be reported to the user verbatim rather than dropped.
- **Deprecation policy:** adding a key, or a new `kind`, is a **minor** bump —
  consumers must tolerate unknown kinds. Removing a key, renaming a `kind`, or
  making `--fix` write a kind it previously refused is **major**. Emitting `gaps` on
  an `ok: false` response would also be major: it reverses the invariant above.

## SLA + telemetry

- **Freshness:** computed on invocation, never cached. `checked_at` is the instant
  it was evaluated; a report is stale the moment anyone claims or closes anything.
- **Cost:** at most two subprocesses — one `bd list --all --json` (~0.2s on bd
  1.1.2) and one `git log` (~0.05s, skipped when there is no `.git`). No `npx`, no
  ccusage: reconstructing a historical spend is not possible, so it is not
  attempted. One `bd update` per repaired issue when `--fix` is passed.
- **Latency (p99):** `latency_p99_ms: 20000`. Not a hook timeout — nothing kills
  this script — so the figure is a budget for an agent turn: the bd read plus up to
  a few dozen sequential `bd update` calls on a large workspace.
- **Availability:** best-effort, and honest about it. Every failure path returns
  `ok: false` with a reason rather than a partial answer.
- **Telemetry:** none emitted. The `abacus_*` keys it writes are the record.
