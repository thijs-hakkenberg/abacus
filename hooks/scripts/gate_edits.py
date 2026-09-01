#!/usr/bin/env python3
"""PreToolUse gate — refuse untracked edits.

The enforcement core: if a beads workspace exists and no task is claimed, this
denies Edit/Write/NotebookEdit and tells the agent how to fix it. Enforcement is
mechanical rather than prompted, so it costs no tokens until it actually fires,
and it cannot be forgotten halfway through a long session.

Two design decisions govern everything below.

**The beads database is the source of truth, not this plugin's state.** A claim
made anywhere — this session, a second terminal, a subagent — opens the gate,
because the gate asks bd rather than consulting its own bookkeeping. State is
only ever used for cost attribution.

**Every path except one genuine case fails open.** bd missing, bd broken, no
workspace, malformed payload, unexpected exception: all allow. A gate that
blocks a user's edits because its own tooling broke is worse than no gate. The
decision ladder below is ordered so the cheap disqualifiers come first and the
only subprocess spawn happens when a real decision depends on it.

This is the one script in the plugin that emits a permission decision (adr/002).
It still exits 0 — the JSON on stdout is the decision, not the exit code.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import beads  # noqa: E402
import ccusage  # noqa: E402
import consent  # noqa: E402
import hook_io  # noqa: E402
import state_store  # noqa: E402
import abacus_config  # noqa: E402

GATED_TOOLS = ("Edit", "Write", "NotebookEdit", "MultiEdit")

# How long a previous *allow* stays good for. Measured on bd 1.1.2: a real
# `bd list --status in_progress --json` against embedded Dolt costs ~0.45s, which
# is ~85% of this hook's total runtime, and Claude edits in bursts.
#
# The cache is one-sided on purpose. A stale allow means an edit slips through a
# second or two after the last task closed — a negligible attribution smear. A
# stale deny would mean refusing an edit *after* the user correctly claimed a
# task, telling them to do the thing they just did; that failure is designed out
# rather than tuned, so denies always re-query. See adr/008.
DEFAULT_GATE_CACHE_TTL_S = 3

# How long the gate will wait for ccusage on its lazy-snapshot repair path.
#
# Far below both ccusage's own 25s default and this hook's 10s timeout, because
# the user's edit is blocked for the whole spawn. Measured 2026-08-06: a cold
# `npx ccusage` costs ~1.9s. At 25s a slow npx would let Claude Code kill the
# gate mid-write, losing the baseline the repair exists to create *and* stalling
# the edit for the full budget first. The repair is optional — attribution is
# nice, an unblocked edit is not — so it gets a small slice and gives up.
GATE_SNAPSHOT_TIMEOUT_S = 4

DENY_NO_TASK = """abacus: no beads task is in progress, so this edit is not attributable to any tracked work.

Claim a task first, then retry:
  existing work:  bd ready --json          then  bd update <id> --claim --json
  new work:       bd create "<title>" --silent   then  bd update <id> --claim --json

Cost and duration are attributed to whatever task is claimed when the edit lands.
(To bypass for this shell: export ABACUS_DISABLE=1)"""

DENY_NO_WORKSPACE = """abacus: no beads workspace found here, and the gate is configured to require one (gate.non_beads_project = "block").

Initialise tracking, then retry:
  bd init                                   then  bd create "<title>" --silent
                                            then  bd update <id> --claim --json

(To allow untracked repos instead, set gate.non_beads_project to "warn" in
~/.claude/abacus/config.json. To bypass for this shell: export ABACUS_DISABLE=1)"""


def _now_iso():
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def _cache_ttl(cfg):
    raw = os.environ.get("ABACUS_GATE_CACHE_TTL_S")
    if raw is None or raw == "":
        raw = (cfg.get("gate") or {}).get("cache_ttl_s", DEFAULT_GATE_CACHE_TTL_S)
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return DEFAULT_GATE_CACHE_TTL_S


def _fresh_allow(state, cwd, ttl):
    """True if this session recently saw a claimed task in this same workspace.

    Keyed on the workspace as well as the session: a claim in one repo says
    nothing about whether work in another repo is tracked.
    """
    if ttl <= 0:
        return False
    allow = state.get("gate_allow")
    if not isinstance(allow, dict):
        return False
    if allow.get("cwd") != cwd:
        return False
    import time

    try:
        return (time.time() - float(allow.get("at") or 0)) <= ttl
    except (TypeError, ValueError):
        return False


def _track(session, issue, cfg):
    """Record which task is current and take its cost baseline.

    Called only when the gate has already decided to allow. This is the repair
    path for a claim the PostToolUse watcher did not see — a `bd update --claim`
    typed in another terminal, or one whose Bash invocation the watcher's parser
    did not recognise. Without it, that task would close with no baseline and be
    recorded at zero cost.
    """
    issue_id = str((issue or {}).get("id") or "")
    if not issue_id:
        return
    state = state_store.load(session)
    if state.get("current_task") == issue_id and isinstance(state.get("snapshot"), dict):
        return  # already tracking: re-snapshotting here would reset the baseline

    # The gate is the only caller that reads ccusage with the user waiting, so it
    # is the only one that overrides the configured timeout downwards.
    gate_cfg = dict(cfg)
    gate_cfg["ccusage_timeout_s"] = min(
        int(cfg.get("ccusage_timeout_s") or GATE_SNAPSHOT_TIMEOUT_S),
        GATE_SNAPSHOT_TIMEOUT_S)
    snapshot = ccusage.snapshot(session, gate_cfg)
    state_store.update(session, {
        "session_id": session,
        "current_task": issue_id,
        "current_title": (issue or {}).get("title", ""),
        "claimed_at": _now_iso(),
        "snapshot": snapshot,
        "snapshot_source": "gate-lazy",
    })


def main():
    payload = hook_io.read_payload()

    tool = str(payload.get("tool_name") or "")
    if tool not in GATED_TOOLS:
        return 0

    # Cheapest disqualifiers first — the kill switch must cost nothing.
    if abacus_config.is_disabled():
        return 0
    cfg = abacus_config.load_config()
    if not abacus_config.gate_enabled(cfg):
        return 0
    # Being installed is not being agreed to. Until the governing settings are
    # acknowledged this hook denies nothing and spawns nothing — no bd, no npx,
    # no state write (adr/014). Placed above the workspace check so it also
    # covers non_beads_project="block", which is the most invasive thing the
    # gate can do and so the last thing that should arrive unannounced.
    if not consent.is_acknowledged(cfg):
        return 0

    cwd = hook_io.payload_cwd(payload)

    if not beads.has_workspace(cwd):
        if abacus_config.non_beads_mode(cfg) == "block":
            hook_io.deny(DENY_NO_WORKSPACE)
        return 0

    session = hook_io.session_id(payload)
    state = state_store.load(session)
    if _fresh_allow(state, cwd, _cache_ttl(cfg)):
        return 0

    status = beads.in_progress(cwd=cwd)
    if not status["available"]:
        # bd absent, timed out, or no database resolved (rc=1). Distinct from
        # "nothing claimed" and must never block — see beads.in_progress.
        hook_io.log("bd unavailable here; allowing edit without attribution")
        return 0

    issues = status["issues"]
    if not issues:
        # Not cached, deliberately — see DEFAULT_GATE_CACHE_TTL_S.
        hook_io.deny(DENY_NO_TASK)
        return 0

    import time

    state_store.update(session, {"gate_allow": {"cwd": cwd, "at": time.time()}})
    _track(session, beads.most_recent(issues), cfg)
    return 0


if __name__ == "__main__":
    hook_io.guard(main)
