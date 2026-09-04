# Contract: `abacus_*` issue metadata — output

## MCP binding

None (adr/004). This is a **subprocess** interface: the plugin writes attribution
by invoking the `bd` CLI.

- **Command:** `bd update <id> --set-metadata <key>=<value> [--set-metadata …]`
- **Emitted by:** `hooks/lib/beads.set_metadata()`, always via
  `hooks/lib/attribution.build_metadata()` (task attribution) or
  `hooks/lib/attribution.build_commit_edges()` (commit edges) — the single place
  that constructs these keys, because several hooks need to finalise a task and
  several independent implementations would drift.
- **Callers:** `watch_bd_commands.py` (on close, and on a claim that supersedes an
  unclosed task), `stop_reconcile.py` (close seen outside the watcher),
  `session_end.py` (session ended mid-task). Commit edges come from
  `hooks/lib/commit_capture.capture()`, which the Bash watcher, `SessionStart`,
  `PreCompact`, `Stop` and `SessionEnd` all share.

This is the plugin's primary output. The beads issue is the store of record — there
is no private database (adr/001) — so these keys are what a future reader, report
or dashboard actually consumes.

## Output schema

Keys are written as argv tokens, so every value reaches `bd` as a string; bd 1.1.2
round-trips them back as typed JSON. Whitespace inside a scalar is collapsed to
underscores by `beads._metadata_token`, because a value containing a space would
reach bd as two words and silently truncate.

```json
{
  "type": "object",
  "properties": {
    "abacus_schema": { "const": 1, "description": "Schema version. Present on every write, so a reader can tell which shape it is looking at before trusting any other key." },
    "abacus_session_id": { "type": "string", "description": "The Claude Code session that did the work. Also the ccusage --id the figures came from." },
    "abacus_partial": { "type": "boolean", "description": "false = these figures are final. true = this much was spent and the task is not finished; a later close reads them back and adds to them (adr/011)." },
    "abacus_duration_min": { "type": "integer", "description": "Wall-clock minutes between claim and finalisation, accumulated across sessions. Written unconditionally — a task's elapsed time is knowable even when its cost is not." },
    "abacus_cost_basis": { "type": "string", "enum": ["ccusage-local-list-rate", "unavailable"], "description": "Always present. The label that makes the dollar figure's provenance non-optional." },
    "abacus_cost_usd_estimate": { "type": "number", "description": "Rounded to 4dp. Present ONLY when abacus_cost_basis is ccusage-local-list-rate. Named _estimate deliberately (adr/005)." },
    "abacus_tokens_total": { "type": "integer" },
    "abacus_tokens_in": { "type": "integer" },
    "abacus_tokens_out": { "type": "integer" },
    "abacus_tokens_cache_read": { "type": "integer" },
    "abacus_tokens_cache_write": { "type": "integer" },
    "abacus_models": { "type": "string", "description": "Comma-separated model ids from ccusage's modelsUsed. Present only alongside a readable cost, and only when ccusage reported any." },
    "abacus_tool_calls": { "type": "integer", "description": "Optional OTEL enrichment. Present only when the event log yielded real activity." },
    "abacus_active_min": { "type": "integer", "description": "Optional OTEL enrichment, same condition as abacus_tool_calls." }
  },
  "patternProperties": {
    "^abacus_commit_[0-9a-f]{12}$": {
      "type": "string",
      "pattern": "^(declared|observed):[^:\\s]*:[0-9]+$",
      "description": "One task↔commit edge. The key's suffix is the commit's abbreviated sha12; the value is <basis>:<session-id>:<commit-epoch> (adr/015). Written by commit_capture, independently of the attribution write, and at any point in a session rather than only at a boundary."
    }
  },
  "required": ["abacus_schema", "abacus_session_id", "abacus_partial", "abacus_duration_min", "abacus_cost_basis"]
}
```

Note that `required` does **not** apply to a commit edge. Edges and attribution are
separate writes with separate lifetimes: an issue can carry edges while its cost is
still unknown, and an issue closed without a commit carries attribution and no
edges. A reader must treat the presence of either as independent of the other.

### The two invariants a consumer can rely on

**A cost figure never travels alone.** `abacus_cost_usd_estimate` is written if and
only if `abacus_cost_basis == "ccusage-local-list-rate"`. A number computed from a
local list-rate pricing table on one developer's machine must not be quotable as
billing, so its provenance is structurally inseparable from it (adr/005).

**An unreadable cost is omitted, never zeroed.** When ccusage cannot be read the
write carries `abacus_cost_basis=unavailable` and **no dollar figure and no token
counts at all** — not zeros. A `$0.00` against a task that ran for an hour is a
wrong answer wearing the costume of a measurement; an absent key prompts a
question instead. The same reasoning governs `abacus_tool_calls`: a zero there is
indistinguishable from a measurement, so a readable-but-empty OTEL log writes
nothing rather than `0`.

### The commit edges

**An edge never travels without its basis** — the same rule as a cost figure, for the
same reason. The value's first field is the evidence the edge rests on, and only the
two that were *witnessed* are ever written:

| Basis | Established by | Needs a claim |
|---|---|---|
| `declared` | a `Beads-Task: <id>` trailer git itself parsed out of the message | no |
| `observed` | HEAD moved during this session while that task was claimed | yes |

A third tier, `inferred` — the commit's timestamp falls inside a claim window — is
**never written to this interface**. It is what `/abacus:audit` already computes and
reports as a proposal, and adr/013 forbids writing it. A consumer that encounters
`inferred` here is reading data this plugin did not produce.

`declared` is the only basis that can express the true m:n relation: a commit closing
three tasks names three, and the same `abacus_commit_<sha12>` key is written onto all
three issues with the same value. **Per-commit costs therefore do not sum to a
repository total**, and any report showing them must say so.

**Keys are read, never deleted.** An amend or a rebase orphans a recorded sha; the key
is left in place and a reader marks it `rewritten` when `git cat-file -e` fails.
Deleting it would be a write based on inference. Withdrawing an edge is the user's
call, and `bd update <id> --unset-metadata abacus_commit_<sha12>` does exactly one
edge's worth of damage — which is the whole reason the unit of write is one key
(adr/015). Verified against bd 1.1.2 at 200 keys on one issue:
`tests/integration/test_bd_metadata_ceiling.py`.

### Accumulation

`--set-metadata` **merges** into existing metadata rather than replacing it, and
works against an already-closed issue — both verified on bd 1.1.2. That is what
lets attribution be written *after* `bd close` lands instead of having to race
ahead of it, and what makes accumulation a safe read-modify-write.

Only `abacus_partial=true` metadata is carried forward. A finalised figure is left
alone, or closing an issue twice would double it.

### Example

```
bd update ab-42 \
  --set-metadata abacus_cost_basis=ccusage-local-list-rate \
  --set-metadata abacus_cost_usd_estimate=0.8123 \
  --set-metadata abacus_duration_min=23 \
  --set-metadata abacus_models=claude-fable-5 \
  --set-metadata abacus_partial=false \
  --set-metadata abacus_schema=1 \
  --set-metadata abacus_session_id=8f3c… \
  --set-metadata abacus_tokens_cache_read=298114 \
  --set-metadata abacus_tokens_cache_write=12043 \
  --set-metadata abacus_tokens_in=9812 \
  --set-metadata abacus_tokens_out=14543 \
  --set-metadata abacus_tokens_total=334512
```

Flags are emitted in sorted key order — not for readability, but so the argv is
deterministic and a test can assert on it.

A commit edge is a separate `bd update`, made when the commit is seen rather than at
the task boundary:

```
bd update ab-42 \
  --set-metadata abacus_commit_b0cff661a2c3=observed:8f3c…:1756900000
```

## SemVer

- **Contract version:** 1.1.0 — added `abacus_commit_<sha12>` (adr/015). Additive:
  every 1.0.0 key keeps its meaning, and a 1.0.0 reader that ignores unknown keys is
  unaffected. `abacus_schema` stays at `1` accordingly.
- **Migration:** `abacus_schema` carries the version in-band. A reader should check
  it before interpreting any other key, and treat an unknown value as "do not
  trust the shape".
- **Deprecation policy:** Adding a `abacus_*` key is a minor bump. Removing one,
  renaming one, or changing a unit (minutes → seconds, dollars → cents) is a
  **major** bump and requires `abacus_schema` to increment — historical issues keep
  their old metadata forever, so a reader must be able to tell the shapes apart.
  Beginning to write a zero where a key is currently omitted would also be major:
  it changes "unknown" into "measured zero", which is a semantic reversal even
  though no key name changes.

## SLA + telemetry

- **Freshness:** attribution is written at the task boundary, not on a schedule.
  Between a claim and its close an issue carries no `abacus_cost_*` keys at all,
  which is correct — the cost is not yet known. A commit edge is fresher and
  independent: it appears within one boundary of the commit, so a claimed task can
  carry edges and no cost.
- **Durability:** whatever `bd`'s embedded Dolt provides. The plugin adds no
  journal of its own; if the write fails it is logged to stderr and retried at the
  next boundary (Stop, then SessionEnd).
- **Latency (p99):** `latency_p99_ms: 5000` for the write itself — one `bd update`
  subprocess, ~0.2s measured on bd 1.1.2, so the budget is five seconds of
  headroom rather than an expectation. It is always preceded by one `npx ccusage`
  read (~1.9s cold, `latency_p99_ms: 25000`) on the paths that need a cost, and
  both fit inside the calling event's own timeout.
- **Availability:** best-effort. A failed write never raises and never breaks the
  session — the session must not break because a bookkeeping write failed.
- **Telemetry:** none emitted. These keys *are* the telemetry.
