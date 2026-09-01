# ADR 014: Governance waits for an acknowledgement, and being installed is not one

## Status

Accepted

## Date

2026-09-01

## Context

Installing this plugin is one line typed into `/plugin`. What it buys, from the very
next hook that fires, is:

- **Edits refused.** `gate_edits.py` denies `Edit`/`Write`/`NotebookEdit`/`MultiEdit`
  whenever no beads task is in progress. With `gate.non_beads_project: "block"` that
  extends to every repository on the machine, including ones with no beads workspace
  at all.
- **A directory written into a repository the user may not own.** `auto_init` creates
  `.beads/` in any git root under `auto_init.roots` (adr/012).
- **Metadata written onto issues.** `abacus_*` keys land on the beads issue, which is
  the store of record for someone's task list (adr/001).
- **A remote reached.** `sync_on_session_end: "push"` runs `bd dolt push` as the
  session closes.

Every one of those is defensible, and each is documented. None of them is *announced*.
The failure this ADR exists to prevent was observed in the plain form on 2026-09-01:
adr/012's addendum records a session where `gate.non_beads_project` was `block`, and
the visible outcome to the user was an edit-blocking nudge in a repository that had
no workspace and had never been asked about. The rails held; the surprise was total.

Two facts constrain what can be done about it.

**Claude Code has no plugin-install hook.** There is no `PluginInstall` event, no
post-install script, nothing that runs once when a plugin is added. The earliest
surface after an install is the first `SessionStart` — and for a plugin installed
mid-session, not even that: that session already had its `SessionStart`, so the
first opportunity is the next `UserPromptSubmit`.

**Neither of those surfaces can block, and that is correct.** `SessionStart` and
`UserPromptSubmit` cannot emit a `permissionDecision`; only `PreToolUse` can
(adr/002). Even if they could, consent extracted by making the editor unusable is
not consent — it is what a user clicks through to get their work back. Whatever is
built here has to be answerable by ignoring it.

There is also a narrower problem than first consent. Consent to `roots:
["~/projects"]` is not consent to `roots: []`, which is every git repository on the
machine. Consent to `gate.non_beads_project: "warn"` is not consent to `"block"`. A
one-time acknowledgement that survives arbitrary widening of scope is a signature on
a blank page. But re-asking on *every* config edit is worse than not asking: a notice
that fires when `ccusage_version` is bumped trains people to dismiss it unread, and
then the one that matters is dismissed too.

## Decision

**Until the settings that govern behaviour have been acknowledged, abacus performs no
write and no denial.** It reads, it observes, it says what it would do. That is all.

Concretely, six hooks gain one check — `consent.is_acknowledged(cfg)` — placed above
anything that acts:

| Script | What the check suppresses |
|---|---|
| `gate_edits.py` | every denial, *and* the `bd`/`npx` spawns; placed above the workspace check so `non_beads_project: "block"` is covered too |
| `session_start.py` | `auto_init`, the baseline, and the primer |
| `watch_bd_commands.py` | the `--set-metadata` write at a task boundary |
| `stop_reconcile.py` | the repair write, which fires on every turn |
| `session_end.py` | the partial write **and** `bd dolt push` |
| `prompt_statusline.py` | the statusline, replaced by the notice |

`hooks/lib/consent.py` holds the record and the fingerprint;
`hooks/scripts/acknowledge.py` is the only thing that writes it, and only when
`--accept` is passed explicitly. A bare invocation shows and records nothing, because
consent that can be given by mistyping is not consent.

**The fingerprint covers the six keys that govern behaviour, and no others:**
`gate.enabled`, `gate.non_beads_project`, `auto_init.enabled`, `auto_init.roots`,
`auto_init.stealth`, `sync_on_session_end`. Each of those changes whether abacus
denies something, writes somewhere, or reaches a remote. `statusline`,
`ccusage_version`, `prime.mode`, the timeouts and the cache TTLs are excluded: they
change what abacus *says* or how fast, never what it does to anything. Widening
`roots` re-asks. Bumping the pinned ccusage version does not.

Four details that follow from the above rather than standing alone:

- **The settings are read through `abacus_config`'s accessors, not off the raw dict.**
  What is agreed to has to be the value the plugin will actually act on. An
  unreadable `auto_init.roots` resolves to `None` there — no scope at all, adr/012
  rail 5 — which is a genuinely different thing to have consented to than a list.
- **`roots` is compared as a sorted set.** Reordering a list grants no new scope, and
  a notice that fires for a reordering is noise of exactly the kind that gets the
  real ones ignored.
- **Asked once per session, by whichever surface gets there first.** `SessionStart`
  and `UserPromptSubmit` both write `consent_asked_at` into the disposable
  per-session state; the second one to run sees it and stays quiet. The answer is
  durable, so a second ask in one session carries no information the first did not.
- **`ABACUS_DISABLE=1` outranks the notice.** Someone who set the kill switch has
  already answered. Asking them to switch on a plugin they explicitly silenced is
  the notice failing to read the room.

**What consent does not gate: anything the user invoked.** `/abacus:audit fix`
repairs metadata while unacknowledged, and `/abacus:task-start` claims a task. Those
are the user acting, not abacus acting unprompted, and gating them would make the
notice self-defeating — it names `/abacus:status` as the way to inspect before
agreeing, and a `/abacus:status` that refused to run until you agreed would be a
closed loop.

**Unacknowledged is the default, and it is reached by every failure.** A missing
record, a corrupt one, one with no fingerprint, one written by a future schema: all
resolve to `never`. This is the second place in the plugin that deliberately fails
*closed* — `auto_init` is the first (adr/012 rail 5) — and for the same reason. Every
other failure mode here defaults to allow because a broken gate must not block work.
This one defaults to inert because "I cannot tell whether you agreed" must never
resolve to "yes".

## Consequences

### Positive
- The first thing abacus does on any machine is describe itself. The 2026-09-01
  surprise — enforcement arriving in a repository nobody had discussed — is not
  reachable, because the gate cannot deny before the record exists.
- The notice is specific, not generic: it is rendered from the live config, so it
  names the actual roots, the actual non-beads mode, and whether a push is
  configured. A user reading it learns what *their* installation will do.
- Scope creep re-asks. Flipping `roots` to `[]` pauses governance until the wider
  scope is agreed to separately, which makes adr/012's "the default cannot be flipped
  later without a new decision" enforceable rather than aspirational.
- Cosmetic churn does not re-ask, so the notice keeps its signal. This is what makes
  the previous point worth anything.
- Zero steady-state cost. `consent.notice()` returns `""` once acknowledged, so the
  per-turn token cost of this feature after the first session is nothing, and the
  check itself is one small JSON read with no subprocess.

### Negative
- **An unacknowledged install enforces nothing at all.** A user who installs abacus,
  ignores the notice, and assumes they are covered is not covered — and the failure
  is silent in exactly the way this plugin's other silent failures are. The notice
  fires once per session forever until answered, which is the only mitigation
  available without a blocking surface.
- Six hooks now share a precondition, which is six places for it to be forgotten when
  a seventh is added. `tests/unit/test_consent_gating.py` drives all six as real
  subprocesses for that reason, with a passing control beside each gated assertion so
  a check that disabled the feature outright cannot pass.
- One more file in the state directory, and one more thing that can be lost. Deleting
  `acknowledged.json` silently reverts to inert. That is the safe direction, but it
  is still a way to end up ungoverned without noticing.
- The fingerprint's key list is a judgement, and judgements age. Adding a setting
  that governs behaviour without adding it to `GOVERNING_KEYS` produces a widening
  that never re-asks — the exact hole this decision exists to close, reopened by
  omission.

### Neutral
- `acknowledged.json` lives beside the session state files but is not one: `prune()`
  only ever deletes `session-*.json`, so it is not subject to the 14-day expiry. An
  acknowledgement that expired on a timer would re-ask users who had answered, which
  is the noise case again.
- The record is per-machine, not per-project. Consent is about what abacus may do to
  this filesystem, and `roots` is already machine-local configuration.
- `/abacus:acknowledge revoke` exists and is a supported answer. Nothing in the
  plugin treats revocation as an error state; it returns to the shipped default.

## Alternatives Considered

### Alternative 1: Ship with everything default-off, and let config be the consent

Rejected, though it is close to what already happens and is the reasoning adr/012
used for `auto_init` specifically. Two things break it. The gate is *not* default-off
— denying untracked edits is the plugin's entire purpose, and a version shipping with
`gate.enabled: false` would be a plugin that does nothing until configured, which
nobody would then configure. And config-as-consent assumes the user read the README
before installing. `/plugin install` does not require that, and the observed failure
came from a machine whose owner had written the README.

### Alternative 2: Block until acknowledged — deny every edit with the notice as the reason

Rejected, and it was tempting because `PreToolUse` is the one surface that *can*
compel an answer. It fails on both halves. Practically, a plugin whose first act is
to make the editor unusable gets uninstalled, not acknowledged. Ethically, an answer
given to unblock your own work is not information about what you agreed to — it is
the shape of a cookie banner, and the resulting record would assert a consent that
was never given. Making abacus inert is the only unacknowledged state that is honest
about not having been answered.

### Alternative 3: A one-time acknowledgement with no fingerprint

Rejected. It is materially simpler — a marker file, one boolean — and it collapses
under the case that motivated the invalidation half of this decision: consent to
`~/projects` surviving a switch to every repository on the machine. A signature that
covers whatever the document says next is not a signature.

### Alternative 4: Fingerprint the entire config file

Rejected as the failure mode dressed as rigour. Hashing the whole file re-asks on a
`ccusage_version` bump, a `statusline` toggle, a timeout tweak, a reformat, a comment.
Each of those trains the user that the notice is noise, and the training generalises
to the one that says `roots` just widened to every repository you own. A narrow
fingerprint that fires rarely is worth more than a complete one that fires constantly,
because the value of this notice is entirely in whether it gets read.

### Alternative 5: Re-ask on a timer — expire the acknowledgement every N days

Rejected. It answers a question nobody asked: consent does not decay, and the risk
being managed here is *change of scope*, which a timer neither detects nor bounds. It
would re-ask users whose settings never moved and stay silent for up to N days after
settings that did. Fingerprinting the governing keys tracks the actual hazard, and the
timer would only add noise on top of it.
