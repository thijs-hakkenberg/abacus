# Changelog

All notable changes to this plugin are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this plugin adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Contract-level
versioning is tracked separately in each file under `contracts/`.

## [Unreleased]

## [0.6.0] — 2026-09-03

Commits enter the model. abacus could already say what a task cost; it could not say
which commits that task produced, in which session, or what any single commit cost.
The relation is genuinely m:n — one commit can complete several tasks, one task spans
many commits — and it was stored nowhere. See adr/015, and
`adr/analysis/015-commit-edges-are-observed-not-inferred.md` for the weighing behind it.

Nothing is written into your repository to make this work: no git hook, no
`core.hooksPath`, no trailer added to your messages. A git hook would also run with no
Claude environment and therefore no session id — the one thing capture exists to record.

### Added

- **Task↔commit edges on the beads issue**, one metadata key per commit:
  `abacus_commit_<sha12>` = `<basis>:<session-id>:<commit-epoch>`. One key per edge, so
  withdrawing one is one `bd update --unset-metadata` and does exactly one edge's worth
  of damage. Verified against bd 1.1.2 at 200 keys on a single issue
  (`tests/integration/test_bd_metadata_ceiling.py`, opt-in via `ABACUS_REAL_BD_TESTS=1`).
- **Two bases, both witnessed.** `declared` — git itself parsed a `Beads-Task: <id>`
  trailer out of the message, the only tier that expresses true m:n and the only one
  needing no claim. `observed` — HEAD moved during this session while that task was
  claimed. A third tier, `inferred` (a timestamp inside a claim window), is **never
  written**: it stays what it already was, a proposal in an audit report. That is how
  this extends adr/013 rather than reversing it — refusing to write what was *inferred*
  says nothing against writing what was *observed*.
- **Capture by HEAD watermark**, in `hooks/lib/commit_capture.py`, shared by the Bash
  watcher and the `SessionStart`, `PreCompact`, `Stop` and `SessionEnd` sweeps. The
  watcher's verb list (`commit merge rebase cherry-pick revert am apply pull`, plus
  `checkout switch reset` to re-mark only) is a cheap trigger, **not** the correctness
  mechanism — the watermark is. An unrecognised verb costs at most one boundary's delay,
  because the next sweep diffs the same watermark and finds the same commits. A commit
  is recorded once, not once per boundary.
- **Three rails that keep `observed` honest.** Seed and attribute nothing on first sight
  of a repository — otherwise the session's first git command would hang the entire
  history on whatever task is claimed. Require `commit.at >= claimed_at`, which is what
  makes `git pull` write nothing rather than fifty edges. And cap the move at
  `commits.max_per_boundary` (default 50): a HEAD move larger than that is a rebase, not
  an afternoon's work.
- **New git reads** in `hooks/lib/gitlog.py` — `repo_root`, `head`, `new_commits`. All
  read-only, all 5s-bounded, all returning `[]`/`None` on every failure including "not a
  repo". `new_commits` deliberately **includes merges**: a squash-merge commit is the
  work. Trailers come from git's own `%(trailers:key=…)` atom, not a regex.
- **Cost per commit, derived at read time and never stored** — equal share within the
  task, with the denominator always printed beside it
  (`1 of 4 commits in abacus-7 · task total $0.8123 · apportioned-equally-within-task`).
  Storing nothing means the apportionment can change with no data to migrate; printing
  the denominator lets you see it and disagree. Omitted entirely, never zeroed, when the
  task's `abacus_cost_basis` is `unavailable`. Lines-weighted apportionment was rejected
  on merit rather than cost: a lockfile swamps the weighting, so the figure becomes less
  accurate while looking more precise.
- **`commits.enabled`** (default `true`), **`commits.max_per_boundary`** (50) and
  **`commits.trailer_key`** (`Beads-Task`) in `abacus.config.json`. Deliberately **not**
  governing settings: capture writes only to issues you already claimed, so it does not
  widen what adr/014's acknowledgement covers. Capture still runs behind that
  acknowledgement like every other write.
- **`adr/analysis/`** — one companion per ADR recording the weighing an ADR compresses
  to a paragraph: criteria stated before options, the option eliminated and by which
  argument, calibrated confidence, the premortem. Three new conformance assertions keep
  them honest (naming, no orphan analysis, and the parent ADR must link to its
  companion), all of which were confirmed RED first. adr/007 gains a dated addendum:
  the "why" pillar now has a subdivision, and the four-pillar count is unchanged.

### Changed

- **`/abacus:audit` reports fewer untracked commits.** A commit carrying a written edge
  is no longer a gap, because it was tracked by a mechanism the window arithmetic cannot
  see. A narrowing only: no new `kind`, and `untracked-commits` is still hardcoded
  unfixable — the repair is `bd create`, which is not reversible bookkeeping.
  `contracts/output/audit-report.md` → 1.1.0.
- The Bash watcher's prefilter widens from `bd` to `bd` **or** `git`. Still pure string
  work ahead of any subprocess. `contracts/input/post-tool-use-bash.md` → 1.1.0, which
  also documents `isImage` and `noOutputExpected` — observed live, undocumented
  upstream, and still deliberately unread: `-q` suppresses git's sha line and real
  commands are compound, so asking git what HEAD is beats parsing what it printed.
- `contracts/output/bd-metadata-write.md` → 1.1.0. Additive: every 1.0.0 key keeps its
  meaning and `abacus_schema` stays at `1`.
- **`git` is now stubbed on `PATH`** in the test suite, alongside `bd` and `npx`, and
  `make_real_git_project()` drives a real local `git init` for `tests/unit/test_gitlog.py`
  — which also closes a pre-existing gap, since `recent_commits` had no tests at all.
  The suite remains fully offline.

### Known limitations

- A second terminal committing during a claim may be attributed to that claim. A
  `declared` trailer overrides it. This sits alongside the existing parallel-agent
  smearing.
- Server-side merges (a GitHub PR merged in the browser) can never be observed by any
  local mechanism. They arrive on the next `pull`, older than the claim, and rail 2
  correctly declines them.
- `observed` cannot distinguish work in a commit from work alongside it.
- Existing untracked commits are not reconciled. Capture is forward-only; the historical
  gap stays exactly what it was — reported by `/abacus:audit`, never written.
- An amend or rebase orphans a recorded sha. The key is **left in place** and a reader
  marks it `rewritten`; deleting it would be a write based on inference.

## [0.5.0] — 2026-09-01

Being installed is no longer agreement. Until the settings that govern abacus's
behaviour have been acknowledged, it performs **no write and no denial** — it reads,
it says what it would do, and it does nothing. See adr/014.

### Added

- **A consent precondition on every unprompted action.** Six hooks now check
  `consent.is_acknowledged()` above anything that acts: the gate before any denial
  (and before it spawns `bd` or `npx` at all), `session_start.py` before `auto_init`
  writes into a repository, the watcher and the Stop reconciler before any
  `--set-metadata`, and `session_end.py` before both the partial write and
  `bd dolt push`. An unacknowledged install denies nothing and writes nothing.
- **`/abacus:acknowledge`**, backed by `hooks/scripts/acknowledge.py`
  (`--show` | `--accept` | `--revoke` | `--json`). `--show` is the default, so a bare
  invocation records nothing: consent that can be given by mistyping is not consent.
- **A notice at the two earliest surfaces after an install** — the first
  `SessionStart`, and `UserPromptSubmit` for a plugin installed mid-session. It is
  rendered from the live config, so it names the actual roots, the actual non-beads
  mode and whether a remote would be reached. Emitted at most once per session, and
  never when `ABACUS_DISABLE=1` — someone who set the kill switch has already
  answered. `consent.notice()` returns `""` once acknowledged, so the steady-state
  token cost of this feature is zero.
- **Re-asks when the governing settings change, and only then.** The record carries a
  sha256 fingerprint of six values: `gate.enabled`, `gate.non_beads_project`,
  `auto_init.enabled`, `auto_init.roots`, `auto_init.stealth`,
  `sync_on_session_end`. Widening `roots` from `~/projects` to `[]` pauses governance
  until the wider scope is agreed to separately; bumping `ccusage_version` or toggling
  `statusline` does not, because a notice that fires for cosmetic churn is one that
  gets dismissed unread. `roots` compares as a sorted set — reordering grants nothing.
- `contracts/output/consent-notice.md` (1.0.0), `adr/014`,
  `features/consent-acknowledgement.feature` (21 scenarios), and a `consent-notice`
  entry in `spec.manifest.yaml`'s `interfaces.outbound[]`.

### Changed

- **Consent gates *unprompted* action only.** `/abacus:audit fix` still repairs and
  `/abacus:task-start` still claims — those are the user acting. Gating them would
  make the notice self-defeating, since it names `/abacus:status` as the way to
  inspect before agreeing.
- This is the second path in the plugin that deliberately fails **closed**, after
  `auto_init` (adr/012 rail 5). A missing, corrupt, non-object, fingerprint-less or
  future-schema record all read as never acknowledged: "I cannot tell whether you
  agreed" must never resolve to "yes".

### Upgrade note

**Existing installs stop governing until `/abacus:acknowledge` is run.** There is no
record on disk yet, so the gate allows every edit and no attribution is written. This
is deliberate — the alternative is inferring agreement from an upgrade nobody was
asked about — but it does mean tracking silently pauses until the command is run once
per machine.

## [0.4.1] — 2026-09-01

### Fixed

- **`auto_init` could never fire on a machine where `~/.beads` exists — which is any
  machine that has run bd.** `beads.has_workspace()` walked upward to `/` and
  returned true at the first directory containing a `.beads/`. bd keeps an
  `eventsData/` sidecar in `~/.beads` that holds no database (`bd list` from `$HOME`
  answers *no beads database found*), and that was enough to satisfy the walk. Since
  `auto_init` fires only for a project that has none, a correctly configured
  `auto_init.enabled: true` with `roots: ["~/projects"]` initialised nothing, ever,
  and logged nothing — because nothing was attempted. Observed 2026-09-01 on a fresh
  clone under `~/projects`: every one of adr/012's five rails passed when queried
  directly; the code was simply unreachable.

  `has_workspace()` now stops before `$HOME` and `/`, the same two directories
  adr/012 rail 2 already refuses to *write* a workspace into, for the same stated
  reason — one there captures every session beneath it. A `.beads/` at any other
  level, including an intermediate ancestor of the cwd, is detected exactly as
  before, and `$BEADS_DIR` still short-circuits the walk, so a home-level workspace
  remains possible when it is asked for explicitly rather than inferred from bd's
  own bookkeeping. See the adr/012 addendum.

  Consequence worth naming: anyone who deliberately ran `bd init` in `$HOME` now
  needs `$BEADS_DIR` set for the plugin to see it.

## [0.4.0] — 2026-08-31

Adds an audit: something that can be pointed at a workspace and asked *is anything
untracked right now?* The gate stops an untracked edit; it never had an answer for a
file written by `sed -i`, a commit made in another terminal, or a task closed while
ccusage was unreadable. Those gaps were accumulating with no way to count them.

The seven hooks are unchanged. Nothing here runs on an event — the audit runs only
when asked.

### Added

- **`hooks/scripts/audit.py`** — `audit.py [--json] [--fix] [--stale-after-h N]
  [--since "<git date>"]`. Two subprocesses at most: one `bd list --all --json` for
  the whole workspace (closed issues and their metadata come back in that one call)
  and one `git log`, skipped when there is no `.git`. No `npx`, no ccusage: a
  historical spend cannot be reconstructed, so it is not attempted. Exits 0 whatever
  it finds, including for an unrecognised flag — it runs inside an agent turn, where
  a non-zero exit reads as a broken tool call.
- **`hooks/lib/audit.py`** — five detectors, pure functions over the issue list plus
  a commit list. `unclaimed` (nothing in progress, so edits are being denied right
  now), `stale-claim`, `unfinalised`, `unattributed`, `untracked-commits`. Every
  ambiguous case reports **no gap**: an unreadable timestamp is not evidence of
  staleness, an unrecognised `abacus_schema` is not evidence of missing attribution,
  a commit that cannot be placed in time is not evidence of untracked work.
- **`attribution.backfill_metadata()`** — the audit's write, added beside
  `build_metadata()` rather than assembled in a skill. `abacus_*` keys are still
  constructed in exactly one module.
- **`abacus_backfilled=true`** on every write `--fix` makes. A reconstruction after
  the fact is weaker evidence than a measurement taken at the boundary, and without
  this key a reader averages the two together. Documented in
  `contracts/output/audit-report.md`.
- **`hooks/lib/gitlog.py`** — a 30-day `git log --no-merges` reader. Timestamps are
  read as `%ct` (unix epoch) rather than `%cI`, because a `+02:00` offset would fail
  to parse and every commit would silently become unplaceable in time.
- **`/abacus:audit`** command, the **`task-audit`** skill, and the
  **`abacus-auditor`** agent. The script writes; the prose does not, and says so.
- `audit.stale_after_h` (default 24) and `audit.commit_window` (default
  `"30 days ago"`) in config.
- `contracts/output/audit-report.md`, an `audit-report` outbound interface in
  `spec.manifest.yaml`, `adr/013`, and `features/task-audit.feature` — 16 scenarios,
  all bound and executing.

### Notes on what `--fix` will not do

`--fix` writes `unattributed` and `unfinalised` only. A **stale claim** is reported
and never written: closing it would mark work done that is not done, finalising it
would bank a figure against a task still running. **Untracked commits** are reported
with their shas and never written: the repair is `bd create`, which is not
reversible bookkeeping. Both are judgements about intent (adr/013).

Three rules the write inherits. A cost is never invented — no `abacus_session_id`
means no ccusage reading to recover, so the basis is `unavailable` with **no** dollar
figure and no token counts, not `0`. A cost is never discarded — an issue that
already banked a real figure keeps it. A duration is omitted when its timestamps
cannot be parsed, because `minutes_between` answers `0` for an unparsable date and a
zero-minute task that ran all afternoon is the same lie as a zero-dollar one.

An issue attributed before the 0.3.0 rename (`tct_*`) and an issue declaring an
`abacus_schema` this version does not know both receive **no write at all**. Reading
`tct_*` as absent would make every pre-0.3.0 issue look unattributed and `--fix`
would overwrite real recorded figures with `unavailable` — the audit destroying the
data it exists to protect. Both cases are pinned by tests.

### Testing

47 new unit tests (`test_audit.py` for the detectors, `test_audit_cli.py` driving
the script as a real subprocess and asserting on the recorded `bd` argv) plus 16
feature scenarios. `git` is not stubbed on `PATH`, so `gitlog` checks for `.git`
before spawning anything and the suite stays hermetic.

## [0.3.1] — 2026-08-30

Makes the repository installable as a marketplace in its own right. No code
changes — the gate, the attribution engine and the five observers are byte for byte
what 0.3.0 shipped.

### Changed

- **The marketplace is named `abacus`, not `abacus-local`.** The name is public —
  it is the right-hand side of `/plugin install abacus@abacus` — and asserting
  *local* was only ever true while the catalogue was added from one machine's disk.
  Published from GitHub it misdescribes itself, and every install instruction
  written against it misleads. **This changes the install identifier**: an existing
  install registered as `abacus@abacus-local` has to be re-added, because Claude
  Code keys marketplaces by name and cannot follow a marketplace rename (`renames`
  covers plugin names only).
- The install section of the README now leads with
  `/plugin marketplace add thijs-hakkenberg/abacus`, with the local-checkout path
  kept as the development route rather than the only route.

### Added

- `homepage`, `repository`, `license`, `category` and `keywords` on the plugin
  entry and in `plugin.json`, so the entry is not anonymous in `/plugin`'s browser.
- 4 conformance tests over the publication surface, 3 of them guarding a gap
  between two files rather than the contents of one: the marketplace name matches
  the repository that publishes it; every plugin `source` is a relative path that
  cannot escape the marketplace root (an absolute path resolves on the author's
  machine and nowhere else, a failure that never shows up locally); the entry names
  the public repository and licence; and **the README's two copy-pasteable install
  lines are derived from the JSON**, so a future rename cannot quietly orphan the
  first two commands a stranger runs.

## [0.3.0] — 2026-08-30

Renames the plugin to **abacus** and prepares it for public release. No behaviour
changes beyond the two compatibility shims below: the gate, the attribution engine
and the five observers are untouched.

### Changed

- **Renamed `task-cost-tracker` to `abacus`.** Every identifier moves with it: the
  plugin and marketplace name, the slash-command namespace (`/abacus:task-start`,
  `/abacus:task-done`, `/abacus:status`), the metadata prefix (`tct_*` →
  `abacus_*`), the env vars (`TCT_*` → `ABACUS_*`, `TASK_COST_TRACKER_DISABLE` →
  `ABACUS_DISABLE`), the state and config directory
  (`~/.claude/task-cost-tracker/` → `~/.claude/abacus/`), and two library modules
  (`tct_config.py` → `abacus_config.py`, `tct_time.py` → `abacus_time.py`).
- **Licensed MIT**, and the specification artefacts no longer reference any
  internal standard, tool or identifier. `adr/007` is rewritten as *why this repo
  keeps four pillars and a manifest* — the layout is self-imposed and
  self-enforced, with no external authority, which is now stated plainly because it
  bounds what a conformance pass means.

### Added

- **Two read-side compatibility shims, so an upgrade loses nothing.** Both are read
  only; nothing is ever written under an old name.
  - A config left in `~/.claude/task-cost-tracker/config.json` is still read, but
    only when the current location has no config of its own and `ABACUS_STATE_DIR`
    is unset. An explicit `path=` argument is never second-guessed. State is
    disposable and is *not* migrated; config is a user's stated intent, and someone
    who set `gate.non_beads_project: "block"` and then upgraded would otherwise
    silently fall back to `warn` — an enforcement regression that looks exactly
    like the plugin working.
  - Metadata written under the `tct_` prefix is normalised on read in
    `attribution.carried_partial()`, the single reader of previously-written
    figures. Without it, a task left `tct_partial=true` across the upgrade would be
    orphaned: its accumulated spend invisible, and the closing write reporting only
    the final session. Precedence is deliberate — when an issue carries both
    `abacus_partial` and `tct_partial`, the current key decides, because a task
    finalised after the upgrade keeps the stale legacy flag alongside the new one
    (`--set-metadata` merges) and an unconditional fallback would make every
    subsequent close add to a figure already banked.
- 8 unit tests for the two shims, 6 of them pinning a boundary rather than the
  happy path.
- `.beads/` and `.harness/` are now in `.gitignore` rather than only in
  `.git/info/exclude`, which does not travel to a clone.

## [0.2.0] — 2026-08-30

Closes the largest coverage gap found by using the plugin: projects with no beads
workspace were never enforced, and multi-line Bash commands were never parsed.

### Added

- **Automatic workspace initialisation**, `auto_init.enabled` (default `false`).
  `SessionStart` runs `bd init --stealth --non-interactive` in a git project that
  has none, so enforcement no longer has to be turned on one repository at a time —
  the manual backfill this replaced took nine `bd init` runs by hand. Five rails,
  each defaulting to *no*: a git root only (`.git` present in the directory, tested
  with `exists` because inside a worktree it is a file); never `$HOME` or `/`;
  inside `auto_init.roots` (default `["~/projects"]`, realpath comparison so
  `~/projects-old` is not a child of `~/projects`); nothing at all if `roots` cannot
  be read as a list of paths; and never on `PreCompact`. Success is defined by a
  `bd list` read-back rather than `bd init`'s exit code, because bd embeds Dolt and
  a database that will not open surfaces on the first *read* (`adr/012`).
- `auto_init.stealth` (default `true`) — `.beads/`, `.beads-credential-key` and
  `.beads/proxieddb/` go into `.git/info/exclude`, so a workspace the plugin created
  unprompted cannot reach a commit. Verified: `git status --porcelain` stays empty.
- `beads.init()`, and `env_extra` on the `bd` subprocess wrapper so
  `BD_NON_INTERACTIVE=1` can accompany `--non-interactive` — bd prompts for an actor
  role when it thinks a human is present, and a prompt inside a hook blocks until
  the event timeout expires.
- `features/workspace-auto-init.feature` (7 scenarios) and 20 unit tests, 16 of them
  asserting that a rail holds. Manifest gains the `bd-workspace-init` outbound
  interface and a `git` dependency.

### Fixed

- **Multi-line Bash commands were invisible to the attribution engine.** `"\n"` was
  listed as a segment separator but `whitespace_split` makes `shlex` treat newline as
  whitespace, so it is never emitted as a token and the entry had no effect. `cd
  dir` on one line and `bd close <id>` on the next parsed as a single `cd`, losing
  the close — the most ordinary multi-step shape an agent writes, and the confirmed
  cause of a task staying `abacus_partial=true` after being closed in an observed
  session. Lines are now reassembled into logical commands before tokenising:
  joined across a trailing `\`, joined forward through an unterminated quote, and
  heredoc bodies skipped to their delimiter (`<<EOF`, `<<-EOF`, `<<'EOF'`,
  `<<"EOF"`, while not matching `<<<` or `2>&1`) so text being written to a file
  cannot fire a boundary (`adr/010` addendum).
- **A line continuation could claim a task named `"\n"`.** `shlex` renders an escaped
  newline as a whitespace-only token, which `_first_positional` then returned as the
  issue id, so `bd update \` + newline + `<id> --claim` set the current task to a
  newline and the following close matched nothing. Whitespace-only tokens are now
  dropped: a token that is only whitespace is never an argument.

### Changed

- `test_spec_conformance.py` discovers step modules by glob instead of importing a
  hardcoded three, so a new step module cannot make a bound feature look unbound.

## [0.1.0] — 2026-08-06

First release. Enforces that work is tracked as a beads task, and attaches what
each task cost to the task itself.

### Added

- **Edit enforcement.** A `PreToolUse` hook denies `Edit`, `Write`, `NotebookEdit`
  and `MultiEdit` when no beads task is in progress, with a denial message naming
  the two commands that fix it and the bypass env var. Enforcement is mechanical,
  so it costs no tokens until it fires. The gate denies in exactly one case — a
  beads workspace exists, `bd` answered, and nothing is in progress — and allows on
  every other path including `bd` missing, `bd` broken, and unexpected exceptions
  (`adr/002`).
- **Per-task cost attribution.** A `PostToolUse` watcher on `Bash` detects
  `bd update <id> --claim` and `bd close <id>` (and the `--status` equivalents),
  takes a ccusage snapshot at the claim, and diffs at the close. Validated under
  subagent fan-out: ccusage deduplicated 251 raw usage lines to the 88 unique
  `(message.id, requestId)` pairs across main and subagent transcripts, so
  snapshot-diffing is correct even when work fans out (`adr/003`).
- **`abacus_*` metadata written onto the beads issue** — cost estimate, basis, five
  token counts, duration, models, tool calls, session id, partial flag and schema
  version. The issue is the store of record; there is no separate database
  (`adr/001`, `contracts/output/bd-metadata-write.md`).
- **Repair passes.** `Stop` and `SessionEnd` finalise a task the watcher did not
  observe closing, and the gate takes a lazy snapshot when it sees an in-progress
  task the session has no baseline for. A session that ends with a task still open
  records `abacus_partial=true`, and a later close accumulates onto it (`adr/011`).
- **Session priming.** `SessionStart` and `PreCompact` inject a ~450-character
  primer rather than passing `bd prime`'s 4,854-character manual through, which is
  the plugin's only token cost. `prime.mode: full` restores the manual verbatim,
  and priming defers entirely when a `beads@*` plugin is enabled (`adr/009`).
- **Commands and skill** — `/abacus:task-start`,
  `/abacus:task-done`, `/abacus:status`, and `/cost-report`.
- **Optional OTEL enrichment** from `~/.claude/logs/claude-code-events.jsonl`,
  adding tool-call counts when the log is present and omitting them silently
  otherwise.
- **Optional beads sync** on session end (`sync_on_session_end: push|sync|off`,
  default `off` — reaching a remote as a session closes is opt-in).
- **Kill switch** — `ABACUS_DISABLE=1`, or a `disabled` marker file.
  Named in every denial message.
- **Specification artefacts** — eleven ADRs, six executable feature files, a
  bounded-context canvas, a D2 context map, ten interface contracts, and a root
  `spec.manifest.yaml` EA-graph node, in the four-pillar layout `adr/007` describes.
- **373 offline tests** — 298 unit (70 of them spec conformance), 11
  integration, and 64 BDD scenarios. Hooks are driven as real subprocesses with
  JSON on stdin; `bd` and `npx` are stubbed on `PATH` and record their argv; `HOME`
  is sandboxed per test. Every `.feature` scenario binds to a step definition and
  drives a real hook — an unbound scenario fails rather than skips, and the
  conformance suite fails if a feature file is never passed to `scenarios()`.

### Notes on deliberate behaviour

These are decisions, not gaps, and each is recorded in an ADR:

- **A cost that cannot be read is omitted, never zeroed.** When ccusage is
  unreadable, `abacus_cost_basis=unavailable` is written with no dollar figure and no
  token counts; the duration is still recorded. A `$0.00` against an hour of work
  is a wrong answer wearing the costume of a measurement (`adr/005`).
- **The cost figure is a local list-rate estimate, not billing.** It is always
  written beside its basis so the number cannot travel without its provenance
  (`adr/005`).
- **`gate_edits.py` breaks the always-exit-0 convention its six siblings keep** —
  deliberately, since a gate that always allows gates nothing (`adr/002`).
- **Allows are cached for 3 seconds; denies never are.** A stale allow costs one
  unattributed edit; a stale deny blocks work the user has already unblocked
  (`adr/008`).
- **No MCP server.** `bd` is already a CLI with `--json`; a second reader would be
  redundant and would require a venv (`adr/004`).
- **ccusage is pinned to `ccusage@20.0.14`.** The version carries the pricing
  table, so `@latest` would silently change historical comparability (`adr/003`).

### Known limitations

1. Parallel-agent smearing — ccusage totals are session-scoped, so concurrent
   subagent work is attributed to the current task, and simultaneous multi-claim to
   the most recent claim.
2. Bash file writes bypass the gate (`sed -i`, heredocs, `python -c`). Regex-gating
   Bash would false-positive constantly.
3. Command parsing approximates a shell rather than being one: chaining, env
   prefixes and quoting are handled; variable expansion, `bash -c` and aliases are
   not (`adr/010`). The lazy snapshot and repair passes cover the gap.
4. Pinned pricing goes stale by design; bump-and-reconcile is the upgrade path.
5. Nothing verifies that the specification artefacts are still true.
   `tests/unit/test_spec_conformance.py` catches drift *between* them — a renamed
   directory, a timeout changed on one side only — but every ADR could be obsolete
   at once and the suite would stay green (`adr/007`).

[Unreleased]: https://github.com/thijs-hakkenberg/abacus/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/thijs-hakkenberg/abacus/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/thijs-hakkenberg/abacus/releases/tag/v0.3.0

<!-- 0.2.0 and 0.1.0 have no compare links: this repository starts from a single
     squashed commit, so the tags those releases would point at do not exist here.
     The entries stay because what changed is still worth knowing. -->

