"""RED: the statusline, Stop reconciliation, and SessionEnd finalisation.

These three share a theme: they run often and must be near-free, and none of them
may block the user. The statusline in particular fires on every prompt the user
types, so it reads cached state and never spawns a subprocess.

SessionEnd carries the one piece of real logic here: a task still open when the
session closes has spent real money that would otherwise be lost. It is written
out as `abacus_partial=true`, which a later close accumulates onto rather than
overwrites.
"""

import json

import pytest

from conftest import session_payload

ISSUE = {"id": "bd-a1b2", "title": "implement the thing", "status": "in_progress",
         "updated_at": "2026-08-05T21:34:12Z"}


def _claimed_state(**extra):
    state = {
        "session_id": "sess-1",
        "current_task": "bd-a1b2",
        "current_title": "implement the thing",
        "claimed_at": "2026-08-05T21:00:00Z",
        "snapshot": {"cost": 1.0, "tokens": 1000, "input_tokens": 100,
                     "output_tokens": 200, "cache_read_tokens": 300,
                     "cache_creation_tokens": 400, "models": [], "ok": True},
    }
    state.update(extra)
    return state


def _metadata_from_calls(harness):
    for call in harness.bd_calls():
        if "--set-metadata" not in call:
            continue
        parts = call.split()
        pairs = {}
        for i, part in enumerate(parts):
            if part == "--set-metadata" and i + 1 < len(parts):
                key, _, value = parts[i + 1].partition("=")
                pairs[key] = value
        return pairs
    return {}


def _context(result):
    data = result.json or {}
    return (data.get("hookSpecificOutput") or {}).get("additionalContext", "")


# ── the statusline (UserPromptSubmit) ───────────────────────────────────────

def test_the_statusline_shows_the_current_task(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    res = harness.run_hook("prompt_statusline.py",
                           session_payload(event="UserPromptSubmit"))
    assert "bd-a1b2" in _context(res)


def test_the_statusline_spawns_no_subprocess(harness):
    """This fires on every prompt. A 0.4s bd query here would be felt directly
    as typing latency, so it reads cached state only."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.run_hook("prompt_statusline.py", session_payload(event="UserPromptSubmit"))
    assert harness.calls() == []


def test_the_statusline_is_one_line(harness):
    """It is prepended to the user's own prompt; more than a line is an
    intrusion on their context, not a status."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    res = harness.run_hook("prompt_statusline.py", session_payload(event="UserPromptSubmit"))
    assert len(_context(res).strip().splitlines()) == 1


def test_the_statusline_is_silent_when_no_task_is_claimed(harness):
    """Silence is correct: the gate will speak up if an edit is attempted, and
    nagging on every prompt is what makes a plugin get uninstalled."""
    harness.make_beads_project()
    res = harness.run_hook("prompt_statusline.py", session_payload(event="UserPromptSubmit"))
    assert _context(res) == ""


def test_the_statusline_can_be_switched_off(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.write_config({"statusline": False})
    res = harness.run_hook("prompt_statusline.py", session_payload(event="UserPromptSubmit"))
    assert _context(res) == ""


def test_the_statusline_declares_the_right_event_name(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    res = harness.run_hook("prompt_statusline.py", session_payload(event="UserPromptSubmit"))
    data = res.json or {}
    assert (data.get("hookSpecificOutput") or {}).get("hookEventName") == "UserPromptSubmit"


def test_the_statusline_never_blocks_a_prompt(harness):
    harness.make_beads_project()
    res = harness.run_hook("prompt_statusline.py", "not-a-dict")
    assert res.rc == 0
    assert res.permission_decision is None


# ── Stop reconciliation ─────────────────────────────────────────────────────

def test_stop_writes_out_a_task_closed_behind_the_watchers_back(harness):
    """`bd close` typed in another terminal leaves state stale; without this the
    task keeps accruing another turn's cost."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("list", [])
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    harness.run_hook("stop_reconcile.py", session_payload(event="Stop"))
    assert _metadata_from_calls(harness).get("abacus_cost_usd_estimate") == "2.0"
    assert not harness.read_state("sess-1").get("current_task")


def test_stop_leaves_a_still_open_task_alone(harness):
    """Stop fires at the end of every turn. Finalising an open task here would
    write a dozen partial figures over one afternoon."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("list", [ISSUE])
    harness.run_hook("stop_reconcile.py", session_payload(event="Stop"))
    assert not [c for c in harness.bd_calls() if "--set-metadata" in c]
    assert harness.read_state("sess-1")["current_task"] == "bd-a1b2"


def test_stop_respects_the_loop_guard(harness):
    """stop_hook_active means we are already inside a Stop; continuing would
    recurse."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("list", [])
    harness.run_hook("stop_reconcile.py",
                     session_payload(event="Stop", stop_hook_active=True))
    assert harness.calls() == []


def test_stop_does_not_block_the_turn_from_ending(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("list", [])
    res = harness.run_hook("stop_reconcile.py", session_payload(event="Stop"))
    assert res.rc == 0
    decision = (res.json or {}).get("decision")
    assert decision != "block"


def test_stop_is_cheap_when_nothing_is_being_tracked(harness):
    harness.make_beads_project()
    harness.run_hook("stop_reconcile.py", session_payload(event="Stop"))
    assert harness.calls() == [], "nothing to reconcile, nothing to spawn"


def test_stop_fails_open_on_a_malformed_payload(harness):
    harness.make_beads_project()
    res = harness.run_hook("stop_reconcile.py", "not-a-dict")
    assert res.rc == 0
    assert "Traceback" not in res.stderr


# ── SessionEnd ──────────────────────────────────────────────────────────────

def test_session_end_writes_an_open_task_as_partial(harness):
    """The session is over but the task is not. This spend is real and would
    otherwise be lost entirely."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [ISSUE])
    harness.set_ccusage_session("sess-1", cost=6.0, tokens=6000)
    harness.run_hook("session_end.py", session_payload(event="SessionEnd", reason="clear"))
    meta = _metadata_from_calls(harness)
    assert meta["abacus_cost_usd_estimate"] == "5.0"
    assert meta["abacus_partial"] == "true", "the task is unfinished, and says so"


def test_session_end_does_nothing_when_no_task_is_open(harness):
    harness.make_beads_project()
    harness.run_hook("session_end.py", session_payload(event="SessionEnd"))
    assert not [c for c in harness.bd_calls() if "--set-metadata" in c]


def test_session_end_pushes_when_configured(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [ISSUE])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    harness.write_config({"sync_on_session_end": "push"})
    harness.run_hook("session_end.py", session_payload(event="SessionEnd"))
    assert any("dolt push" in c for c in harness.bd_calls())


def test_session_end_does_not_push_by_default(harness):
    """Pushing is outward-facing and can fail noisily on a session the user just
    closed; it stays opt-in."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [ISSUE])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    harness.run_hook("session_end.py", session_payload(event="SessionEnd"))
    assert not any("dolt" in c for c in harness.bd_calls())


def test_it_still_pushes_when_every_task_was_closed_cleanly(harness):
    """The well-behaved case must sync too. A session that closed its tasks
    properly has metadata sitting in the local Dolt DB and nothing else will ever
    ship it — tying the push to "a task was left open" syncs only sloppy
    sessions."""
    harness.make_beads_project()
    harness.write_config({"sync_on_session_end": "push"})
    harness.run_hook("session_end.py", session_payload(event="SessionEnd"))
    assert any("dolt push" in c for c in harness.bd_calls())


def test_the_push_happens_after_the_metadata_write(harness):
    """Pushing first would ship the repo without the attribution just recorded."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [ISSUE])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    harness.write_config({"sync_on_session_end": "push"})
    harness.run_hook("session_end.py", session_payload(event="SessionEnd"))
    calls = harness.bd_calls()
    write = next(i for i, c in enumerate(calls) if "--set-metadata" in c)
    push = next(i for i, c in enumerate(calls) if "dolt push" in c)
    assert write < push


def test_session_end_prunes_stale_state(harness):
    import os
    import time

    harness.make_beads_project()
    stale = harness.state_dir / "session-ancient.json"
    stale.write_text("{}")
    old = time.time() - (40 * 86400)
    os.utime(stale, (old, old))
    harness.run_hook("session_end.py", session_payload(event="SessionEnd"))
    assert not stale.exists()


def test_session_end_never_blocks(harness):
    harness.make_beads_project()
    res = harness.run_hook("session_end.py", session_payload(event="SessionEnd"))
    assert res.rc == 0
    assert res.permission_decision is None


def test_session_end_survives_a_failed_push(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [ISSUE])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    harness.set_bd("dolt", stdout="error: no remote", rc=1)
    harness.write_config({"sync_on_session_end": "push"})
    res = harness.run_hook("session_end.py", session_payload(event="SessionEnd"))
    assert res.rc == 0


def test_session_end_is_inert_when_disabled(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    res = harness.run_hook("session_end.py", session_payload(event="SessionEnd"),
                           ABACUS_DISABLE="1")
    assert res.rc == 0
    assert harness.calls() == []


def test_session_end_fails_open_on_a_malformed_payload(harness):
    harness.make_beads_project()
    res = harness.run_hook("session_end.py", "not-a-dict")
    assert res.rc == 0
    assert "Traceback" not in res.stderr
