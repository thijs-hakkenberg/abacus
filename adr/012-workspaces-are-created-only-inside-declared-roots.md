# ADR 012: A beads workspace is created automatically, but only inside declared roots

## Status

Accepted

## Date

2026-08-30

## Context

The gate allows every edit in a project that has no beads workspace (adr/002,
step 2 of the decision ladder). That is correct as a failure mode and wrong as a
steady state: enforcement is opt-in *per repository*, so the projects most in need
of tracking — the new ones, opened for the first time — are exactly the ones where
nothing is enforced.

The measured evidence for the gap: enabling `gate.non_beads_project: "block"`
across this machine required hand-running `bd init` in **nine** repositories, via a
throwaway script. Coverage of actual spend by attributed tasks over the first weeks
of use was **16.2%**, and untracked projects were the largest single contributor.
A per-repo manual step is a step that does not happen.

So SessionStart should be able to create the workspace. The difficulty is that this
inverts the plugin's governing rule. Every other decision here **fails open** —
`bd` missing, `bd` broken, a malformed payload, an unexpected exception all allow
the edit and stay silent, because a gate that blocks work when its own tooling
breaks is worse than no gate. Auto-init cannot fail open in that sense, because
failing "open" here means *writing a directory into a repository on the strength of
a value we could not read*.

Three concrete ways a naive implementation does damage:

- **`$HOME` as a project.** A dotfiles repository at `~` gets a workspace, and then
  every session in every directory beneath `~` walks up, finds it, and gates
  unrelated work against one shared task list.
- **A sibling read as a child.** Plain string containment puts `~/projects-old`
  inside `~/projects`. One carelessly named directory silently widens the scope.
- **A workspace in someone else's repository.** A cloned dependency opened to read
  one file acquires a `.beads/` directory, and — without `--stealth` — a dirty
  `git status` and a plausible route into a commit.

Two facts from bd 1.1.2, both established by running it rather than reading docs:

- `bd init` **prompts for an actor role** when it believes a human is present. A
  prompt inside a hook blocks until the event timeout expires, so both
  `--non-interactive` and `BD_NON_INTERACTIVE=1` are required.
- **A zero exit from `bd init` is not proof of a usable workspace.** bd embeds a
  Dolt database; a failure to open it surfaces on the first *read*, not at init.
  Measured cost of a real init in a fresh git repo: **~3.1s**, against
  SessionStart's 20s budget, and only ever once per project.

## Decision

`session_start.py` may create a beads workspace, behind five independent rails.
Each rail's default answer is **no**.

1. **Off unless asked for.** `auto_init.enabled` defaults to `false`. Every other
   default in `abacus_config.py` decides only what the plugin *says*; this one writes
   to a filesystem the user may not own, so it is asked for explicitly rather than
   inferred.
2. **A git root, or nothing.** `.git` must exist in the directory itself — not a
   parent. A git root is an explicit statement that these files are one unit of
   work, and `--stealth` needs `.git/info/exclude` to keep the workspace out of
   version control. Tested with `os.path.exists`, not `isdir`: inside a worktree or
   a submodule, `.git` is a *file*.
3. **Never `$HOME`, never `/`.** Independent of the roots list, because these are
   not a scoping preference — they are the two paths whose workspace would capture
   every session beneath them.
4. **Inside a declared root.** `auto_init.roots` defaults to `["~/projects"]`.
   Containment compares realpaths with a trailing separator, so `~/projects-old` is
   not a child of `~/projects`. An explicit empty list means "any git repository" —
   available, but it has to be typed.
5. **An unreadable `roots` creates nothing.** This is the deliberate inversion of
   fail-open. `auto_init_roots()` returns `None` — distinct from `[]` — when the
   configured value is not a list of paths, and `None` means *no scope*. "I cannot
   tell what you meant" must narrow to nothing, never widen to every git
   repository on the machine.

Beyond the rails:

- **Success is defined by a read-back, not by an exit code.** `beads.init()` returns
  True only if `bd list` afterwards resolves a database. A workspace bd cannot read
  is not a workspace, and treating one as such would have the gate deny every edit
  in a project the plugin itself just broke.
- **`--stealth` is the default**, so `.beads/`, `.beads-credential-key` and
  `.beads/proxieddb/` go into `.git/info/exclude`. A workspace this plugin created
  unprompted must never be able to appear in someone's commit. Verified: after
  auto-init, `git status --porcelain` is empty.
- **`--non-interactive` and `BD_NON_INTERACTIVE=1` are both set**, per the prompt
  behaviour above.
- **PreCompact never initialises.** It is the middle of a session, not the start of
  a project; `session_start.py --precompact` skips auto-init entirely.
- **Failure is silent and total.** A failed init leaves the session exactly as if
  there were no workspace: no state file, no primer, exit 0. The outcome is written
  to the debug log and nowhere else.

## Consequences

### Positive
- Enforcement becomes the default for new work under a declared root, without a
  per-repository manual step. The nine-repository backfill does not recur.
- The blast radius is bounded by construction rather than by care: four of the five
  rails are structural (git root, not `$HOME`, inside a root, readable config) and
  hold regardless of what the config says.
- `--stealth` means the worst case of a wrongly-created workspace is an ignored
  directory, not a polluted commit or a dirty tree.
- The read-back definition of success means a broken embedded-Dolt install produces
  *no* workspace rather than a workspace that gates every edit and can never be
  satisfied.
- Verified end to end against real bd 1.1.2, not only against the stub: workspace
  created, `bd list` returns `[]`, `git status` clean, exclude file updated, primer
  emitted, exit 0, ~5s wall. Re-running does not re-init (987ms). A non-git
  directory and a git repo outside `roots` both get nothing.

### Negative
- **This plugin now writes to repositories on its own initiative.** That is a
  categorically larger claim on the user's filesystem than seven read-only hooks,
  and no set of rails makes it small. Default-off is the mitigation, and the reason
  the default cannot be flipped later without a new decision.
- SessionStart gains ~3s in a project that qualifies — once per project, but that
  once is the least convenient moment, at the start of a session.
- `roots` is machine-local configuration that has to be maintained. A user working
  outside `~/projects` gets nothing and no explanation unless they read the debug
  log.
- A git repository the user does not own but has opened under a declared root is
  still initialised. Stealth limits the damage; it does not prevent the act. There
  is no reliable signal for "mine" that does not amount to another allowlist.

### Neutral
- A subdirectory of an already-initialised repository is not initialised, because
  `beads.has_workspace()` walks up and finds the parent's workspace first. The
  effect is the intended one, but it is inherited from the workspace lookup rather
  than enforced here.
- `auto_init.stealth` can be turned off for a user who wants `.beads/` committed and
  shared. Untested against a repository where a second person then clones it; the
  bd-side consequences of a shared workspace are bd's concern, not this plugin's.

## Alternatives Considered

### Alternative 1: Initialise on the first denied edit, inside the gate

Rejected. The gate is the one script permitted to influence tool execution
(adr/002), and its own constraint is that it does the least possible work — the hot
path is ~0.27s, and a 3s `bd init` inside it would land on a `PreToolUse` with a
10s timeout. More importantly it inverts cause and effect: the workspace would be
created by the act of being blocked, so the user's first experience of a new project
is a denial that silently rewrites their repository.

### Alternative 2: Ask the user, once per project

Rejected as unimplementable in this position. A hook has no interactive channel; the
nearest approximation is injecting a request into context and hoping the agent
relays it, which spends tokens on enforcement (the constraint this plugin exists
to respect) and makes the outcome depend on the model's cooperation. Default-off
config is the same consent, obtained once, in writing.

### Alternative 3: One global workspace for every project

Rejected. It would remove per-repository setup entirely, and it destroys the
property that makes beads the store of record (adr/001): the issue lives *with* the
code and syncs with it. A single global task list also gates unrelated work against
one another's claims, which is the `$HOME` failure mode adopted deliberately.

### Alternative 4: Any git repository by default (`roots: []`)

Rejected as the default while keeping it available as a value. `[]` is the correct
expression of "every git repository" and some users will want it; making it the
default means the first session in a cloned dependency writes to it. The narrow
default is wrong for fewer people than the wide one.
