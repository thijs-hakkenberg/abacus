#!/usr/bin/env python3
"""UserPromptSubmit — one line saying what the cost is being charged to.

This fires on every prompt the user types, which sets the entire design: it reads
the cached state file and spawns nothing. A ``bd list`` here would be ~0.45s of
latency between pressing return and the agent starting, on every single turn.

It is also silent by default. When nothing is claimed there is no line at all —
the gate speaks up the moment an edit is attempted, so nagging on every prompt
adds a token cost and an irritation to buy a warning the user is about to get
anyway from the one place it can actually be acted on.

It has one other job, and it is here because there is nowhere earlier. A plugin
installed mid-session never sees a ``SessionStart``, so the next prompt the user
types is the first moment abacus can say that it exists and ask whether it may
act (adr/014). That ask happens once per session and then stops — an answer that
is durable does not become more informative for being requested again.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import consent  # noqa: E402
import hook_io  # noqa: E402
import state_store  # noqa: E402
import abacus_config  # noqa: E402
import abacus_time  # noqa: E402

# Kept short deliberately: this is prepended to the user's own prompt, so every
# character is context they did not ask for, once per turn.
MAX_TITLE = 60


def main():
    payload = hook_io.read_payload()

    # The kill switch outranks everything, including asking to be switched on.
    # Someone who set ABACUS_DISABLE=1 has already answered.
    if abacus_config.is_disabled():
        return 0
    cfg = abacus_config.load_config()

    session = hook_io.session_id(payload)
    state = state_store.load(session)

    if not consent.is_acknowledged(cfg):
        # The notice replaces the statusline rather than joining it: two
        # competing messages prepended to one prompt is neither of them. And it
        # is emitted whatever `statusline` says — that setting is about a label
        # for work in progress, not about whether abacus may introduce itself.
        if state.get("consent_asked_at"):
            return 0
        state_store.update(session, {"consent_asked_at": abacus_time.now_iso()})
        hook_io.additional_context(consent.notice(cfg), event="UserPromptSubmit")
        return 0

    if not cfg.get("statusline", True):
        return 0

    issue_id = state.get("current_task")
    if not issue_id:
        return 0

    title = str(state.get("current_title") or "").strip()
    if len(title) > MAX_TITLE:
        title = title[: MAX_TITLE - 1] + "…"

    # Elapsed comes from the claim timestamp already in state, so the line stays
    # subprocess-free while still being the useful part of a status.
    elapsed = abacus_time.minutes_between(state.get("claimed_at"), abacus_time.now_iso())

    line = "[abacus] tracking %s" % issue_id
    if title:
        line += " — %s" % title
    line += " (%dm)" % elapsed

    # One line, no trailing newline: `additional_context` is not a log stream.
    hook_io.additional_context(line, event="UserPromptSubmit")
    return 0


if __name__ == "__main__":
    hook_io.guard(main)
