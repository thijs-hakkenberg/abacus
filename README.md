# abacus

A Claude Code plugin that makes every edit attributable to a tracked task, and
attaches what that task cost to the task itself.

Two things happen:

- **Enforcement.** `Edit`, `Write`, `NotebookEdit` and `MultiEdit` are denied when
  no [beads](https://github.com/steveyegge/beads) task is in progress. The denial
  names the commands that fix it. Enforcement is mechanical — a `PreToolUse` hook
  asking `bd` — so it costs no tokens until it fires, and it cannot be forgotten
  halfway through a long session the way a prompt-based rule can.
- **Attribution.** Claiming a task takes a [ccusage](https://github.com/ryoppippi/ccusage)
  reading; closing it takes another, diffs them, and writes the cost, tokens,
  duration and models onto the beads issue as `abacus_*` metadata. The issue is the
  store of record — there is no separate database.

```
$ /abacus:task-start "fix the retry backoff"
ab-14 in progress — fix the retry backoff

… work happens; edits are allowed and attributed …

$ /abacus:task-done
ab-14 closed
  cost estimate  $0.81
  tokens         334,512
  duration       23m
```

## Requirements

| | |
|---|---|
| `bd` | beads 1.1.2+ on `PATH`, and a `bd init`-ed workspace (or `$BEADS_DIR`) |
| `npx` | Node.js, for ccusage. Without it, tasks record a duration and `abacus_cost_basis=unavailable` |
| `python3` | 3.9+. Stdlib only, no venv, no pip install (adr/006) |

An OTEL collector writing `~/.claude/logs/claude-code-events.jsonl` is optional; it
adds tool-call counts when present and is omitted silently when not.

## Install

The repo is its own marketplace, so there is nothing to clone:

```bash
/plugin marketplace add thijs-hakkenberg/abacus
/plugin install abacus@abacus
```

Restart Claude Code, then confirm the hooks loaded with `claude --debug`.

To work on the plugin instead of just using it, add your own checkout as the
marketplace — same two commands, with a path in place of the shorthand:

```bash
/plugin marketplace add /path/to/your/clone
/plugin install abacus@abacus
```

Only one marketplace can hold the name `abacus` at a time, so adding a checkout
replaces the GitHub copy rather than sitting beside it. Re-add the shorthand to go
back. Either way `/plugin marketplace update abacus` pulls the latest catalogue.

## What it does, hook by hook

| Event | Script | Timeout | Can block? |
|---|---|---|---|
| `SessionStart` | `session_start.py` | 20s | no |
| `PreToolUse` (`Edit\|Write\|NotebookEdit\|MultiEdit`) | `gate_edits.py` | 10s | **yes — deny** |
| `PostToolUse` (`Bash`) | `watch_bd_commands.py` | 30s | no |
| `UserPromptSubmit` | `prompt_statusline.py` | 5s | no |
| `Stop` | `stop_reconcile.py` | 10s | no |
| `PreCompact` | `session_start.py --precompact` | 10s | no |
| `SessionEnd` | `session_end.py` | 60s | no |

`gate_edits.py` is the only script that can affect whether a tool runs. It still
exits 0 — the JSON on stdout is the decision (adr/002). Every other script runs
inside a guard that converts any unexpected exception into a silent exit 0, so the
plugin fails open by construction rather than by discipline.

### The gate's decision

It denies in exactly one case: a beads workspace exists, `bd` answered
successfully, and nothing is in progress. Everything else allows — `bd` missing,
`bd` broken, no workspace, malformed payload, unexpected exception. A gate that
blocks your edits because its own tooling broke is worse than no gate.

The source of truth is `bd list --status in_progress`, never the plugin's own
state, so a claim made in a second terminal or by a subagent opens the gate.

### Covering projects that have no workspace yet

Because the gate allows everything in a project with no `.beads/`, enforcement is
opt-in per repository — and the projects that most need tracking are the new ones.
`SessionStart` can close that gap by running `bd init` itself:

```json
{"auto_init": {"enabled": true}}
```

It is **off by default, and this is the only setting that writes to your
repositories.** When on, five independent rails each default to *no* (adr/012):

- the directory must be a **git root** (`.git` present in it, not in a parent);
- **never `$HOME`, never `/`** — a workspace there would capture every session
  beneath it. This rail runs in both directions: a `.beads/` found at `$HOME` is not
  read as a workspace either, because bd keeps an `eventsData/` sidecar there that
  holds no database, and treating it as one would report every repository on the
  machine as already tracked (adr/012 addendum). Set `$BEADS_DIR` if you actually
  want a home-level workspace;
- it must be **inside `auto_init.roots`** (default `["~/projects"]`; `[]` means any
  git repository). Containment compares realpaths, so `~/projects-old` is not a
  child of `~/projects`;
- a `roots` value that is not a list of paths initialises **nothing**. This is the
  one place the plugin fails *closed*: "I cannot tell what scope you meant" must
  narrow, not widen;
- **`PreCompact` never initialises** — it is mid-session, not the start of a
  project.

`--stealth` is the default, so `.beads/` lands in `.git/info/exclude` and `git
status` stays clean. Success is defined by a read-back — `bd init` exiting 0 is not
proof the embedded database opens, so if `bd list` cannot then resolve it, the
session behaves exactly as if there were no workspace. Costs ~3s, once per project.

### Commands and slash commands

- `/abacus:task-start <id-or-title>` — claim existing work, or create
  and claim new work
- `/abacus:task-done [id]` — close, which is what writes the cost
- `/abacus:status` — what is tracked, and whether an edit would be
  allowed
- `/cost-report` — what recent tasks cost, read back from the issues
- `/abacus:audit [fix]` — what is untracked or unattributed, and repair the
  metadata gaps

Or use `bd` directly; the plugin watches for `bd update <id> --claim` and
`bd close <id>` (and `--status in_progress` / `--status closed`) whichever way you
type them.

## What gets written

```
$ bd show ab-14 --json | jq '.[0].metadata'
{
  "abacus_schema": 1,
  "abacus_session_id": "8f3c…",
  "abacus_partial": false,
  "abacus_cost_basis": "ccusage-local-list-rate",
  "abacus_cost_usd_estimate": 0.8123,
  "abacus_tokens_total": 334512,
  "abacus_tokens_in": 9812,
  "abacus_tokens_out": 14543,
  "abacus_tokens_cache_read": 298114,
  "abacus_tokens_cache_write": 12043,
  "abacus_duration_min": 23,
  "abacus_models": "claude-fable-5"
}
```

Full field reference: `contracts/output/bd-metadata-write.md`.

### The cost figure is an estimate, and it says so

`abacus_cost_usd_estimate` is computed on your machine from a pinned local list-rate
pricing table. It is not billing and not account-level spend. It is always written
beside `abacus_cost_basis`, so the number cannot travel without its provenance
(adr/005).

**If ccusage cannot be read, no dollar figure is written at all** —
`abacus_cost_basis=unavailable`, no cost, no token counts, and the duration is still
recorded. A `$0.00` against a task that ran for an hour is a wrong answer wearing
the costume of a measurement; an absent key prompts a question instead. The same
applies to tool-call counts: a readable-but-empty OTEL log writes nothing rather
than a zero.

## Finding what the gate could not see

The gate stops an untracked `Edit`. It cannot stop a `sed -i` or a heredoc, it never
sees a commit made in another terminal, and it has nothing to say about a task closed
while ccusage was unreadable. Those gaps accumulate quietly, so there is something
that counts them:

```bash
/abacus:audit          # report
/abacus:audit fix      # report, and repair the metadata gaps
```

Behind it: `hooks/scripts/audit.py`, the `task-audit` skill, and an
`abacus-auditor` agent for the parts that need judgement. Not a hook — it runs only
when asked. One `bd list --all --json` plus one `git log`; no ccusage, because a
historical spend cannot be reconstructed. Five kinds of gap:

| kind | what it means | who fixes it |
|---|---|---|
| `unclaimed` | nothing is in progress, so edits are being denied right now | you, by claiming |
| `stale-claim` | a claim held past `audit.stale_after_h`, still accruing cost | needs a decision |
| `unfinalised` | closed, but its attribution is still marked partial | `--fix` |
| `unattributed` | closed with no `abacus_*` metadata at all | `--fix` |
| `untracked-commits` | commits outside every claim window | needs a decision |

**`--fix` writes the middle two and refuses the rest.** Closing a stale claim would
mark work done that is not done; creating issues for untracked commits is not
reversible bookkeeping. Both are judgements about intent, so they are reported with a
proposed command and left alone (adr/013).

**A repair is labelled as one.** Every write carries `abacus_backfilled=true`,
because a reconstruction after the fact is weaker evidence than a measurement taken
at the boundary. Where no measurement survived, the basis is `unavailable` with no
dollar figure — the same rule as everywhere else, never a `$0.00`. A figure that was
really measured is preserved, not overwritten. An issue attributed before the 0.3.0
rename, or declaring a schema this version does not know, receives no write at all.

Every ambiguous case reports **no gap**: an unreadable timestamp is not evidence of
staleness, and `--fix` runs unattended, so the audit is allowed to miss things and
not allowed to invent them. `ok: false` in the JSON means it could not look — a
failed read is never reported as a clean workspace.

## Configuration

`~/.claude/abacus/config.json`. Every key has a safe default, and a
missing or malformed file falls back to defaults entirely rather than breaking a
session.

| Key | Default | |
|---|---|---|
| `gate.enabled` | `true` | |
| `gate.non_beads_project` | `"warn"` | `warn` \| `off` \| `block`. Default is not `block`: a user-wide plugin must not make unrelated repos un-editable |
| `gate.cache_ttl_s` | `3` | How long an *allow* is memoised. Denies are never cached (adr/008) |
| `prime.mode` | `"compact"` | `compact` \| `full` \| `off`. `full` passes `bd prime`'s manual through verbatim, at ~10× the tokens (adr/009) |
| `auto_init.enabled` | `false` | Create a beads workspace in a git project that has none. **The only setting that writes to your repositories**, so it is off until asked for (adr/012) |
| `auto_init.roots` | `["~/projects"]` | Only these paths and their descendants. `[]` means every git repository; a value that is not a list of paths initialises *nothing* rather than everything |
| `auto_init.stealth` | `true` | `bd init --stealth`, so `.beads/` goes into `.git/info/exclude` and can never reach a commit |
| `ccusage_version` | `"ccusage@20.0.14"` | Pinned, never `@latest` — it carries the pricing table |
| `ccusage_timeout_s` | `25` | |
| `cache_ttl_s` | `30` | Snapshot cache. The closing read always bypasses it |
| `sync_on_session_end` | `"off"` | `push` \| `sync` \| `off`. Opt-in: reaching a remote as a session closes is not a default |
| `statusline` | `true` | The one-line `UserPromptSubmit` label |
| `otel_enrichment` | `true` | |
| `otel_events_path` | `~/.claude/logs/claude-code-events.jsonl` | |
| `state_max_age_days` | `14` | |

**Kill switch:** `export ABACUS_DISABLE=1` for the shell, or
`touch ~/.claude/abacus/disabled` for good. This is the documented
escape hatch and it is named in every denial message.

### Upgrading from `task-cost-tracker`

This plugin was called `task-cost-tracker` before 0.3.0, and both of the names it
left behind are still understood on **read**. Nothing is ever written under an old
name, so no migration step is needed and none is offered.

- A config still at `~/.claude/task-cost-tracker/config.json` is read when the new
  location has none, and only when `ABACUS_STATE_DIR` is unset. Move it when
  convenient. State is *not* carried over — it is disposable by design.
- Metadata written as `tct_*` is read as `abacus_*`, so a task left open across the
  upgrade still accumulates onto its earlier figures instead of reporting only the
  final session. Old keys are left in place rather than deleted; a closed task keeps
  the history of what it was measured with.

Test/override env vars: `ABACUS_CONFIG`, `ABACUS_STATE_DIR`, `ABACUS_CCUSAGE_CMD`,
`ABACUS_BD_CMD`, `ABACUS_CCUSAGE_TIMEOUT_S`, `ABACUS_CACHE_TTL_S`, `ABACUS_GATE_CACHE_TTL_S`,
`ABACUS_DEBUG` (re-raise instead of failing open).

## Known limitations

1. **Parallel-agent smearing.** ccusage totals are session-scoped, so concurrent
   subagent work is attributed to whichever task is current, and simultaneous
   multi-claim attributes to the most recent claim.
2. **Bash file writes bypass the gate** — `sed -i`, heredocs, `python -c`.
   Regex-gating Bash would false-positive constantly.
3. **Command parsing is an approximation of a shell, not a shell.** It handles
   chaining, env prefixes and quoting, but not variable expansion, `bash -c`, or
   aliases (adr/010). The gate's lazy snapshot and the Stop/SessionEnd repair
   passes cover what it misses.
4. **Pinned pricing goes stale.** Deliberate — bump-and-reconcile is the
   documented upgrade path (adr/003).
5. **Nothing checks that the specification artefacts are still true.**
   `tests/unit/test_spec_conformance.py` proves they agree with each other, not
   that they describe the current software (adr/007).
6. **`auto_init` cannot tell your repository from someone else's.** A clone you
   opened under a declared root is initialised like any other git project. Stealth
   keeps it out of commits; it does not prevent the act. Narrow your `roots`
   (adr/012).

## Development

```bash
python3 -m pytest tests/ -q     # 498 tests, ~3.5 min, no network
```

Fully offline. `bd` and `npx` are stubbed on `PATH` and record their argv; `HOME`
is sandboxed per test. No test touches a real beads database or spawns a real
`npx`. Hooks are driven as **real subprocesses** with JSON on stdin, because an
in-process call would miss an import error, a stray `print` corrupting the
envelope, or a non-zero exit that only appears under the real entrypoint.

The `features/` directory is executable: every scenario binds to a pytest-bdd step
definition in `tests/features/steps/` and drives a real hook. An unbound scenario
fails rather than skips.

## Repo layout

Four pillars plus a manifest, enforced by the test suite (adr/007):

```
spec.manifest.yaml    EA graph node
adr/                  why it is the way it is
features/             what it does, executably
contexts/             where its boundaries are
contracts/            how to talk to it
hooks/                lib/ + scripts/ + hooks.json
commands/  skills/  agents/  tests/
```

Start with `contexts/abacus-canvas.md` for the shape of the thing, or
`adr/002` for the one decision everything else follows from.
