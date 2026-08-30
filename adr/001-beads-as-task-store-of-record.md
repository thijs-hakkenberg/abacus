# ADR 001: beads is the task store of record; `metadata` is the extension point

## Status

Accepted

## Date

2026-08-05

## Context

This plugin has to answer two questions at once: *is the current work tracked as
a task?* and *what did that task cost?* The first is an enforcement question, the
second an accounting one, and they need a shared notion of "task".

`bd` (beads) 1.1.2 is already installed at `/opt/homebrew/bin/bd` and is the
tracker the user actually uses. It is a distributed issue tracker over an
embedded Dolt database, so issues sync between machines through Dolt's own
push/pull rather than a service this plugin would have to operate. It also
already answers every read this plugin needs with `--json`.

The obvious alternative — this plugin keeping its own table of tasks and costs — is
the shape session-level cost trackers usually take: a private SQLite file keyed on
`session_id`, one project label per session. That design was examined closely and
deliberately not copied, because its granularity is the session, and a session is
not a unit of work: one session routinely spans several tasks, and one task
routinely spans several sessions. A private table would also immediately create a
second answer to "what work exists", which would then need reconciling against bd
forever.

The question that decided it was whether beads has a sanctioned place to put
foreign data. It does: `bd update <id> --set-metadata k=v`. Three properties
were verified empirically on bd 1.1.2 on 2026-08-05, because the whole design
rests on them:

- **It merges rather than replaces.** Two separate `--set-metadata` calls leave
  both keys present, so this plugin can add `abacus_*` keys without knowing or
  disturbing what else is in there.
- **Values round-trip with their JSON types.** An int comes back an int, a bool
  comes back a bool — confirmed on a real closed issue (`ab-e2e-ngd`):
  `abacus_tokens_total 144254 int`, `abacus_partial False bool`, `abacus_schema 1 int`.
- **It works on an already-closed issue.** This is the load-bearing one. It
  means attribution can be written *after* `bd close` lands, in response to
  seeing the close, rather than having to detect an imminent close and race
  ahead of it. The plan carried a fallback for the opposite finding ("write
  metadata just before the close lands"); that fallback is not needed.

## Decision

The beads database is this plugin's store of record for tasks, and issue
`metadata` is where attribution is written. Concretely:

- **The gate asks bd, not this plugin's own state.** `gate_edits.py` decides
  whether work is tracked with `bd list --status in_progress --json`. A claim
  made anywhere — this session, a second terminal, a subagent — opens the gate,
  because the gate does not consult its own bookkeeping to answer a question bd
  can answer authoritatively. This is what makes the enforcement correct rather
  than merely local.
- **This plugin's state file holds attribution bookkeeping only** — which task
  is current, when it was claimed, and the ccusage snapshot taken at that
  moment (`hooks/lib/state_store.py`). It is a cache and a diff baseline, never
  a task list. If it is deleted, tracking still works; only in-flight cost
  attribution for the current task is lost.
- **Attribution is written as `abacus_`-prefixed metadata on the issue itself**
  (`hooks/lib/attribution.py`, `SCHEMA_VERSION = 1`): `abacus_cost_usd_estimate`,
  `abacus_cost_basis`, `abacus_tokens_total`/`_in`/`_out`/`_cache_read`/`_cache_write`,
  `abacus_duration_min`, `abacus_session_id`, `abacus_models`, `abacus_partial`,
  `abacus_schema`, and optionally `abacus_tool_calls`/`abacus_active_min` from OTEL. The
  prefix exists so the keys are unambiguously ours and can be swept or migrated
  as a set.
- **`abacus_schema` is written on every finalisation** so a future reader can tell
  which shape it is looking at without guessing from which keys happen to be
  present.
- **Attribution travels with the issue.** Because it lives in bd rather than in
  a local file, `bd dolt push` carries per-task cost to every other machine and
  to anyone else with the repo, for free. That is a capability a private
  SQLite file structurally cannot have.

## Consequences

### Positive
- There is exactly one answer to "what work exists" on this machine, and this
  plugin is not it. No reconciliation job, no drift, no second source of truth.
- Cost attribution syncs with the issue for free via Dolt. Nothing in this
  plugin implements sync, and nothing needs to.
- Deleting `~/.claude/abacus/` loses no history — every finalised
  figure is already in bd. The state directory is disposable by construction.
- Enforcement is correct under subagent fan-out and multi-terminal use without
  any cross-process coordination, because the shared state is bd's database
  rather than an in-memory or per-session notion of "current task".

### Negative
- Hard dependency on `bd` being installed and on a resolvable beads database.
  Mitigated but not eliminated: every failure to reach bd fails *open* (the
  edit is allowed, a line is logged), so a missing bd degrades to "no tracking"
  rather than "no editing" — see `beads.in_progress`'s `available` flag.
- `metadata` is a string-keyed flat namespace with no schema enforcement on
  bd's side. A typo in a key name is silently accepted and produces a key
  nobody reads. Mitigated by the metadata dict being built in exactly one
  place (`attribution.build_metadata`) with the keys as module constants.
- Metadata values cannot contain whitespace without being split into two argv
  tokens by bd. `beads._metadata_token` collapses whitespace to underscores and
  compacts lists/dicts to separator-free JSON. This is a real, if small, loss
  of fidelity: a model name containing a space would come back with an
  underscore.
- Any bd upgrade that changes `--set-metadata`'s merge behaviour, its tolerance
  for closed issues, or `bd show --json`'s array shape breaks attribution. The
  three properties this design rests on are asserted against real bd in the E2E
  pass rather than assumed.

### Neutral
- `bd show --json` returns a single-element **array**, not an object. Wrapped
  once in `beads.show` so no caller has to remember it.
- `bd list` exits non-zero when no database resolves, which this plugin treats
  as categorically different from an empty list — see adr/002.

## Alternatives Considered

### Alternative 1: A private SQLite table of our own

Rejected. It would create a second answer to "what work exists" and would need
reconciling against bd indefinitely. The natural key is also the wrong one: the
schema such trackers reach for is `session_id PRIMARY KEY` with one project label
per session, first-mention-wins, which cannot express a session covering three
tasks or a task covering three sessions. Per-task granularity is the entire point
here, so adopting that schema would have meant adopting its central limitation.

### Alternative 2: A sidecar file per issue inside `.beads/`

Rejected. It would be inside the beads workspace without being part of the beads
data model, so Dolt would either not sync it or sync it as an opaque blob that
merges badly. `--set-metadata` is the sanctioned extension point; writing beside
it rather than through it forfeits merge semantics and typed round-tripping for
no gain.

### Alternative 3: Store cost in the issue's description or a comment

Rejected. It renders in every `bd show`, is unparseable without inventing a
format, and would put a dollar figure into human-readable prose where it reads
as an assertion rather than a labelled estimate (see adr/005).
