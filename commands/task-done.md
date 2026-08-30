---
name: task-done
description: Close the task in progress, which writes its cost, token, duration and model attribution
---

Close the beads task in progress. The close is what triggers attribution — the
PostToolUse watcher reads ccusage again, diffs against the baseline taken at claim
time, and writes the `abacus_*` metadata onto the issue.

## Argument

`$ARGUMENTS` is an optional issue id. When omitted, close whatever is in progress.

## Execution

**1. Find the task to close.**

```bash
bd list --status in_progress --json
```

If `$ARGUMENTS` names an id, use it. If not, and exactly one issue is in progress,
use that. If several are, list them and ask which — do not pick.

If none are, say so and stop. There is nothing to close and nothing to attribute.

**2. Close it.**

```bash
bd close <id>
```

Run this as a single Bash call with `bd close` as the command, not wrapped in a
subshell or a heredoc. The watcher tokenises the command it observes; an
invocation it cannot see leaves the cost unattributed until the Stop or SessionEnd
repair pass catches it.

**3. Read back what was recorded.**

```bash
bd show <id> --json
```

Take `[0].metadata` — `bd show` returns an array.

## Reporting

Report the recorded figures in one short block: cost estimate, total tokens,
duration in minutes.

Two rules on presenting the cost, both from adr/005:

- **Say "estimate".** The figure comes from a local list-rate pricing table on
  this machine. Call it an estimate, as its key name does.
- **If `abacus_cost_basis` is `unavailable`, report no dollar figure at all.** There
  will be no `abacus_cost_usd_estimate` key. Say the cost could not be read and give
  the duration, which is always recorded. Do not print `$0.00` and do not compute
  a substitute — a zero against an hour of work is a wrong answer wearing the
  costume of a measurement.

If `abacus_partial` is `true`, say the figures include carried-over work from an
earlier session.

## Failure

If the metadata is absent entirely, the write may have failed or the watcher may
not have seen the close. Say so, and mention that the Stop and SessionEnd passes
retry. Do not attempt to write the metadata by hand — `hooks/lib/attribution.py`
is the single place that constructs those keys, and a hand-written set would drift
from it.
