"""RED: SessionStart / PreCompact priming.

This is the only hook that spends tokens, so it is the one place where "enforce
tracking without spending tokens to enforce it" can be violated. Measured on bd
1.1.2: `bd prime --hook-json` emits 4,854 characters (~1,200 tokens) of workflow
manual, *every session*. The gate needs none of that to work — it is mechanical —
so the default is a compact primer that names the gate and its two remediation
commands, with the full manual available on request (adr/009).

The other property under test is that priming must never reset attribution.
SessionStart fires on resume and compaction too, and a baseline reset mid-task
would silently zero everything the task had spent up to that point.
"""

import json

import pytest

from conftest import session_payload

ISSUE = {"id": "bd-a1b2", "title": "implement the thing", "status": "in_progress",
         "updated_at": "2026-08-05T21:34:12Z"}

PRIME_JSON = {"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "# Beads Workflow Context\n" + ("manual " * 600),
}}


def _start(harness, payload=None, **kw):
    return harness.run_hook("session_start.py", payload or session_payload(), **kw)


def _context(result):
    data = result.json or {}
    return (data.get("hookSpecificOutput") or {}).get("additionalContext", "")


# ── the compact primer ──────────────────────────────────────────────────────

def test_startup_in_a_beads_project_emits_a_primer(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    res = _start(harness)
    assert res.rc == 0
    assert _context(res), "the agent needs to know the gate exists"


def test_the_primer_names_the_commands_that_satisfy_the_gate(harness):
    """A primer that says "track your work" without the exact commands just
    converts a token cost into a denial and a retry."""
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    context = _context(_start(harness))
    assert "bd ready" in context
    assert "--claim" in context
    assert "bd create" in context


def test_the_compact_primer_is_a_small_fraction_of_bd_prime(harness):
    """bd prime is ~4,850 chars. The budget here is a rounding error by
    comparison — this hook runs every single session."""
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    harness.set_bd_json("prime", PRIME_JSON)
    assert len(_context(_start(harness))) < 700


def test_the_full_bd_prime_manual_is_not_emitted_by_default(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    harness.set_bd_json("prime", PRIME_JSON)
    _start(harness)
    assert not [c for c in harness.bd_calls() if "prime" in c], \
        "bd prime should not even be spawned by default"


def test_full_prime_mode_passes_bds_own_manual_through(harness):
    """Opt-in: a user who wants the full manual gets bd's own output verbatim
    rather than a paraphrase that could drift from bd's actual behaviour."""
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    harness.set_bd_json("prime", PRIME_JSON)
    harness.write_config({"prime": {"mode": "full"}})
    context = _context(_start(harness))
    assert "Beads Workflow Context" in context


def test_prime_can_be_switched_off_entirely(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    harness.write_config({"prime": {"mode": "off"}})
    res = _start(harness)
    assert res.rc == 0
    assert _context(res) == ""


def test_the_primer_mentions_the_current_task_when_one_is_claimed(harness):
    """On resume, the agent has lost the conversation but the task is still
    open; naming it is what prevents a duplicate claim."""
    harness.make_beads_project()
    harness.set_bd_json("list", [ISSUE])
    context = _context(_start(harness, session_payload(source="resume")))
    assert "bd-a1b2" in context


def test_no_primer_in_a_project_without_beads(harness):
    """Nothing to enforce here, so nothing to say. Spending tokens telling the
    agent about a tracker this repo does not use is pure waste."""
    res = _start(harness)
    assert res.rc == 0
    assert _context(res) == ""


def test_no_primer_when_the_plugin_is_disabled(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    res = _start(harness, ABACUS_DISABLE="1")
    assert _context(res) == ""


def test_priming_is_skipped_when_a_beads_plugin_is_already_installed(harness):
    """beads ships its own SessionStart primer. Two primers in one context is
    the same manual twice."""
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    settings = harness.home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"enabledPlugins": {"beads@some-marketplace": True}}))
    assert _context(_start(harness)) == ""


# ── the baseline ────────────────────────────────────────────────────────────

def test_startup_records_a_session_baseline(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    harness.set_ccusage_session("sess-1", cost=1.5, tokens=1500)
    _start(harness)
    state = harness.read_state("sess-1")
    assert state["session_id"] == "sess-1"
    assert state["started_at"]


def test_startup_adopts_a_task_that_is_already_claimed(harness):
    """A task claimed in another terminal before this session opened still needs
    a baseline, or its cost would only start counting at the first edit."""
    harness.make_beads_project()
    harness.set_bd_json("list", [ISSUE])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    _start(harness)
    state = harness.read_state("sess-1")
    assert state["current_task"] == "bd-a1b2"
    assert state["snapshot"]["cost"] == 2.0


def test_resume_preserves_an_existing_baseline(harness):
    """ccusage totals are cumulative per session id, so the original baseline
    still diffs correctly after a resume. Replacing it would discard every
    dollar the task had already spent."""
    harness.make_beads_project()
    harness.set_bd_json("list", [ISSUE])
    harness.write_state("sess-1", {"current_task": "bd-a1b2",
                                   "claimed_at": "2026-08-05T20:00:00Z",
                                   "snapshot": {"cost": 1.0, "tokens": 100, "ok": True}})
    harness.set_ccusage_session("sess-1", cost=8.0, tokens=8000)
    _start(harness, session_payload(source="resume"))
    state = harness.read_state("sess-1")
    assert state["snapshot"]["cost"] == 1.0
    assert state["claimed_at"] == "2026-08-05T20:00:00Z"


def test_compaction_preserves_the_baseline(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [ISSUE])
    harness.write_state("sess-1", {"current_task": "bd-a1b2",
                                   "snapshot": {"cost": 3.0, "tokens": 300, "ok": True}})
    harness.set_ccusage_session("sess-1", cost=9.0, tokens=9000)
    _start(harness, session_payload(source="compact"))
    assert harness.read_state("sess-1")["snapshot"]["cost"] == 3.0


def test_precompact_preserves_the_baseline_and_re_primes(harness):
    """PreCompact reuses this script with a flag; the agent is about to lose the
    conversation, so it needs the primer again but not a new baseline."""
    harness.make_beads_project()
    harness.set_bd_json("list", [ISSUE])
    harness.write_state("sess-1", {"current_task": "bd-a1b2",
                                   "snapshot": {"cost": 3.0, "tokens": 300, "ok": True}})
    harness.set_ccusage_session("sess-1", cost=9.0, tokens=9000)
    res = harness.run_hook("session_start.py",
                           session_payload(event="PreCompact", source="manual"),
                           extra_args=("--precompact",))
    assert harness.read_state("sess-1")["snapshot"]["cost"] == 3.0
    assert "bd-a1b2" in _context(res)


# ── robustness ──────────────────────────────────────────────────────────────

def test_the_hook_event_name_matches_the_event(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    data = _start(harness).json or {}
    assert (data.get("hookSpecificOutput") or {}).get("hookEventName") == "SessionStart"


def test_it_never_emits_a_permission_decision(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    assert _start(harness).permission_decision is None


def test_a_broken_bd_still_lets_the_session_open(harness):
    harness.make_beads_project()
    harness.set_bd("list", stdout="", rc=1)
    res = _start(harness)
    assert res.rc == 0
    assert "Traceback" not in res.stderr


def test_a_missing_bd_still_lets_the_session_open(harness):
    harness.make_beads_project()
    harness.remove_bd()
    assert _start(harness).rc == 0


def test_a_malformed_payload_fails_open(harness):
    harness.make_beads_project()
    res = harness.run_hook("session_start.py", "not-a-dict")
    assert res.rc == 0
    assert "Traceback" not in res.stderr


def test_old_state_files_are_pruned_at_startup(harness):
    """Nothing else ever cleans these up; without this the state dir grows one
    file per session forever."""
    import os
    import time

    harness.make_beads_project()
    harness.set_bd_json("list", [])
    stale = harness.state_dir / "session-ancient.json"
    stale.write_text("{}")
    old = time.time() - (40 * 86400)
    os.utime(stale, (old, old))
    _start(harness)
    assert not stale.exists()


def test_pruning_never_deletes_the_user_config(harness):
    import os
    import time

    harness.make_beads_project()
    harness.set_bd_json("list", [])
    harness.write_config({"statusline": False})
    config = harness.state_dir / "config.json"
    old = time.time() - (400 * 86400)
    os.utime(config, (old, old))
    _start(harness)
    assert config.exists(), "user-authored config is not disposable bookkeeping"
