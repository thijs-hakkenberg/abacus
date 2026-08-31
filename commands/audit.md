---
name: audit
description: Check whether any work is unclaimed or unattributed, and repair the metadata gaps that have exactly one correct fix
---

Audit this project's tracking coverage. Argument: `$ARGUMENTS` — pass `fix` to repair
the metadata gaps as well as report them, otherwise this is a read.

## Execution

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/audit.py" --json
```

Add `--fix` when the user asked for `fix`. The script exits 0 whatever it finds, so
read the JSON rather than the exit code.

**Read `ok` first.** `false` means the audit could not look — no beads workspace, `bd`
missing, or the workspace would not resolve. Report `reason` and stop. Never report a
failed read as a clean result.

For anything beyond a straight read of the output — grouping untracked commits into
proposed issues, deciding what a stale claim means — use the `task-audit` skill or the
`abacus-auditor` agent, which hold the rules about what may and may not be written.

## Reporting

The blocking gap first (`unclaimed` means edits are being denied right now), then what
`--fix` repaired, then the gaps needing a decision with a concrete command for each.

Two things to state rather than imply: a repaired figure carries
`abacus_backfilled=true` because it is a reconstruction and not a measurement, and
where no measurement survived the basis is `unavailable` with **no** dollar figure —
never `$0.00`.

If there are no gaps, one line saying so with the counts that were checked.
