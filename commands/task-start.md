---
name: task-start
description: Claim an existing beads task, or create and claim a new one, so edits are attributable
---

Claim a beads task so the gate allows edits and cost starts accruing against it.

## Argument

`$ARGUMENTS` is either an existing issue id (`ab-4`, `bd-a1b2`) or a title for new
work. Distinguish by asking beads, not by guessing at the shape — beads ids are
opaque strings and a title can look like anything.

## Execution

**1. Decide whether the argument names an existing issue.**

```bash
bd show "$ARGUMENTS" --json 2>/dev/null
```

`bd show` returns a single-element **array** on success. A non-zero exit or empty
output means no such issue, so treat the argument as a title.

**2a. If it exists — claim it.**

```bash
bd update <id> --claim --json
```

**2b. If it does not — create, then claim.**

```bash
bd create "$ARGUMENTS" --silent
```

`--silent` prints just the new id. Then claim that id with the same
`bd update <id> --claim --json`.

**3. If `$ARGUMENTS` is empty**, show what is ready and stop — do not pick for the
user:

```bash
bd ready --json
```

List the ready issues with their ids and titles, and say that
`/abacus:task-start <id>` claims one.

## Reporting

One line: which task is now in progress, and its title. Do not print the raw JSON.

Do not explain the cost mechanism unless asked. The claim triggers the PostToolUse
watcher, which takes the ccusage baseline on its own — there is nothing for you to
do about attribution here, and nothing worth spending the user's context on.

## Failure

If `bd` is not on PATH or exits non-zero on every attempt, say so plainly and
mention that edits will still be allowed (the gate fails open when bd is
unavailable) but will not be attributed. Do not retry in a loop.
