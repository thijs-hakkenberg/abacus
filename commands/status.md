---
name: status
description: Show what task is being tracked, whether the gate would allow an edit, and why not
---

Show the plugin's current state: what is being tracked, and whether an edit would
be allowed right now.

## Execution

Read, in this order, and stop at the first thing that explains the state:

**1. Is the plugin switched off?**

```bash
echo "${ABACUS_DISABLE:-unset}"
ls "${ABACUS_STATE_DIR:-$HOME/.claude/abacus}/disabled" 2>/dev/null
```

Either one being set means the gate is inactive and nothing is being attributed.
Say that and stop — everything below is irrelevant.

**2. Is this a beads workspace?**

```bash
bd list --status in_progress --json
```

A non-zero exit means no beads database resolved here. That is **not** the same as
nothing being claimed: the gate fails open in this case, so edits are allowed and
unattributed. Say which of the two it is.

**3. What does beads say is in progress?**

From the same command's output. This is the gate's actual source of truth — a claim
made in another terminal or by a subagent counts.

**4. What is this session attributing to?**

```bash
cat "${ABACUS_STATE_DIR:-$HOME/.claude/abacus}/session-<session-id>.json" 2>/dev/null
```

The interesting keys are `current_task`, `snapshot.ok`, and `snapshot_source`
(`watch-claim`, `gate-lazy`, or `session-start-adopt` — which of the three tells you
how the baseline was obtained).

## Reporting

A short block, in plain sentences:

- whether an edit would be allowed, and if not, that claiming a task fixes it;
- which task is in progress, per beads;
- which task this session is attributing cost to, and — **only if they differ** —
  say so, because that divergence is the one thing here that indicates a real
  problem;
- whether the cost baseline is readable (`snapshot.ok`). If it is false, the task
  will be recorded with `abacus_cost_basis=unavailable` and no dollar figure, so it
  is worth knowing before the close rather than after.

Do not print raw JSON, and do not list every config value. This command answers
"can I edit, and where is the money going" — not "dump the state".
