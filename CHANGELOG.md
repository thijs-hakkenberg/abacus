# Changelog

All notable changes to this plugin are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this plugin adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Contract-level
versioning is tracked separately in each file under `contracts/`.

## [Unreleased]

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

