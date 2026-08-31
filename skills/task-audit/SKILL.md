---
name: task-audit
description: Find work that was never claimed or never attributed, and repair the metadata gaps that have exactly one correct fix
triggers:
  - /audit
  - /task-audit
---

## What This Skill Does

Answers one question: **is anything untracked right now?** The gate stops an `Edit`
from happening with no task claimed, but it cannot stop a `sed -i`, a heredoc, or a
commit made from another terminal, and it has nothing to say about a task that was
closed while ccusage was unreadable. Gaps accumulate silently. This finds them, and
repairs the ones that have exactly one correct answer.

## Execution

**1. Run the audit.**

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/audit.py" --json
```

If `$CLAUDE_PLUGIN_ROOT` is not set in your shell, locate the script once with
`find "$HOME/.claude/plugins" -name audit.py -path '*abacus*' 2>/dev/null | head -1`
and use that path.

Two subprocesses at most — one `bd list --all --json`, one `git log`. It exits 0
whatever it finds; the answer is in the JSON, not the exit code.

**2. Read `ok` before reading anything else.**

`{"ok": false, "reason": "..."}` means the audit **could not look** — no beads
workspace, `bd` missing, or the workspace would not resolve. Report the reason as-is
and stop. Do not say "no gaps found", and do not fall back to guessing from `git
log`: a clean bill of health that was never actually checked is worse than no
report, because it ends the user's investigation.

**3. Work through the gaps.**

Each carries `kind`, `issue_id`, `title`, `detail`, and `fixable`. Five kinds:

| kind | what it means | who fixes it |
|---|---|---|
| `unclaimed` | nothing is `in_progress`, so edits are being denied right now | the user, by claiming |
| `stale-claim` | a claim held past the threshold, still accruing cost | needs a decision |
| `unfinalised` | closed, but its attribution is still marked partial | `--fix` |
| `unattributed` | closed with no `abacus_*` metadata at all | `--fix` |
| `untracked-commits` | commits outside every claim window | needs a decision |

**4. Repair the metadata gaps.**

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/audit.py" --fix --json
```

`--fix` writes only the two `fixable` kinds, and only through
`hooks/lib/attribution.py` — the single constructor of `abacus_*` keys. Never
assemble `bd update --set-metadata` flags yourself. A second writer is free to drift
from the rules the real one enforces, and those rules are the entire point of the
metadata.

Check `fixed` and `fix_failed` in the result. A non-empty `fix_failed` means `bd`
rejected the write; say so plainly rather than reporting the gap as closed.

## The two gaps you must not fix yourself

**A stale claim** is a claim held longer than the threshold. Closing it would mark
work done that is not done. Finalising it would bank a figure against a task still
running. Both are guesses about intent dressed as bookkeeping. Report it — id, title,
how long it has been held — and ask whether it is still live, finished, or should go
back to `open` (`bd update <id> --status open`).

**Untracked commits** are the harder one, because the fix is a new issue and issue
creation is not reversible bookkeeping. Group the commits by what they were actually
doing, propose *one* issue per coherent piece of work with the shas that belong to
it, and get confirmation before running `bd create`. Twenty commits from one
afternoon are one gap, not twenty — the audit already aggregates them, so do not
un-aggregate them into twenty tickets.

Retroactively created issues cannot have a real cost. Their spend was never
snapshotted, so there is nothing to recover; say that rather than backfilling a
figure onto them.

## What a backfilled figure means, and how to say it

A repair is a reconstruction, not a measurement, and it is written to say so:

- **`abacus_backfilled=true`** on every write `--fix` makes. It is what lets a later
  reader tell a repair from a real claim/close measurement instead of averaging the
  two together.
- **`abacus_cost_basis=unavailable` with no dollar figure**, whenever no measurement
  survived. Not `$0.00`. A closed issue with no metadata has no
  `abacus_session_id`, so there is no ccusage reading to recover and nothing
  honest to write. Report the duration — which comes from bd's own timestamps and is
  always recoverable — and say the cost is unrecoverable for that task.
- **A banked figure is kept.** An issue left partial already has a real measured
  cost; finalising flips the flag and preserves the number. If you ever see a repair
  replace a figure with `unavailable`, that is a bug — report it.

The three presentation rules in the `cost-report` skill apply to anything you print
here too: always call it an estimate, never substitute a zero for an absence, and
exclude unreadable tasks from any total while saying how many you excluded.

## Reporting

Lead with the count and the one thing blocking the user (`unclaimed` is the only gap
that stops work right now). Then the fixed list, then the gaps needing a decision
with a concrete proposed action for each. Keep it to a screen — this is a report on
bookkeeping, not an audit opinion.

If there are no gaps, say so in one line with what was checked (`issues_seen`,
`commits_seen`). "No gaps across 14 issues and 31 commits" is a useful sentence;
"everything looks good" is not.
