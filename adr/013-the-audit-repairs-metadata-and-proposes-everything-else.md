# ADR 013: The audit repairs metadata unattended, and only proposes everything else

## Status

Accepted

## Date

2026-08-31

## Context

The gate stops an `Edit` from happening with no task claimed, so most work is
tracked by construction. What it cannot see is documented as a known limitation and
does not go away: a file written by `sed -i` or a heredoc, a commit made from
another terminal, a task closed while ccusage was unreadable, a claim left open
overnight. Each of those leaves the same residue — work that happened with no task,
or a task with no figures — and none of them announce themselves. adr/012 recorded
the shape of this problem from the other side: attributed-task coverage of actual
spend was **16.2%** in the first weeks of use, and the largest single cause was
whole projects with no workspace at all. Auto-init addressed the projects. It did
nothing for the gaps *inside* a tracked project, and there was no way to ask how
many there were.

So the plugin needs something that can be pointed at a workspace and answer "is
anything untracked right now?". That much is uncontroversial. The decision is what
it may then **do**.

Three facts constrain the answer.

**`--fix` is unattended.** It is invoked from inside an agent turn, by a skill or an
agent, and the write lands with nobody looking at it. Every other write in this
plugin happens at a task boundary the user just created, seconds after the fact,
from a measurement taken at the time. A backfill is a write to historical records
of a workspace the user is not currently reading. A false positive here is not a
noisy report — it is a wrong number in the store of record, permanently, and adr/001
means there is no second copy to reconcile against.

**Some gaps have exactly one correct repair and some do not.** A closed issue
carrying no `abacus_*` keys at all can only be brought to the documented shape one
way: the honest `unavailable` basis with no dollar figure. An issue left
`abacus_partial=true` can only be finalised one way: flip the flag, keep the figure
that was really measured (adr/011). Against that, *what a stale claim means* is a
question about intent — a claim held 30 hours is either an oversight or a long task,
and the two want opposite actions. Closing it would mark work done that is not done.
And the repair for an untracked afternoon is `bd create`, which is not reversible
bookkeeping.

**Reading `tct_*` as absent would make the audit destructive.** Every issue tracked
before 0.3.0 carries the pre-rename prefix. Without the read shim, all of them
detect as unattributed, and `--fix` then overwrites real recorded figures with
`unavailable` — the audit destroying exactly the data it exists to protect. This is
not hypothetical; it is one function call away in either direction.

## Decision

An audit script, a skill and an agent. The script writes; the prose does not.

1. **`hooks/scripts/audit.py` is the only writer.** `abacus_*` keys are constructed
   in one module (`hooks/lib/attribution.py`), and the audit's backfill is a new
   constructor **there** — `backfill_metadata()` — not an assembly of
   `--set-metadata` flags in a skill. A skill that built its own flags would be a
   second writer, free to drift from the rules that make the figures trustworthy and
   bound by none of them. The skill and the agent are explicitly forbidden from
   constructing metadata, and say so in their own text.

2. **`--fix` writes two kinds and refuses the other three.** `unattributed` and
   `unfinalised` are written unattended. `unclaimed`, `stale-claim` and
   `untracked-commits` are reported with a concrete proposed command and never
   written. The split is not caution about risk; it is the difference between a
   repair and a guess about intent.

3. **Every ambiguous case reports no gap.** An unreadable timestamp is not evidence
   of staleness. An unrecognised `abacus_schema` is not evidence of missing
   attribution — it is a shape written by a version that knows more than this one,
   and flattening it would lose information. A commit that cannot be placed in time
   is not evidence of untracked work. The audit is allowed to miss things; it is not
   allowed to invent them, because under `--fix` an invented gap is an unattended
   write.

4. **A backfilled figure is labelled as one.** `abacus_backfilled=true` on every
   write `--fix` makes. A reconstruction after the fact is weaker evidence than a
   measurement taken at the boundary, and a reader who cannot tell them apart will
   average the two together.

5. **A cost is never invented and never discarded.** No `abacus_session_id` means no
   ccusage reading to recover, so the write carries `abacus_cost_basis=unavailable`
   with no dollar figure and no token counts — not `0` (adr/005). An issue that
   already banked a real figure keeps it, with its token counts and models. Duration
   is computed from bd's own timestamps and **omitted** when they cannot be parsed,
   because `abacus_time.minutes_between` answers `0` for an unparsable date and a
   zero-minute task that ran all afternoon is the same lie as a zero-dollar one.

6. **`bd list --all --json` once, `git log` at most once.** The whole workspace
   including closed issues and their metadata comes back in one read. No `npx`, no
   ccusage: a historical spend cannot be reconstructed, so it is not attempted.

7. **A failed read is reported as a failed read.** `{"ok": false, "reason": …}` with
   `gaps` **absent** — not `gaps: []`. Telling someone their tracking is complete
   because `bd` could not be reached ends their investigation on the strength of a
   question that was never asked.

8. **One project.** The workspace the script was invoked in. No walking into
   siblings looking for more gaps.

9. **Not a hook, but hook discipline.** Nothing in `hooks.json` points at
   `audit.py`; it runs only when asked. It still wraps `main()` in `guard()`, still
   exits 0 whatever happens, still parses its own flags — `argparse` exits 2 with
   usage on stderr for an unknown option, which inside an agent turn is
   indistinguishable from the script being broken.

The write scope in point 2 was chosen explicitly by the user over two narrower
options (report-only, and report-plus-repair-the-obvious-one). This ADR records that
it was a decision rather than a default, and the five rails above are the conditions
under which it is safe.

## Consequences

### Positive
- The gaps the gate structurally cannot see become countable, and the answer arrives
  in one bd call rather than a manual sweep.
- Two of the five kinds close themselves. The remaining three arrive as concrete
  proposed commands, which is the form a user can act on in one step.
- The single-constructor rule survives a new writer. `backfill_metadata()` sits
  beside `build_metadata()`, subject to the same review and the same tests.
- `abacus_backfilled` makes the provenance of every repaired figure legible forever,
  so a later report can exclude reconstructions rather than silently blend them.
- The destructive failure mode is pinned by a test rather than by memory: a
  `tct_*`-attributed issue must receive **no** write, and an unknown
  `abacus_schema` must receive no write.

### Negative
- **This is the second code path that writes to the user's data unprompted**, after
  auto-init (adr/012), and the first that writes to *historical* records. A detector
  bug does damage that is invisible at the time and hard to attribute later.
- A backfilled `unavailable` basis is a permanent admission that a task's cost is
  unrecoverable. It is honest, and it is also a row in every future report that can
  never be filled in.
- `abacus_backfilled` is a new key on an existing contract. Consumers written
  against 1.0.0 of `bd-metadata-write.md` will not know to check it, and will treat
  a reconstruction as a measurement until they are updated.
- The untracked-commits detector depends on claim windows, so a workspace whose
  issues have unreadable `started_at` values silently reports nothing. Correct by
  rule 3, and indistinguishable from a clean workspace to anyone not reading
  `issues_seen`.

### Neutral
- `stale_after_h` defaults to 24 and is configurable. The number is a convention,
  not a measurement; a workspace where 30-hour tasks are normal should raise it.
- The git window defaults to 30 days. Outside a repository `commits_seen` is 0,
  which is not evidence of no untracked work — the report says how many it looked at
  for exactly this reason.
- The agent is defined at `sonnet` rather than the session's model. Reading a JSON
  report and grouping commits by subject does not need more, and the audit is
  something a user should feel free to run often.

## Alternatives Considered

### Alternative 1: Report only; never write

Rejected by the user after being offered, and worth recording as the option that was
turned down rather than overlooked. Its case is strong: the audit's whole risk is
the unattended write, and removing it removes the risk entirely. Its cost is that
`unattributed` and `unfinalised` are then repaired by hand, one `bd update` at a
time, with the flags typed by an agent from prose — which reintroduces the second
constructor this plugin has spent twelve ADRs avoiding. A read-only audit whose
remediation instructions are "assemble these keys yourself" is more dangerous than a
narrow writer, not less.

### Alternative 2: Fix everything, including stale claims and untracked commits

Rejected. It is the natural reading of "write everything the audit found", and it
fails on the first case it meets: closing a stale claim marks work done that is not
done, and the user cannot tell afterwards which closes were theirs. Creating issues
for untracked commits is worse — issue creation is not reversible bookkeeping, and a
detector that mistakes a claim window boundary produces tickets nobody asked for.
"Everything the audit found" is honoured by *reporting* everything and writing where
there is one correct answer.

### Alternative 3: A `Stop` or `SessionEnd` hook that audits automatically

Rejected. The existing reconcile hooks repair the *current* session's task, which is
bounded work with a known answer. A workspace-wide audit on every Stop would add a
`bd list --all` to the hot path, and — far worse — would make the unattended
backfill happen continuously rather than when a user asked for it. A write to
historical records should be traceable to a moment someone requested it.

### Alternative 4: Reconstruct missing costs from ccusage history

Rejected as not possible rather than as unwise. ccusage reports per *session*, and
the diff that produces a per-task figure needs the snapshot taken **at the claim**.
An issue with no `abacus_session_id` has no session to query, and one that has a
session id has no baseline within it — the task's slice of that session's total is
unrecoverable. Estimating from duration, or averaging neighbouring tasks, would
produce a number indistinguishable from a measurement, which is the failure adr/005
exists to prevent.

### Alternative 5: A skill with no script — let the agent run `bd` directly

Rejected. It is the cheapest thing to build and it breaks the invariant in CLAUDE.md
outright: `abacus_*` keys are constructed in one place. An agent assembling
`--set-metadata` flags from a prose table would omit `abacus_backfilled` the first
time it was in a hurry, write `abacus_cost_usd_estimate=0` the first time it saw an
unreadable cost, and there would be nothing in the repository that could fail
because of it.
