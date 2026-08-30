# abacus

Claude Code plugin. Denies `Edit`/`Write`/`NotebookEdit`/`MultiEdit` when no beads
task is in progress, and writes per-task cost, token, duration and model
attribution onto the beads issue itself as `abacus_*` metadata. Read
`adr/002-gate-breaks-the-exit-zero-contract.md` before touching the gate, and
`adr/001-beads-as-task-store-of-record.md` before adding any storage.

## Architecture

**Seven hooks, no MCP server, no database.** The beads issue is the store of record
(adr/001); `bd` is already a CLI with `--json`, so a second reader would be
redundant and would drag in a venv (adr/004).

- **`hooks/scripts/gate_edits.py`** (`PreToolUse`) is the only script that can
  affect whether a tool runs. It emits a `permissionDecision` on stdout and still
  exits 0. It denies in **exactly one** case: a beads workspace exists, `bd`
  answered successfully, and nothing is in progress. `bd` missing, `bd` broken, no
  workspace, malformed payload, or an unexpected exception all **allow**.
- **`hooks/scripts/watch_bd_commands.py`** (`PostToolUse` on `Bash`) is the
  attribution engine. It tokenises the observed command, and on a claim takes a
  ccusage snapshot, on a close diffs against it and writes the metadata.
- **The other five** (`session_start.py`, also wired to `PreCompact` via
  `--precompact`; `prompt_statusline.py`; `stop_reconcile.py`; `session_end.py`)
  observe and repair. None can block.

The gate's source of truth is `bd list --status in_progress`, **never plugin
state** — a claim from another terminal or a subagent must open the gate. The
per-session state file exists only for cost attribution: which task is current,
and the snapshot taken when it was claimed.

`hooks/lib/attribution.py` is the single place `abacus_*` keys are constructed. Do
not build that dict anywhere else, including in a command or skill.

## Constraints when working on this plugin

- **`gate_edits.py` is the only script permitted to influence tool execution, and
  even it exits 0.** Every other script wraps its body in a guard that converts any
  exception into a silent exit 0. When adding a code path, the question is not
  "does this work" but "what does this do when `bd` is a broken symlink" — the
  answer must be *allow and stay quiet*.
- **Fail open, always.** A gate that blocks edits because its own tooling broke is
  worse than no gate. New failure modes default to allow. `ABACUS_DEBUG=1` re-raises,
  and is the only way to see a swallowed exception.
- **`auto_init` is the one exception, and it fails closed.** It is the only code
  path that writes to the user's repository, so an `auto_init.roots` value that
  cannot be read as a list of paths initialises **nothing** — `auto_init_roots()`
  returns `None`, deliberately distinct from `[]`. Widening the scope because a
  config value was unreadable is the failure mode adr/012 exists to prevent. Its
  other four rails (git root only, never `$HOME`/`/`, inside `roots`, never on
  `PreCompact`) are structural and hold whatever the config says. Success is a
  `bd list` **read-back**, not `bd init`'s exit code — bd embeds Dolt and a
  broken database surfaces on the first read.
- **Every subprocess call sets `stdin=subprocess.DEVNULL` explicitly** and resolves
  its executable with `shutil.which()` first. Both are hazards learned the hard
  way: an unset stdin inherits a live pipe and hangs forever; a bare `"npx"` fails
  on Windows because `CreateProcess` does not consult `PATHEXT`.
- **Never write a zero where a value could not be read.** `abacus_cost_usd_estimate`
  is written *iff* `abacus_cost_basis == "ccusage-local-list-rate"`; when ccusage is
  unreadable the key is **omitted** and the basis is `unavailable`. A `$0.00`
  against an hour of work is a wrong answer wearing the costume of a measurement.
  The same rule governs `abacus_tool_calls` from OTEL. See adr/005 and
  `contracts/output/bd-metadata-write.md`.
- **A cost figure never travels alone.** `abacus_cost_basis` accompanies every cost.
  Any new consumer must label the figure an estimate; a predecessor tool had to
  withdraw its bare dollar figures for exactly this reason (adr/005).
- **Pin `ccusage`, never `@latest`.** The version carries the pricing table, so
  floating it would silently change historical comparability. Bump-and-reconcile is
  the documented upgrade path (adr/003).
- **stdlib only, Python 3.9.6.** No venv, no `pip install`, no `match`, no `X | Y`
  runtime annotations, no `tomllib` (adr/006). `pyyaml` is used *only* in tests.
- **Denies are never cached; allows are cached for 3s.** The asymmetry is the point
  — a stale allow costs at most one unattributed edit, a stale deny blocks work the
  user has already unblocked (adr/008).
- **Do not name a directory `mcp/`.** It collides with the installed SDK.
- **The state file is disposable.** Anything that cannot be reconstructed from beads
  or ccusage does not belong in it.

## Repo layout

Four pillars plus a manifest, self-imposed and self-enforced (adr/007 records why):

| Path | Pillar |
|---|---|
| `spec.manifest.yaml` (root, **not** under `contracts/`) | 0 · EA graph node |
| `adr/NNN-lowercase-title.md` | 1 · why |
| `features/*.feature` | 2 · what, executably |
| `contexts/{abacus-canvas.md,context-map.d2}` | 3 · boundaries |
| `contracts/{input,output}/*.md` | 4 · interfaces |

**Only `README.md`, `CLAUDE.md`, `CHANGELOG.md` and `adr/*.md` are permitted as
loose markdown.** A design note goes in an ADR or a contract, not a new top-level
file. When an ADR is superseded, add an addendum rather than rewriting it — the
reasoning behind a reversed decision is most of an ADR's value.

`spec.manifest.yaml`'s `interfaces.inbound[]` must stay in step with
`hooks/hooks.json`: one entry per event, and each `sla.latency_p99_ms` mirrors that
event's timeout × 1000. `tests/unit/test_spec_conformance.py` enforces this. Note
what it cannot do: it checks the artefacts agree with **each other**, never that
they still describe the software — all twelve ADRs could be obsolete and the suite
would stay green (adr/007).

## Key files

| File | Role |
|---|---|
| `hooks/hooks.json` | The seven event wirings, matchers and timeouts. Changing a timeout means changing the matching contract's SLA. |
| `hooks/scripts/gate_edits.py` | The gate. The six-step decision ladder. |
| `hooks/scripts/watch_bd_commands.py` | Claim/close detection and attribution. |
| `hooks/lib/attribution.py` | The only constructor of `abacus_*` keys. |
| `hooks/lib/ccusage.py` | Pinned `npx ccusage` wrapper, 30s snapshot cache. The closing read must pass `fresh=True`. |
| `hooks/lib/beads.py` | `bd` wrapper. `bd show --json` returns an **array** — take `[0]`. A non-zero exit from `bd list` means *no database resolved*, categorically different from `[]`. `init()` is the only call that creates anything, and returns True only on a read-back. |
| `hooks/lib/abacus_config.py` | Config load with per-key defaults; a malformed file falls back entirely. |
| `hooks/lib/state_store.py` | Atomic per-session state writes, prune. |
| `hooks/lib/otel.py` | Tail-window scan of the events JSONL. Session attribute is `session.id`, dotted. |
| `commands/*.md`, `skills/cost-report/SKILL.md` | Prose only. They call `bd`; they never construct metadata. |
| `CHANGELOG.md` | Keep a Changelog, kept in step with `plugin.json` version bumps. |

## Testing

```bash
python3 -m pytest tests/ -v
```

**TDD, Red-Green-Refactor. Write the failing test first.** Every behaviour in this
repo was driven by a test that was confirmed RED before the code existed; keep it
that way.

Hooks are driven as **real subprocesses** with JSON on stdin. An in-process call
would miss an import error, a stray `print` corrupting the JSON envelope, or a
non-zero exit that only appears under the real entrypoint — all three are failure
modes this plugin is specifically vulnerable to.

The suite is **fully offline**: `bd` and `npx` are stubbed on `PATH` and record
their argv, `HOME` is sandboxed per test. No test may touch a real beads database,
spawn a real `npx`, or read the user's real `~/.claude`. When asserting a `bd`
write, assert on the recorded argv — the flags are emitted in sorted key order
precisely so that is deterministic.

`features/` is executable via pytest-bdd: every scenario binds to a step definition
in `tests/features/steps/` and drives a real hook. **An unbound scenario fails
rather than skips** — the conformance test enforces it, because a feature space
that silently skips is documentation pretending to be a test.
