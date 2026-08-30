# ADR 010: Task boundaries are found by tokenising Bash commands, not by regex

## Status

Accepted

## Date

2026-08-05

## Context

Attribution needs to know when a task starts and stops. The signal is the agent
running `bd update <id> --claim` and `bd close <id>`, observed from the PostToolUse
Bash hook, which receives the command string in `tool_input.command`.

The plan called for "regex-parse chained forms … documented as an approximation,
not a shell parser". Building that revealed the approximation is not uniformly
harmless — the errors fall into two very different classes.

**Missing a real boundary** costs attribution for one task. Recoverable: the gate's
lazy-snapshot path repairs a missed claim on the next edit, and the Stop and
SessionEnd passes repair a missed close.

**Inventing a boundary that never happened** is not recoverable, and a regex over
the raw command string does it readily:

```
echo "bd update x --claim"          # a regex sees a claim
grep -r 'bd close' .                # a regex sees a close
git commit -m 'bd close ab-4'      # a regex sees a close
```

The first would move a cost baseline for a claim that never occurred, silently
discarding whatever the real current task had spent. The last would finalise an
unrelated issue from a commit message. Both write wrong data that nothing later
detects or repairs, because from the plugin's point of view nothing went wrong.

Three real invocation forms also had to be handled, each found by testing against
bd 1.1.2 rather than by reading the docs:

- `cd subdir && bd update t-1 --claim` — chained, and the common shape in practice.
- `BEADS_DIR=/other bd close x` — a leading environment assignment. A parser that
  only inspects token 0 sees `BEADS_DIR=/other`, concludes no bd ran, and loses the
  attribution write for a task that really did close.
- `bd update <id> --status closed` — verified to genuinely close an issue, exactly
  as `bd close` does. Agents use both spellings. Watching only `bd close` loses
  those tasks' cost with no error anywhere.

## Decision

The command is **tokenised with `shlex`, not matched with a regex**.

- `shlex.shlex(command, posix=True, punctuation_chars=True)` with
  `whitespace_split = True`. `punctuation_chars` is what makes `&&`, `||`, `;`, `|`
  emerge as their own tokens so the line can be split into simple commands.
- **Quoting is respected, which is the whole point.** `echo "bd update x --claim"`
  tokenises to two tokens, the second a single quoted string, so no segment has
  `bd` at position 0 and no claim is seen. This is the property a regex cannot
  have.
- **A command that fails to tokenise yields no events.** An unterminated quote
  returns `[]`, which correctly means "no bd invocation detected". Guessing at a
  malformed command's intent is how a watcher invents boundaries.
- **Leading `VAR=value` assignments are stripped** (`_strip_env_prefix`), gated on
  `str.isidentifier()` so a genuine argument containing `=` is not mistaken for an
  assignment.
- **bd is matched by basename**, so `/opt/homebrew/bin/bd` matches while `bdiff`
  and `sbd` do not.
- **Issue ids are found by position, not by shape.** bd ids are opaque
  (`bd-a1b2`, `ab-e2e-ngd`, any custom prefix), so `_first_positional` takes the
  first non-flag argument, skipping flags that consume a value so
  `--reason done bd-a1b2` does not read `done` as the id.
- **Read-only subcommands are listed as an allowlist of things to skip**, not as a
  denylist of things to act on. An unfamiliar future subcommand therefore falls
  through to the harmless no-match path rather than being acted on.
- **Events keep execution order.** `bd update x --claim && bd close x` must take
  the baseline before diffing against it.
- **`--status closed` is a close and `--status in_progress` is a claim**, alongside
  the dedicated subcommands.
- **A cheap prefilter runs first**: return immediately unless `"bd "` appears in the
  command at all, so the ~95% of Bash calls that are not bd cost ~1ms.

This remains an approximation of a shell, and the docstring says so. It does not
expand variables, follow `bash -c`, resolve aliases, or read shell functions.

## Consequences

### Positive
- The unrecoverable error class is largely designed out. Quoted mentions of bd
  commands in `echo`, `grep`, commit messages and heredocs do not register as
  boundaries, so the parser does not invent task transitions.
- The recoverable error class has three independent repair paths: the gate's lazy
  snapshot for a missed claim, `stop_reconcile.py` for a close that happened
  outside the watcher's view, and `session_end.py` for a session ending mid-task.
  A missed boundary degrades to slightly-late attribution rather than none.
- `shlex` is stdlib, so this costs no dependency (adr/006).
- The parser is the most heavily tested unit in the plugin, including the chained,
  env-prefixed, quoted and `--status closed` forms above — each of which was a real
  bug found by testing rather than a hypothetical.

### Negative
- `shlex` is not `bash`. `bash -c 'bd close x'` is one quoted token and is missed.
  A shell alias or function wrapping bd is missed. `$BD close x` is missed. All
  fall into the recoverable class and are repaired at Stop or SessionEnd.
- `punctuation_chars=True` changes tokenisation subtly enough that the behaviour is
  worth asserting rather than reasoning about — hence the parser tests. A future
  Python changing `shlex`'s punctuation handling would be caught there.
- The valued-flag list in `_first_positional` is hand-maintained. A new bd flag
  taking a value could have its value misread as an issue id. Bounded blast
  radius: the id then does not resolve and the write fails, logged.
- **Bash file writes still bypass the gate entirely** (adr/002). This ADR is about
  detecting bd *invocations*, and does nothing about `sed -i`. Separate limitation,
  separately accepted.

### Neutral
- `bd update --claim` with no id targets bd's last-touched issue. Rather than track
  that, `_resolve_claim_id` asks `bd list --status in_progress` — after the claim
  has run, that is the answer.
- A repeat claim of the already-current task is a no-op. `--claim` is idempotent and
  agents re-run it; re-snapshotting would move the baseline forward and silently
  discard the cost accrued so far.

## Alternatives Considered

### Alternative 1: Regex over the raw command string

Rejected — this was the plan's approach until the false-positive class became
clear. `echo "bd close x"` mis-firing writes wrong attribution that no later pass
detects, because nothing looks wrong. Tokenising costs a few dozen lines and
removes that class.

### Alternative 2: Actually invoke a shell to parse the command

Rejected. Handing an arbitrary agent-authored command to a shell for *analysis*
means executing it, or reimplementing enough of `bash -n` to be worse than
`shlex`. Not acceptable for a watcher whose whole job is passive observation.

### Alternative 3: Do not watch Bash at all; poll bd state at each boundary

Rejected as the primary mechanism, though it is exactly what the repair passes do.
Polling every prompt or every Stop detects that a task changed but not *when*,
which is what a cost diff needs. Watching the command gives the boundary at the
moment it happens; polling is the safety net underneath it.

## Addendum: 2026-08-30 — newlines were never a separator, and the fix needs three exceptions

A coverage review of real captured sessions found a task that was demonstrably
closed inside an observed session yet kept `abacus_partial=true`. The cause was in
the separator set this ADR describes:

```python
_SEGMENT_SEPARATORS = frozenset(("&&", "||", ";", "|", "&", "\n"))
```

`"\n"` was listed and had **no effect whatsoever**. `whitespace_split = True` makes
`shlex` classify newline as whitespace, so a newline is consumed as a token
*delimiter* and is never emitted as a token — the membership test it was written for
could not fire. Every multi-line Bash command therefore tokenised into one long
segment whose position 0 was the *first* command, so:

```
cd some/dir
bd close ab-1
```

was read as a single `cd` invocation, and the close was invisible. This is the most
ordinary multi-step shape an agent produces, and it fell into the recoverable class
only in theory: `stop_reconcile.py` repaired the metadata but the boundary timing
was lost, and a session ending before Stop kept `abacus_partial=true` indefinitely.

The obvious fix — `command.split("\n")` before tokenising — is wrong in three ways,
each of which reintroduces the *unrecoverable* error class this ADR exists to
prevent:

1. **A trailing `\` is a line continuation.** Splitting it produces `bd update \`
   and a bare id on its own line: two fragments, neither a valid invocation.
2. **A quoted argument may contain a newline.** `bd close x --reason "line one\nline
   two"` splits mid-quote, so the first fragment has an unterminated quote and
   yields no events — losing a real close.
3. **A heredoc body is data, not commands.** `cat > f <<'EOF'` followed by a line
   reading `bd close ab-1` would fire a close for text being *written to a file* —
   exactly the `echo "bd close x"` false positive, one line further down.

Resolution: `_logical_lines()` reassembles physical lines into logical commands
before tokenising. It joins across a trailing `\`, joins forward while the candidate
fails to tokenise (an open quote), and skips heredoc bodies up to their delimiter,
recognising `<<EOF`, `<<-EOF`, `<<'EOF'` and `<<"EOF"` while not matching `<<<` or
`2>&1`. The `"\n"` entry is removed from the separator set with a comment saying why
it never worked, so it is not restored by someone reading the list as a shell
reference.

A second, subtler bug surfaced only after that fix, and only from an empirical probe
rather than a test: `shlex` renders an escaped newline as a token containing just
that newline. With continuations now handled, `bd update \` + newline + `<id>
--claim` tokenised to `["bd", "update", "\n", "<id>", "--claim"]`, and
`_first_positional` returned `"\n"` — the plugin claimed a task literally named
`"\n"`, and the close that followed matched nothing. `_tokenise` now drops
whitespace-only tokens: a token that is only whitespace is never an argument.

Both are covered by tests in `tests/unit/test_watch_bd_commands.py`, and two
scenarios in `features/task-close-attribution.feature` pin the newline case and the
heredoc case executably. The general lesson is the one this ADR already argued from
the other direction: the parser's correctness is not reasoned about, it is asserted.
A separator that was silently inert for three weeks is what happens when it is not.
