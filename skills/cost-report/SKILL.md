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
