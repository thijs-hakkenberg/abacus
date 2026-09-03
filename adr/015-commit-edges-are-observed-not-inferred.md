# ADR 015: Record the commits a task produced as observed edges, never inferred ones

## Status

Accepted

## Date

2026-09-03

## Context

abacus attributes cost, tokens and duration to a **beads task**, and records the
**session** the task was claimed in. Commits are invisible to that model except
negatively: `/abacus:audit` reports commits falling outside every claim window as one
aggregate `untracked-commits` gap, and adr/013 forbids repairing it — "the repair for an
untracked afternoon is `bd create`, which is not reversible bookkeeping." A run across
this machine measured **133 untracked commits over 11 repositories, 0 fixable**.

So the plugin can say a task cost $0.81, and it can say that some commits were never
tracked. It cannot say which commits a task produced, which session produced them, or
what any single commit cost. The relation is genuinely m:n — one commit can complete
several tasks, one task spans many commits — and it is stored nowhere.

Three primitives, with honest cardinalities. The commit is the only **instant** and the
only **immutable** one; sessions and tasks are intervals. So the commit is the fact, and
the edges are what need modelling:

| Edge | Cardinality |
|---|---|
| session ↔ task | m:n — already true, recorded lossily today as one `abacus_session_id` |
| task ↔ commit | **m:n** — this decision |
| commit → session | 1:0..1, deterministic at capture — this decision |

### What was established before deciding, rather than assumed

- **No Claude Code hook fires on a git commit.** The event enum in the v2.1.258 binary
  has 27 members and none is commit- or git-related. `PostToolUse` fires only after a
  **successful** tool; a failed `git commit` routes to `PostToolUseFailure`, which this
  plugin does not wire.
- **The sha is not reliably in the command's stdout.** `-q` suppresses git's
  `[branch shortsha]` line, and it is abbreviated when present; real commands are
  compound with heredocs (`git add -A && git commit -q -F - <<'EOF' && git log --stat`).
  Asking git is deterministic; parsing stdout is not.
- **`git rev-list --reverse OLD..HEAD` includes merge commits** — verified, 6 commits
  here of which 2 were merges — unlike `gitlog.recent_commits`, which passes
  `--no-merges`. A squash-merge commit *is* the work, so the capture path must not
  exclude merges.
- **Git's own trailer parser works** for the strongest tier:
  `--pretty=format:%(trailers:key=Beads-Task,valueonly,separator=%x2C)`, verified against
  `Co-Authored-By`. No regex needed.
- **bd has no issue↔commit relation.** `bd link`/`bd dep add` is issue↔issue only;
  `--external-ref` is a single scalar. `bd hooks install` writes identity trailers *into*
  commit messages; it does not record commits against issues.

## Decision

**Every task↔commit edge carries the basis on which it was established, and only the two
bases that were *witnessed* are ever written.** This mirrors adr/005's rule that a cost
figure never travels alone: an edge without its basis is a claim without its evidence.

- **`declared`** — the commit message carries a `Beads-Task: <id>` trailer. Strongest,
  and **the only tier that can express true m:n**: a commit closing three tasks names
  three. Needs no claim at all. It is git's own trailer parser that decides this, not
  ours, which means the trailer must sit in the message's **final paragraph** —
  `Beads-Task:` followed by a blank line and then a `Co-Authored-By:` block is not a
  trailer at all, and git reports nothing. Found the hard way while verifying this ADR
  end-to-end: the commit fell back to `observed`, which was the correct answer to the
  message as written. Delegating the parse is still right — a regex of our own would
  have "helpfully" accepted a malformed message and disagreed with `git interpret-trailers`
  about what the message says.
- **`observed`** — HEAD moved during a Bash tool call in this session while task X was
  claimed. Deterministic at the moment of capture.
- **`inferred`** — the commit's timestamp falls inside a claim window. This is exactly
  what `audit._untracked_commits` already computes. Ambiguous, and therefore **never
  written**: it remains a proposal in a report.

That tiering is why this feature **respects adr/013 rather than superseding it.**
adr/013 refused to *write* what was only *inferred*, and that refusal stands untouched —
`fixable` on the `untracked-commits` gap is still hardcoded `False`, and the 133
historical commits keep their present treatment. Writing what was *observed* is a
different act, and this ADR exists partly to say so in those words, so that a later
reader does not mistake the new writes for a quiet reversal.

### One metadata key per commit, on the task issue

```
abacus_commit_<sha12>  =  <basis>:<session-id>:<epoch>
```

Constructed only in `attribution.build_commit_edges`, like every other `abacus_*` key.
Whitespace-free by construction, so it survives `beads._metadata_token`'s
whitespace-to-`_` collapse. Adding keys is a **minor** contract bump; `abacus_schema`
does not increment.

The warrant is the step an ADR usually skips: this repo's governing constraint is
**reversibility of a write to the store of record**, and per-key granularity is the only
option whose *unit of write equals the unit of the fact* — one edge, one key, undone by
one `--unset-metadata`. Every rejected option's unit of write is larger than one edge.
The accepted downside is that commit→tasks needs one `bd list --all --json` scan, which
the audit already performs once per run.

### Capture is a HEAD watermark, and writes nothing into the user's repository

`hooks/lib/commit_capture.capture` compares HEAD against a watermark held in the
disposable per-session state. Four callers share it: the `PostToolUse` Bash watcher when
a command contained a verb that could have moved HEAD, and `SessionStart`, `PreCompact`,
`Stop` and `SessionEnd` with no trigger at all. **The verb list is a cheap trigger, not
the correctness mechanism — the watermark is.** A commit made by a shell script, a
Makefile target, `gh pr merge`, or a verb this plugin has never heard of costs at most
one boundary's delay before a sweep collects it.

Three rails make `observed` honest:

1. **Seed, never attribute, on first sight.** No watermark means record HEAD and write
   nothing. Without it the first git command of a session would hang the repository's
   entire history on whatever task happens to be claimed. The single most important
   fail-safe here.
2. **A commit older than the claim cannot have been observed being made during it.**
   `commit.at >= claimed_at`, checked per commit and per tier. This is what makes
   `git pull` harmless: fifty upstream commits move HEAD and every one predates the
   claim.
3. **A move larger than `commits.max_per_boundary` (default 50) is not one boundary's
   work.** A rebase or a pull; record nothing, log, advance past it.

Verbs that move HEAD without creating anything — `checkout`, `switch`, `reset` — re-seed
and write nothing. The difference between two branches is not work this task did.

### No git hook, in this version

`core.hooksPath` is unset at all three scopes and is a *single global slot*: setting it
would silently disable the four real hooks in `~/projects/repos/cli/`, the only one of
170 repositories on this machine with non-sample hooks. `init.templateDir` affects only
repositories created afterwards. A per-repo write is the adr/012 class of action, which
this plugin permits in exactly one place and only inside declared roots. And decisively:
a git hook runs with **no Claude environment, so no session id** — the one thing capture
exists to record. `bd hooks install` is the documented route for anyone who wants one,
and its honest ceiling is "a commit happened".

### Cost per commit is derived at read time, never written

Equal share within the task, and no lines-weighted knob. What makes the figure honest is
not the weighting but **publishing the denominator**:

```
b0cff66  $0.20  (1 of 4 commits in abacus-7 · task total $0.8123 · apportioned-equally-within-task)
```

Where a commit carries edges to several tasks, the report shows the **set** of shares and
states plainly that per-commit costs do not sum to the repository total under m:n.
Omitted entirely — never `$0.00` — when the task's `abacus_cost_basis` is `unavailable`
(adr/005).

Scope is **forward capture only**.

## Consequences

### Positive
- The m:n relation is now recorded rather than reconstructed, and every edge names the
  evidence that produced it. A reader can tell an edge git itself vouched for from one
  this plugin inferred from a clock, because only the former exists.
- Each edge is individually reversible: one `bd --unset-metadata`. Nothing accumulates
  that cannot be undone at the granularity it was written.
- `git pull`, `git rebase`, `git checkout`, a failed commit and a commit in a repository
  the beads workspace does not govern all write nothing, and each for a structural
  reason rather than a special case. A failed commit is covered **by construction**: HEAD
  did not move, so there is nothing to diff — which is how the unwired
  `PostToolUseFailure` event stops mattering.
- The audit's `untracked-commits` set **narrows**: a commit carrying a written edge was
  tracked by a mechanism the window arithmetic cannot see. Nothing new is reported and
  nothing becomes fixable that was not.
- Nothing is written into any user repository — no git hook, no config value, no trailer.
  The only write is `abacus_*` metadata onto beads issues the user already has.

### Negative
- **A second terminal committing during a claim may be attributed to that claim.** The
  same limitation as the existing parallel-agent cost smearing, and `declared` overrides
  it when the user cares.
- **Server-side merges can never be observed by any local mechanism.** Three of this
  repository's ten commits are GitHub PR merges; they arrive on the next `pull`, older
  than the claim, so rail 2 correctly declines them. Correct and still a gap.
- **`observed` cannot distinguish work-in-a-commit from work-alongside-it.** The edge
  means "this commit landed while this task was claimed", and nothing stronger. Anyone
  building on it should not read more into it than that.
- Losing the state file loses one boundary's edges. Deliberate: rail 1 re-seeds and
  writes nothing rather than inventing the range it missed.
- The watcher's prefilter widens from `bd` to `bd`-or-`git`, roughly doubling the set of
  commands reaching `parse_events()`. Still pure string work, still ahead of any
  subprocess.

### Neutral
- An amend or a rebase orphans a recorded sha. The key is **left in place** and the
  report marks it `rewritten` when `git cat-file -e` fails. Deleting it would be a write
  based on inference; old keys are read, never deleted.
- `commits.*` is deliberately **outside** `consent.GOVERNING_KEYS` (adr/014). Capture
  writes only `abacus_*` metadata onto beads issues, which is the thing the acknowledged
  settings already permit; it creates no workspace, denies no tool call and reaches no
  remote. The consent gate still sits above every capture call site, as it does above
  every other write.
- **Event beads are recorded here as the upgrade path** if commit→task ever becomes a hot
  query. They win bidirectional queryability outright and lose reversibility and
  issue-list noise decisively, which is the trade-off to revisit and not a mistake to
  correct.

## Alternatives Considered

The full weighing — criteria stated before options, the assessment matrix, the
disconfirmation pass, calibrated confidence, the premortem and the Y-statements — is
archived at [analysis/015-commit-edges-are-observed-not-inferred.md](analysis/015-commit-edges-are-observed-not-inferred.md).
What follows is the summary; that file is the reasoning.

### Alternative 1: A packed `abacus_commits` list on the issue

Rejected on reversibility. Merging a new sha into one value needs a read-modify-write,
which adr/011's `carried_partial` already shows is error-prone in this codebase, and two
terminals committing concurrently would lose edges. The unit of write is the whole list
where the unit of the fact is one edge.

### Alternative 2: One event bead per commit

Rejected, as a trade-off rather than a defect. adr/013's finding is that *issue creation
is not reversible bookkeeping* — a property of `bd create`, unchanged by our evidence
being observed rather than inferred. 133 commits would mean 133 beads in the list the
user works in. Recorded above as the upgrade path.

### Alternative 3: The `bd kv` store

Rejected, and it should not have been shortlisted. kv lives inside the beads DB but is
*not an issue*, so choosing it requires amending adr/001. And it does not even win the
criterion it was shortlisted for: it **inverts** the queryability weakness
(commit→task direct, task→commit needs a scan) rather than removing it. An adr/001
amendment plus unverified mechanics for zero net gain.

### Alternative 4: Parse the sha out of the Bash command's stdout

Rejected on determinism, per the established facts above: `-q` suppresses the line, the
sha is abbreviated when present, and real commands are compound. Asking git costs one
`rev-parse` and cannot be wrong about what HEAD is.

### Alternative 5: A git `post-commit` hook

Rejected on all three of its available routes, and decisively on the fact that a git hook
has no Claude environment and therefore no session id. See the Decision above.

### Alternative 6: Lines-weighted apportionment of cost across commits

Rejected on merit, not on implementation cost. Neutralising the cost of `--numstat`
entirely still does not make it win: a lockfile, a generated file or a vendored directory
swamps the weighting, so the figure becomes *less* accurate while *looking* more precise.
That is adr/005's failure mode exactly — "a wrong answer wearing the costume of a
measurement".
