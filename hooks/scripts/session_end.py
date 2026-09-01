#!/usr/bin/env python3
"""SessionEnd — write out an unfinished task's cost before the session is gone.

A task still claimed when the session closes has spent real money. This is the
last moment anything can record it: the ccusage snapshot in state is the only
record of where the task started, and once the state file is pruned that baseline
is unrecoverable. So it is written now, marked ``abacus_partial=true``.

Partial is not a lesser answer, it is a different claim: "this much was spent by
this session, and the task is not finished". A later close reads those figures
back and adds to them (see ``attribution.carried_partial``), so a task worked on
across three sessions still reports its whole cost rather than only its last
sitting.

Pushing is opt-in. ``bd dolt push`` reaches a remote, can prompt, and can fail
noisily on a session the user has just closed — that is not something to do by
default on the user's behalf. When it is enabled it runs *after* the metadata
write, or the push would ship the repo without the attribution it exists to sync.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import attribution  # noqa: E402
import beads  # noqa: E402
import consent  # noqa: E402
import hook_io  # noqa: E402
import state_store  # noqa: E402
import abacus_config  # noqa: E402


def _sync(cfg, cwd):
    """Best-effort remote sync. A failure here is logged, never raised."""
    mode = str(cfg.get("sync_on_session_end") or "off").lower()
    if mode == "push":
        ok = beads.dolt_push(cwd=cwd)
    elif mode == "sync":
        ok = beads.dolt_sync(cwd=cwd)
    else:
        return
    if not ok:
        # The session is already over; there is no one to prompt and nothing
        # useful to retry. The metadata is committed locally either way.
        hook_io.log("bd dolt %s failed; attribution is still recorded locally" % mode)


def main():
    payload = hook_io.read_payload()

    if abacus_config.is_disabled():
        return 0

    session = hook_io.session_id(payload)
    cwd = hook_io.payload_cwd(payload)
    cfg = abacus_config.load_config()

    # Of everything consent gates, `sync_on_session_end` is the only effect that
    # leaves the machine and the only one the user cannot inspect afterwards — so
    # the check sits above both the write and the push (adr/014). Pruning still
    # runs below: the state directory is ours, and tidying it acts on nobody.
    if not consent.is_acknowledged(cfg):
        state_store.prune(int(cfg.get("state_max_age_days") or 14))
        return 0

    state = state_store.load(session)
    current = state.get("current_task")
    if current:
        attribution.finalise(session, current, cfg, partial=True, cwd=cwd)
        attribution.clear_current(session)
        hook_io.log("session ended with %s open; recorded as partial" % current)

    # Outside the branch above: a session that closed all its tasks properly has
    # attribution sitting in the local Dolt DB and nothing else will ever ship
    # it. Syncing only when a task was left open would sync only sloppy sessions.
    if beads.has_workspace(cwd):
        _sync(cfg, cwd)

    # Runs whether or not a task was open: this is the plugin's only guaranteed
    # cleanup point for sessions that never claimed anything.
    state_store.prune(int(cfg.get("state_max_age_days") or 14))
    return 0


if __name__ == "__main__":
    hook_io.guard(main)
