#!/usr/bin/env python3
"""Stop — repair attribution when a task boundary happened outside our view.

The Bash watcher sees ``bd close`` only when Claude runs it as a Bash tool call.
A close typed by the user in another terminal, or run inside a script, or spelled
in a form the tokeniser does not recognise, leaves this session still believing
the task is open — and every subsequent turn keeps accruing cost against an issue
that finished an hour ago.

Stop is the natural place to notice, because it fires at the end of every turn
and the check is one ``bd list`` that the plugin is already making elsewhere.

Since 0.6.0 it carries a second repair of the same shape: a HEAD sweep for commits
the Bash watcher's verb list never matched (adr/015). Same reasoning — a boundary
that happened outside our view — and the same asymmetry, in that Stop is the last
surface that fires while the session id is still knowable.

Two things this hook deliberately does *not* do. It does not finalise a task that
is still in progress: Stop fires on every turn, so that would scatter a dozen
partial figures across one afternoon's work. And it never blocks — the gate
already enforces at the edit boundary (adr/002), so there is nothing here worth
interrupting the user for.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import attribution  # noqa: E402
import beads  # noqa: E402
import commit_capture  # noqa: E402
import consent  # noqa: E402
import hook_io  # noqa: E402
import state_store  # noqa: E402
import abacus_config  # noqa: E402


def main():
    payload = hook_io.read_payload()

    # We are already inside a Stop invocation; going further risks re-triggering
    # the same hook chain.
    if payload.get("stop_hook_active"):
        return 0
    if abacus_config.is_disabled():
        return 0

    cfg = abacus_config.load_config()
    # Repair is still a write, and this hook fires on every turn — it would be the
    # first thing to breach the invariant if it were exempt (adr/014).
    if not consent.is_acknowledged(cfg):
        return 0

    session = hook_io.session_id(payload)
    cwd = hook_io.payload_cwd(payload)

    # Above the `current_task` return below, deliberately, and this is the whole
    # reason the sweep lives at Stop rather than only in the watcher. A commit made
    # by a shell script, a Makefile target or `gh pr merge` moves HEAD with nothing
    # in the Bash command for the verb list to match, and a `Beads-Task:` trailer
    # names its own tasks and needs no claim at all — so gating the sweep on
    # `current_task` would make the *strongest* tier of evidence the only one a
    # sweep could miss. Stop is also the last surface that fires while the session
    # is still open, which is to say while its id is still knowable.
    #
    # It stays cheap in the quiet case: capture's first check is a filesystem walk
    # for `.beads`, and HEAD not having moved costs two `rev-parse` calls and no
    # write. See `commit_capture.capture`.
    commit_capture.capture(session, cwd, cfg)

    state = state_store.load(session)
    current = state.get("current_task")
    if not current:
        # Nothing is being tracked, so there is nothing to reconcile and no
        # reason to spawn bd on the end of every turn.
        return 0

    status = beads.in_progress(cwd=cwd)
    if not status["available"]:
        # bd is missing, wedged, or the workspace vanished. "Cannot tell" is not
        # "was closed" — leave the state alone and try again next turn.
        return 0

    claimed_ids = {str(i.get("id")) for i in status["issues"]}
    if current in claimed_ids:
        return 0

    # The task left in_progress without us seeing it. Whether that is a finish or
    # an un-claim decides whether the figures are final: writing partial=false on
    # a task someone merely moved back to open would claim it was completed.
    issue = beads.show(current, cwd=cwd) or {}
    issue_status = str(issue.get("status") or "").lower()
    partial = issue_status != "closed"

    attribution.finalise(session, current, cfg, partial=partial, cwd=cwd)
    attribution.clear_current(session)
    hook_io.log("reconciled %s at Stop (status=%s)" % (current, issue_status or "unknown"))
    return 0


if __name__ == "__main__":
    hook_io.guard(main)
