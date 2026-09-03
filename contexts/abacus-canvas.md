# Bounded Context Canvas: Task Cost Tracker

> Template based on the ddd-crew Bounded Context Canvas.

## Name

Task Cost Tracker

## Purpose

Makes every Claude Code edit attributable to a tracked unit of work, and attaches
what that work actually cost to the work item itself. It does this by refusing
edits when no beads task is in progress, and by diffing two readings of a
cumulative ccusage session total across each task's claim/close boundary.

It exists as its own context because it is the only part of the Claude Code
rollout that is allowed to **block a tool call**, and the only one whose unit of
attribution is a **task** rather than a session. Keeping enforcement isolated
means every other plugin can keep the always-exit-0 contract that makes hooks
safe (see adr/002); keeping per-task attribution isolated means the store of
record can be the beads issue itself rather than a private database, so the
figures travel with the work item to whoever reads it next (adr/001).

## Strategic Classification

**Domain type**: Supporting
**Model role**: Policy enforcer
**Evolution stage**: Custom-built

## Domain Roles

- Policy enforcer — decides, per Edit/Write/NotebookEdit/MultiEdit call, whether
  the work is attributable, and denies it with remediation when it is not. The
  only role in the plugin that can affect whether a tool runs.
- Attribution ledger — owns the claim→close boundary, the ccusage snapshot pair
  either side of it, and the `abacus_*` metadata written onto the issue. Records a
  task's cost, tokens, duration and models, or records honestly that it could
  not.
- Gateway — translates between the Claude Code hook lifecycle and two external
  collaborators (the `bd` CLI over embedded Dolt, the `npx`-invoked ccusage CLI),
  reading an OTEL event log as a third, optional one.

## Architecture: one blocking hook, six that cannot block

Seven hook invocations share `hooks/lib/` and communicate only through the beads
database and a per-session JSON state file (`$ABACUS_STATE_DIR/session-<id>.json`).
There is no server, no daemon and no second reader (adr/004).

- **`gate_edits.py` (PreToolUse)** is the one script permitted to emit a
  permission decision. Its source of truth is `bd list --status in_progress`, never
  this plugin's own state, so a claim made in another terminal or by a subagent
  opens the gate. Every failure path — `bd` absent, `bd` non-zero, no workspace,
  malformed payload, unexpected exception — allows. It still exits 0; the JSON on
  stdout is the decision (adr/002).
- **`watch_bd_commands.py` (PostToolUse:Bash)** is the attribution engine. It
  tokenises the command with `shlex(punctuation_chars=True)` rather than
  regex-matching it, so `echo "bd close x"` is not mistaken for a close
  (adr/010), and it acts only on the two boundaries — claim and close.
- **The four lifecycle scripts** (`session_start.py`, which also serves PreCompact;
  `prompt_statusline.py`; `stop_reconcile.py`; `session_end.py`) prime, label,
  repair and finalise. None of them spawns a subprocess on the per-turn hot path:
  the statusline reads state only.
- **`hooks/lib/attribution.py`** is the single place that builds `abacus_*` metadata,
  because four callers need to finalise a task and four independent
  implementations would drift.

Every script except the gate runs its body inside `hook_io.guard`, which converts
any unexpected exception into a silent exit 0 — the plugin fails open by
construction rather than by discipline.

## Inbound Communication

| Message | Type | From | Relationship |
|---|---|---|---|
| SessionStart event | event | Claude Code runtime | Conformist (CF) — consumes the hook payload shape Claude Code defines |
| PreToolUse event (Edit\|Write\|NotebookEdit\|MultiEdit) | event | Claude Code runtime | Conformist (CF) — and the only inbound message whose response can stop a tool call |
| PostToolUse event (Bash) | event | Claude Code runtime | Conformist (CF) |
| UserPromptSubmit event | event | Claude Code runtime | Conformist (CF) |
| Stop event | event | Claude Code runtime | Conformist (CF) — including the `stop_hook_active` loop guard |
| PreCompact event | event | Claude Code runtime | Conformist (CF) |
| SessionEnd event | event | Claude Code runtime | Conformist (CF) |
| `bd list`/`bd show`/`bd prime` JSON | response | local `bd` CLI (embedded Dolt) | Customer/Supplier (C/S) — depends on `--json` emitting an array and on a non-zero exit meaning "no database", not "nothing claimed" |
| ccusage `session --json` payload | response | local `npx`-invoked ccusage CLI | Customer/Supplier (C/S) — depends on the pinned version's `sessions[]`/`totalCost`/`totalTokens` contract |
| OTEL event lines | event | `~/.claude/logs/claude-code-events.jsonl` | Conformist (CF) — consumes whatever the collector wrote, including the dotted `session.id` attribute name |

## Outbound Communication

| Message | Type | To | Relationship |
|---|---|---|---|
| deny decision | response | Claude Code runtime | Conformist (CF) — must match the `hookSpecificOutput.permissionDecision` shape (`contracts/output/permission-decision.md`) |
| injected context (primer, statusline) | response | Claude Code runtime / the model's context | Conformist (CF) — `hookSpecificOutput.additionalContext` |
| `bd update <id> --set-metadata abacus_*=…` | command | local `bd` CLI (embedded Dolt) | Customer/Supplier (C/S) — depends on `--set-metadata` merging rather than replacing, and on it working against a closed issue (`contracts/output/bd-metadata-write.md`) |
| `git rev-parse` / `git log` | query | local `git` CLI | Conformist (CF) — read-only, and asked rather than parsed out of a command's stdout; depends on the `%(trailers:key=…)` atom and on `rev-list`-style ranges including merges (adr/015) |
| `bd dolt push` / `bd dolt sync` | command | beads remote, via `bd` | Customer/Supplier (C/S) — opt-in, best-effort, never retried |
| stderr diagnostics | event | Terminal / user | Open Host Service (OHS) — plain text prefixed `[abacus]`, no consumer contract |

## Ubiquitous Language

| Term | Definition |
|---|---|
| Task | A beads issue. The unit of attribution and the store of record — the plugin has no private issue concept and no database of its own (adr/001) |
| Claim | `bd update <id> --claim` (or `--status in_progress`). Opens the gate and takes the cost baseline |
| Close | `bd close <id>` (or `bd update <id> --status closed`). Reads ccusage again, diffs, and writes the delta as metadata |
| Boundary | A claim or a close — the only two events the Bash watcher acts on |
| Snapshot | One reading of ccusage's cumulative `totalCost`/`totalTokens` for a session id, cached 30s. Stored in session state, never in beads |
| Baseline | The snapshot taken at claim time. Created once and never overwritten, because priming fires on resume and compaction too and replacing it would discard the current task's spend so far |
| Delta | `after − baseline`, clamped at zero. The task's cost. Never a sum of per-message `costUSD` figures, which are unreliable (adr/003) |
| Cost estimate | `abacus_cost_usd_estimate` — a local, this-device, list-rate number. Named `_estimate` and always written beside `abacus_cost_basis` so it cannot later be quoted as billing (adr/005) |
| Cost basis | `abacus_cost_basis` — either `ccusage-local-list-rate` or `unavailable`. The label that makes the number's provenance non-optional |
| Partial | `abacus_partial=true` — "this much was spent, and the task is not finished". A later close reads those figures back and adds to them, so a task spanning three sessions reports its whole cost (adr/011) |
| Unavailable | ccusage could not be read. The metadata carries no dollar figure and no token counts at all — an absent key prompts a question, whereas `$0.00` is a wrong answer wearing the costume of a measurement |
| Fail open | Every hook except the gate's one genuine deny allows on every error path. A gate that blocks edits because its own tooling broke is worse than no gate |
| Gate cache | A 3s memo of a previous *allow*, keyed on session **and** workspace. One-sided on purpose: a stale allow smears attribution by seconds, a stale deny would refuse an edit after the user correctly claimed (adr/008) |
| Lazy snapshot | The gate's repair path — when it allows an edit for a task it has no baseline for, it takes one now. Covers claims the watcher's tokeniser did not see |
| Compact primer | The ~450-character orientation injected at SessionStart, instead of `bd prime`'s ~1,200 tokens of workflow manual (adr/009) |
| Commit edge | One `abacus_commit_<sha12>` key on a task issue, recording that a commit belongs to that task. The relation is m:n — one commit can complete several tasks, one task spans many commits — and one key is one edge, so one `--unset-metadata` withdraws exactly one (adr/015) |
| Basis | The evidence an edge rests on, and the first field of its value. Mirrors `abacus_cost_basis`: an edge without its basis is a claim without its evidence |
| Declared | A `Beads-Task: <id>` trailer git itself parsed out of the commit message. The strongest basis, the only one that expresses true m:n, and the only one that needs no claim |
| Observed | HEAD moved during this session while that task was claimed. Means "this commit landed while this task was claimed" and nothing stronger — it cannot distinguish work in a commit from work alongside it |
| Inferred | The commit's timestamp falls inside a claim window. Ambiguous, so **never written**: it stays a proposal in an audit report (adr/013, adr/015) |
| Watermark | The HEAD sha this session last saw for a repository, held in disposable session state. Capture diffs against it. It, not the list of git verbs the watcher recognises, is what makes capture correct |
| Seed | Recording a watermark and attributing nothing — on first sight of a repository, and on `checkout`/`switch`/`reset`. Without it the first git command of a session would hang the whole history on whatever task is claimed |

## Business Decisions

- The gate's source of truth is always the beads database, never this plugin's
  session state. State exists only for cost attribution.
- The gate denies only in one case: a beads workspace exists, `bd` answered
  successfully, and nothing is in progress. Every other outcome allows.
- A denial always names the commands that fix it and the environment variable
  that bypasses it. An enforcement mechanism with no stated remediation is a
  trap.
- `gate.non_beads_project` defaults to `warn`, never `block`. A plugin installed
  user-wide must not make unrelated repositories un-editable.
- An unreadable cost is omitted, never zeroed — no dollar figure and no token
  counts, with `abacus_cost_basis=unavailable` recording why.
- The dollar figure never travels alone. `abacus_cost_usd_estimate` is always written
  beside `abacus_cost_basis`.
- Duration is written even when cost is not. A task's elapsed time is knowable
  without ccusage.
- A zero tool-call count is never written. Nothing distinguishes it from a
  measurement, so only real OTEL activity is recorded.
- Cost is only ever attributed to the task this session claimed. Closing an issue
  this session never claimed writes no cost figure — the spend accrued under
  whatever *is* claimed.
- Only `abacus_partial=true` metadata is carried forward on a later close. A
  finalised figure is left alone, or closing an issue twice would double it.
- ccusage's version is pinned, never `@latest`. It carries the pricing table, so
  floating it silently re-prices historical tasks.
- `bd dolt push` is opt-in (`sync_on_session_end`, default `off`). Reaching a
  remote on the user's behalf as a session closes is not a default.
- Only witnessed edges are written. `declared` and `observed` are recorded;
  `inferred` never is, which is how adr/015 extends adr/013 rather than reversing it.
- Nothing is written into the user's repository to capture commits — no git hook, no
  config value, no trailer. A git hook would also run with no Claude environment and
  so no session id, which is the one thing capture exists to record.
- Cost per commit is derived at read time and never stored, so the apportionment can
  change with no data to migrate. Equal share within the task, and the denominator is
  always published beside the share — per-commit costs do not sum to a repository
  total under m:n.
- A commit is recorded once, not once per boundary. The watermark advances even when
  a write failed: losing one boundary's edges is a smaller failure than a growing
  range that eventually trips the cap and captures nothing ever again.
- The plugin owns no store of its own. Session-level cost reporting is a separate
  concern with separate tools; this context owns task-level attribution only, and
  writes it where beads already keeps the task.

## Assumptions

- `bd`'s `--set-metadata` merges into existing metadata and works against a
  closed issue — verified on bd 1.1.2, and the reason the read-modify-write
  accumulation needs no race with `bd close`. A future bd that replaced metadata
  instead would silently destroy prior keys.
- `bd list --json` exiting non-zero means no database resolved, categorically
  different from returning `[]`. The gate's fail-open behaviour rests entirely on
  that distinction.
- ccusage deduplicates usage lines on `(message.id, requestId)` across main and
  subagent transcripts, so a session total already includes subagent fan-out —
  validated empirically at 88 unique pairs from 251 raw lines across 5 subagents
  (adr/003). Without this, per-task attribution under fan-out would be wrong
  rather than merely imprecise.
- Users have Node.js/`npx` available. Users without it see tasks recorded with
  `abacus_cost_basis=unavailable` and a duration, by design.
- The OTEL collector, when running, writes `session.id` (dotted) as the session
  attribute. Enrichment is omitted silently on any mismatch.
- Python 3.9 is the floor. Hooks are stdlib-only with no venv (adr/006).

## Verification Metrics

- Every hook except `gate_edits.py` exits 0 on every code path, including on a
  malformed payload and a missing `bd`. Asserted per script in the feature space.
- The gate denies in exactly one scenario and allows in all others; no scenario
  produces a deny whose reason omits remediation.
- No `abacus_cost_usd_estimate` is ever written without a sibling
  `abacus_cost_basis`; no `$0.00` is ever written for an unreadable cost.
- `python3 -m pytest tests/ -v` stays green offline, with `bd` and `npx` stubbed
  on `PATH` and `HOME` sandboxed — no test touches a real beads database or
  spawns a real `npx`.
- Every scenario in `features/*.feature` binds to a real step definition and
  drives a real hook subprocess; an unbound scenario fails rather than skips.
- The gate's hot path stays subprocess-light enough to sit in front of every
  edit: one `bd list` (~0.45s measured on bd 1.1.2), memoised for 3s, and no
  `npx` spawn except on the lazy-snapshot repair path, which is bounded at 4s.

## Open Questions

- [ ] Parallel-agent smearing: session-scoped ccusage totals attribute concurrent
      subagent work to whichever task is current, and simultaneous multi-claim
      attributes to the most recent claim. Is per-subagent attribution worth the
      complexity, or is the smear acceptable given subagents are usually serving
      the claimed task anyway?
- [ ] Bash file writes bypass the gate (`sed -i`, heredocs, `python -c`).
      Regex-gating Bash would false-positive constantly. Is there a cheap
      signal — `tool_response` file mtimes? — that would catch the common cases
      without becoming a second, worse gate?
- [ ] Nothing checks that these artefacts still describe the software. The
      conformance test catches drift *between* artefacts, not staleness in all of
      them at once (adr/007). A freshness signal would need a human, or a much
      cleverer test than exists here.
