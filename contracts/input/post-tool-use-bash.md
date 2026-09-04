# Contract: `PostToolUse` (Bash) hook event — input

## MCP binding

None (adr/004). Claude Code hook event, JSON on stdin.

- **Event:** `PostToolUse`
- **Matcher:** `Bash`
- **Script:** `hooks/scripts/watch_bd_commands.py`
- **Timeout:** 30s (`hooks/hooks.json`)
- **Entrypoint:** `main()` under `hook_io.guard`

This is the attribution engine's only trigger, and since v0.6.0 also the cheap
trigger for commit capture. It writes `contracts/output/bd-metadata-write.md` and
emits nothing on stdout.

## Input schema

```json
{
  "type": "object",
  "properties": {
    "session_id": { "type": "string", "description": "Attribution key and ccusage --id argument." },
    "hook_event_name": { "const": "PostToolUse" },
    "tool_name": { "type": "string", "description": "Anything other than \"Bash\" returns 0 immediately." },
    "tool_input": {
      "type": "object",
      "properties": {
        "command": { "type": "string", "description": "The shell command that ran. Prefiltered on the substrings \"bd\" and \"git\" before anything else, so the Bash calls that are neither cost a string scan rather than a subprocess. Then tokenised with shlex(punctuation_chars=True) — never regex-matched (adr/010)." }
      },
      "required": ["command"]
    },
    "tool_response": { "type": "object", "description": "Present in the payload but deliberately not read. Whether bd succeeded is verified by asking bd, and which commits exist by asking git — never by parsing stdout. See below.", "properties": {
      "stdout": { "type": "string" },
      "stderr": { "type": "string" },
      "interrupted": { "type": "boolean" },
      "isImage": { "type": "boolean", "description": "Observed live, undocumented upstream. Recorded here for completeness; not read." },
      "noOutputExpected": { "type": "boolean", "description": "Observed live, undocumented upstream. Recorded here for completeness; not read." }
    } },
    "cwd": { "type": "string", "description": "The workspace the bd invocation targeted, and the directory whose git repository is measured." }
  },
  "required": ["tool_name", "tool_input"]
}
```

### Commands recognised as boundaries

Verified against bd 1.1.2. Both spellings of each boundary are watched because
agents genuinely use both, and watching only the dedicated subcommand loses those
tasks' cost silently:

| Command shape | Boundary |
|---|---|
| `bd update <id> --claim` | claim |
| `bd update <id> --status in_progress` | claim |
| `bd close <id>` (accepts several ids) | close |
| `bd done <id>` | close |
| `bd update <id> --status closed` | close |

Chained and prefixed forms are handled: `&&`, `\|\|`, `;`, `\|` and `&` split into
segments; leading `VAR=value` assignments are stripped; `/opt/homebrew/bin/bd`
matches on basename while `bdiff` and `sbd` do not. `bd list`/`show`/`ready`/
`prime` and the other read-only subcommands are skipped explicitly, so an
unfamiliar subcommand falls through to the harmless no-match path rather than
being acted on.

`echo "bd close x"` is **not** a close: the quoted string survives tokenising as
a single token. A regex over the raw command string false-positives there and
would mis-attribute an entire task.

**Not recognised** (documented limits, covered by the gate's lazy snapshot and the
Stop/SessionEnd repair passes): variable expansion, `bash -c "…"`, shell
aliases, and `bd` invoked from inside a script file.

### Commands recognised as possible HEAD movement

Since v0.6.0, a `git` verb is also a trigger — for commit capture, which shares the
same tokenising and therefore the same immunity to quoted and heredoc'd text
(`git commit` inside a heredoc body is documentation, not a boundary).

| Verbs | Effect |
|---|---|
| `commit merge rebase cherry-pick revert am apply pull` | capture: diff HEAD against the watermark, write the edges, re-mark |
| `checkout switch reset` | re-mark only. The difference between two branches is not work this task did |

**This list is a cheap trigger, not the correctness mechanism — the watermark is**
(adr/015). A commit made by a shell script, a Makefile target, `gh pr merge`, or a
verb this plugin has never heard of is collected by the `Stop`/`SessionEnd` sweep
instead, so an unrecognised verb costs at most one boundary's delay rather than a
lost edge. That is why widening the list is a convenience rather than a fix.

`tool_response` stays unread here too, and more emphatically: `-q` suppresses git's
`[branch shortsha]` line, the sha is abbreviated when present, and real commands are
compound. Asking git what HEAD is cannot be wrong about it.

## SemVer

- **Contract version:** 1.1.0 — the prefilter and the recognised command set widen
  beyond `bd` to include `git` (adr/015), and two live-but-undocumented
  `tool_response` keys are recorded. Additive: every 1.0.0 boundary is still a
  boundary.
- **Deprecation policy:** Recognising an additional command shape as a boundary
  is a minor bump. Ceasing to recognise one is a **major** bump — tasks that
  used to be attributed would silently stop being. Beginning to *read*
  `tool_response` would also be major: it would make the hook's behaviour depend on
  output formats neither `bd` nor `git` guarantees.

## SLA + telemetry

- **Latency (p99):** `latency_p99_ms: 30000` — the `hooks.json` timeout, sized for
  the boundary path, not the common one. A command matching neither prefilter exits
  in ~0.10s (interpreter startup plus a substring scan). A git verb spawns two short
  `git` reads (`rev-parse`, then `log` only when HEAD actually moved), each bounded
  at 5s. A close spawns one `npx ccusage`
  (~1.9s cold, measured 2026-08-06, bounded at `ccusage_timeout_s` = 25) plus one
  `bd update --set-metadata`. This hook is non-blocking, so its latency delays
  nothing the user is waiting on.
- **Availability:** best-effort. A failed metadata write is logged to stderr and
  retried at the next boundary (Stop, or SessionEnd) rather than raised.
- **Telemetry:** none written. `~/.claude/logs/claude-code-events.jsonl` is read
  for optional enrichment and never appended to.
