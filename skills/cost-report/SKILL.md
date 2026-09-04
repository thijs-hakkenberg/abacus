---
name: cost-report
description: Report what recent beads tasks cost, from the abacus_* attribution metadata on the issues themselves
triggers:
  - /cost-report
---

## What This Skill Does

Reports what tracked work actually cost, by reading the `abacus_*` metadata this plugin
wrote onto beads issues. The issues are the store of record — there is no separate
database to query (adr/001), so this is a read over `bd`.

## Execution

**1. Get the issues.**

```bash
bd list --status closed --json
```

Add `--status in_progress` and `--status open` as separate calls if the user asked
for everything; an in-progress task carries no `abacus_*` keys yet, which is correct
rather than missing — its cost is not known until it closes.

Take `metadata` from each issue. Skip issues with no `abacus_schema` key: they were
never tracked by this plugin, and inferring anything about them would be invention.

**2. Check the schema version.** `abacus_schema` should be `1`. If a value other than
`1` appears, say so and do not interpret that issue's other keys — the version
exists precisely so a reader can tell it is looking at a shape it does not know.

**3. Build the report.**

Per task: id, title, cost estimate, total tokens, duration in minutes, and whether
it is partial. Then a total.

**4. Only if the user asked about commits**, read the `abacus_commit_*` keys off the
same metadata you already have. No extra `bd` call, and no `git` call unless you are
checking whether a sha still exists.

## Presenting the cost — three rules that are not negotiable

These come from adr/005 and `contracts/output/bd-metadata-write.md`, and they are
the whole reason the metadata is shaped as it is.

**Always label it an estimate.** The figure is computed on this machine from a
pinned local list-rate pricing table. It is not billing, it is not account-level
spend, and it must never be presented in a way that could be quoted as either. The
key is named `abacus_cost_usd_estimate`; use the same word.

**Never substitute a zero for an absence.** When `abacus_cost_basis` is `unavailable`
there is no `abacus_cost_usd_estimate` and no token count. Report the duration, which
is always present, and say the cost could not be read for that task. Do not print
`$0.00`, do not omit the row silently, and do not estimate from the duration. An
absent figure prompts a question; a fabricated zero ends one.

**Exclude unavailable tasks from the total, and say how many you excluded.** A total
that silently drops rows is a wrong number; a total that says "sum of 9 of 12 tasks;
3 had no readable cost" is a right one.

## Cost per commit

A task's metadata may carry one `abacus_commit_<sha12>` key per commit recorded against
it, each valued `<basis>:<session-id>:<epoch>` (adr/015). The key's suffix is the
abbreviated sha; the basis is `declared` (the commit message named the task in a
`Beads-Task:` trailer) or `observed` (HEAD moved while the task was claimed).

**Nothing apportioned is stored.** The share is computed here, at read time, from the
task's own total and its own edge count — which is why the method can change without
migrating anything. Divide equally within the task, and **always print the
denominator**:

```
b0cff66  $0.20  (1 of 4 commits in abacus-7 · task total $0.8123 · apportioned-equally-within-task)
```

That trailing clause is not decoration. It is what lets the user see the apportionment
and disagree with it, which is the only honest posture for a figure this soft. Drop it
and the number travels as though it were measured.

Four rules on top of the three above, all of which follow from them:

- **Omit the commit's share entirely when the task's `abacus_cost_basis` is
  `unavailable`.** There is no task total to divide, so there is no share. List the
  commit with its basis and no figure — never `$0.00`, and never a guess from duration.
- **Say that per-commit costs do not sum to a repository total.** The relation is m:n:
  a `declared` commit closing three tasks carries the same sha key on all three issues,
  so its work is counted once per task. When you show such a commit, show the **set** of
  shares rather than picking one or adding them.
- **Report the basis with every edge**, exactly as `abacus_cost_basis` accompanies every
  cost. `observed` means "this commit landed while this task was claimed" and nothing
  stronger — it cannot distinguish work in a commit from work alongside it. Do not
  present the two bases as equally strong evidence.
- **A sha that no longer resolves is marked, not dropped.** If `git cat-file -e <sha>^{commit}`
  fails the commit was amended or rebased away; label the row `rewritten` and keep it.
  The plugin never deletes these keys (that would be a write based on inference), so a
  report that quietly hid them would disagree with the store of record.

An `inferred` basis is never written by this plugin. If you see one, the data did not
come from abacus — say so rather than reporting it. Commits that fall in a claim window
with no edge are `/abacus:audit`'s business, reported there as a proposal.

## Cross-checking against the session total

If the user asks whether the per-task figures add up:

```bash
npx -y ccusage@20.0.14 claude session --json --mode calculate
```

Per-task figures are **snapshot diffs** across claim/close boundaries, so they sum
to less than the session total by design — anything spent while no task was claimed
belongs to no task. A gap is expected. Report it as a gap, not as an error, and do
not try to close it by redistributing the difference.

Pin the version as shown. `@latest` would use a different pricing table than the one
the recorded figures were computed with, which would make the comparison
meaningless.

## Reporting

A compact table, then the total, then the excluded count if any. Keep it short — the
user asked what things cost, not for a methodology essay. The three presentation
rules above govern the output; they do not need to be explained in it.
