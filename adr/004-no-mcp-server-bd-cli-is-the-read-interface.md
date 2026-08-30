# ADR 004: No MCP server — `bd`'s own CLI is the read interface

## Status

Accepted

## Date

2026-08-05

## Context

The default architecture for a plugin like this one is a clean split: **hooks
write, MCP reads.** Hooks do only local, offline, always-exit-0 work; every
collaborator call and every user-facing read goes through an MCP server exposing
named tools.

That split solves a real problem when reads are expensive or authenticated —
a token to acquire, a remote API to poll, typed errors to convert at a tool
boundary. The author has built exactly that shape before, and it needs a bootstrap
venv, because an MCP framework and its dependencies are not stdlib.

Applying the same split here was the default assumption and does not survive
contact with what this plugin actually reads. Its data lives in two places:

- **Task state and attribution** — in the beads database, which already ships a
  CLI with `--json` on every read. `bd list`, `bd show`, `bd ready` are the
  reader. There is nothing to translate.
- **Cost** — in ccusage, which is itself a CLI with `--json`, invoked at task
  boundaries by hooks that then write the result into bd. By the time a user
  wants to read it, it is already bd metadata.

So an MCP server here would be a process whose tools shell out to `bd` and
return what `bd --json` already returns. That is not a boundary, it is a
forwarding layer — and one that has to be kept in sync with bd's output shape
across upgrades.

There is a second cost. An MCP server means Python dependencies, which means a
bootstrap venv — which in the author's experience costs two ADRs and a
hook-interpreter bug. This plugin is currently pure-stdlib Python 3.9 with no
install step at all (adr/006). An MCP server would forfeit that for a forwarding
layer.

## Decision

This plugin ships **no MCP server**. `bd`'s CLI is the read interface, and the
plugin's own read surface is thin sugar over it.

- **Slash commands** (`commands/task-start.md`, `task-done.md`, `status.md`) are
  markdown prompts that tell the agent which `bd` commands to run. They cost
  nothing when unused and add no process.
- **A skill** (`skills/cost-report/SKILL.md`) reads attribution out of bd
  metadata and formats it. Also markdown; also no process.
- **Hooks write, `bd` reads.** The rule is honoured in spirit — reads do not happen
  in hooks, they happen in the reader that already exists — while the "MCP" half is
  satisfied by not building a second reader.
- **`.claude-plugin/plugin.json` declares no `mcpServers` key.** There is
  deliberately no `mcp/` directory: a directory named `mcp/` shadows the installed
  MCP SDK on import, a hazard this plugin avoids by not having one.

## Consequences

### Positive
- No venv, no bootstrap, no dependency install, no interpreter-resolution
  problem for a subprocess. The plugin is seven Python files and some markdown,
  and it runs on the system Python 3.9.6 as-is.
- No second reader to keep in sync with bd's `--json` output. When bd changes its
  output shape, exactly one wrapper (`hooks/lib/beads.py`) is affected, and it is
  ~200 lines with the observed contract in its docstring.
- No MCP server process per session, so no startup cost and no stdio transport
  to deadlock against (the `stdin` inheritance hazard described in adr/003 is
  structurally absent here).
- Reads are available in contexts an MCP server would not cover: the user can run
  `bd show <id> --json | jq '.[0].metadata'` in any terminal, with no plugin
  loaded at all. Attribution is not locked behind this plugin.

### Negative
- Reads are not typed or schema-validated at a tool boundary. A malformed `bd`
  output surfaces as whatever `jq` or the agent makes of it, rather than as a
  `{"status": "unavailable", "reason": ...}` shape. Accepted: the failure is
  visible and local, not silent.
- The read path costs the agent a Bash call and its tokens, where an MCP tool
  call would be a structured invocation. In practice these reads are rare
  (a cost report, a status check) and the commands are short.
- A future need for a genuinely non-trivial read — cross-repo aggregation, a
  cost report joining several beads workspaces, anything needing caching or
  auth — would justify revisiting this. The decision is scoped to v1's read
  surface, not asserted as permanent.
- This is a visible divergence from the conventional shape, and a reader who
  expects `mcpServers` in `plugin.json` will wonder where it went. Hence this ADR.

### Neutral
- If an MCP server is ever added, its venv bootstrap must resolve the interpreter
  explicitly rather than trusting `PATH`, and it must not live in a directory named
  `mcp/`.
- `skills/` and `commands/` are the ordinary Claude Code surfaces; only the layer
  underneath them differs from the conventional arrangement.

## Alternatives Considered

### Alternative 1: An MCP server wrapping `bd` and ccusage

Rejected. Its tools would forward to CLIs that already return JSON, adding a
process, a dependency set, a venv bootstrap, and a shape to keep in sync, in
exchange for typed errors on reads that are already local and already visible.

### Alternative 2: A Python CLI in this repo as the read interface

Rejected for the same reason at smaller scale, plus one more: a second CLI
alongside `bd` invites the question of which one is authoritative. `bd` is
(adr/001), and not shipping a competitor keeps that unambiguous.

### Alternative 3: Also write attribution into a session-level cost tracker's store

Rejected, and the user made this call explicitly ("stay separate"). Such a store is
another tool's private schema, keyed on `session_id`, which cannot represent
per-task figures without changing that schema. Writing into a database this plugin
does not own would couple two release cycles for no capability gain. Coexistence is
cheap instead: a session-level tracker hooks SessionStart/UserPromptSubmit/
SessionEnd against its own store and has no PreToolUse hook, so there is no matcher
overlap and nothing to reconcile.
