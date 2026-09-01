---
name: acknowledge
description: Show what abacus would do on this machine, and switch governance on or off
---

Show the settings that govern abacus's behaviour, and record — or withdraw — agreement
to them. Argument: `$ARGUMENTS` — `revoke` to withdraw, anything else (including
nothing) shows first and then asks.

Until this has been accepted, abacus is inert: it denies no tool call, creates no
workspace, writes no `abacus_*` metadata and reaches no remote (adr/014). Being
installed is not agreement.

## Execution

**1. Read the current state. Always do this first, even when the user asked to accept.**

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/acknowledge.py" --json
```

`status` is `never`, `changed` or `acknowledged`; `settings` is the six governing
values as they stand. When `status` is `changed`, `changed` lists which of them moved
since the last agreement — that list is the point of the re-ask, so report it.

**2. Show what would be switched on, in your own words, from `settings`.**

| Setting | What agreeing to it permits |
|---|---|
| `gate.enabled` | refusing `Edit`/`Write`/`NotebookEdit`/`MultiEdit` when no beads task is in progress |
| `gate.non_beads_project` | `block` extends that refusal to projects with no beads workspace at all |
| `auto_init.enabled` + `auto_init.roots` | creating a `.beads/` directory inside git repositories under those roots |
| `auto_init.stealth` | `false` means the created directory is **not** hidden from git and can reach a commit |
| `sync_on_session_end` | `push`/`sync` runs `bd dolt` against a remote as a session ends |

`auto_init.roots` of `[]` means every git repository on this machine; `null` means the
configured value could not be read, so auto-init does nothing at all.

**3. Record the answer, only when the user has actually given one.**

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/acknowledge.py" --accept
```

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/acknowledge.py" --revoke
```

Do not run `--accept` on the strength of the user having typed the command. Running
`/abacus:acknowledge` is a request to *see* this; agreement is the reply to step 2. If
the answer is at all unclear, show and stop — an unacknowledged plugin costs the user
nothing, and a wrongly recorded agreement is a denial they never asked for.

The script exits 0 whatever happens, including when the record cannot be written. Read
`ok` from `--json`, or the message from the plain output: a failed write leaves abacus
inert and must be reported as such, never as success.

## Reporting

Two or three sentences. What abacus is currently permitted to do, what changed if this
was a re-ask, and — if an answer was recorded — that governance is now on or off. Say
that any change to the six settings above will ask again, and that `ABACUS_DISABLE=1`
silences one shell without touching the record.

Do not print the JSON, and do not walk through settings that are off. A user reading
this is deciding, not auditing.
