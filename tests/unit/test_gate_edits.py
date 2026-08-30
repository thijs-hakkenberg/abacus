"""RED: the PreToolUse gate — the enforcement core.

This is the only script permitted to influence whether a tool runs, so it gets
the densest tests. Two properties matter above all:

1. It denies when, and only when, a beads workspace exists and nothing is claimed.
2. Every other situation — bd missing, bd broken, no workspace, plugin disabled,
   malformed payload — fails OPEN. A gate that blocks edits because its own
   tooling broke is worse than no gate.
"""

import json

import pytest

from conftest import pre_tool_payload

CLAIMED = [{
    "id": "bd-a1b2", "title": "implement the thing", "status": "in_progress",
    "assignee": "dev", "updated_at": "2026-08-05T21:34:12Z",
}]


def _gate(harness, payload=None, **kw):
    return harness.run_hook("gate_edits.py", payload or pre_tool_payload(), **kw)


# ── the deny path ───────────────────────────────────────────────────────────

def test_denies_when_workspace_exists_and_nothing_is_claimed(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    res = _gate(harness)
    assert res.rc == 0, "must communicate via JSON, not a non-zero exit"
    assert res.permission_decision == "deny"


def test_deny_names_the_plugin_and_gives_runnable_remediation(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    reason = _gate(harness).reason
    assert "abacus" in reason
    assert "bd update" in reason and "--claim" in reason
    assert "bd create" in reason
    assert "ABACUS_DISABLE" in reason, "the escape hatch must be discoverable"


def test_deny_output_is_the_documented_hook_envelope(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    data = _gate(harness).json
    hso = data["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert isinstance(hso["permissionDecisionReason"], str)


@pytest.mark.parametrize("tool", ["Edit", "Write", "NotebookEdit"])
def test_all_three_mutating_tools_are_gated(harness, tool):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    res = _gate(harness, pre_tool_payload(tool=tool))
    assert res.permission_decision == "deny"


# ── the allow paths ─────────────────────────────────────────────────────────

def test_allows_when_a_task_is_claimed(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", CLAIMED)
    res = _gate(harness)
    assert res.rc == 0
    assert res.permission_decision != "deny"


def test_allows_when_bd_is_not_installed(harness):
    harness.make_beads_project()
    harness.remove_bd()
    assert _gate(harness).permission_decision != "deny"


def test_allows_when_bd_has_no_database(harness):
    """The real failure seen in the beads clone: rc=1, 'no beads database found'.
    A stale .beads/ directory must not brick every edit in the repo."""
    harness.make_beads_project()
    harness.set_bd("list", stdout="", rc=1)
    assert _gate(harness).permission_decision != "deny"


def test_allows_in_a_project_with_no_beads_workspace(harness):
    harness.set_bd_json("list", [])
    assert _gate(harness).permission_decision != "deny"


def test_allows_when_disabled_by_env_var(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    res = _gate(harness, ABACUS_DISABLE="1")
    assert res.permission_decision != "deny"


def test_allows_when_disabled_by_marker_file(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    (harness.state_dir / "disabled").write_text("")
    assert _gate(harness).permission_decision != "deny"


def test_allows_when_the_gate_is_switched_off_in_config(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    harness.write_config({"gate": {"enabled": False}})
    assert _gate(harness).permission_decision != "deny"


def test_disabled_gate_makes_no_bd_calls_at_all(harness):
    """The kill switch must be cheap — no subprocess spawn when switched off."""
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    _gate(harness, ABACUS_DISABLE="1")
    assert harness.bd_calls() == []


def test_non_gated_tool_is_ignored_without_spawning_bd(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    res = _gate(harness, pre_tool_payload(tool="Read"))
    assert res.permission_decision != "deny"
    assert harness.bd_calls() == []


# ── the non-beads-project modes ─────────────────────────────────────────────

def test_block_mode_denies_even_without_a_workspace(harness):
    harness.write_config({"gate": {"non_beads_project": "block"}})
    harness.set_bd_json("list", [])
    res = _gate(harness)
    assert res.permission_decision == "deny"
    assert "beads" in res.reason.lower()


def test_off_mode_allows_without_a_workspace(harness):
    harness.write_config({"gate": {"non_beads_project": "off"}})
    harness.set_bd_json("list", [])
    assert _gate(harness).permission_decision != "deny"


# ── lazy snapshot repair ────────────────────────────────────────────────────

def test_records_the_claimed_task_when_state_has_none(harness):
    """If the watcher missed the claim, the gate repairs attribution itself —
    otherwise the task would close with no baseline and get zero cost."""
    harness.make_beads_project()
    harness.set_bd_json("list", CLAIMED)
    harness.set_ccusage_session("sess-1", cost=4.0, tokens=400)
    _gate(harness)
    state = harness.read_state("sess-1")
    assert state is not None
    assert state["current_task"] == "bd-a1b2"
    assert state["snapshot"]["cost"] == 4.0


def test_does_not_re_snapshot_a_task_already_being_tracked(harness):
    """Re-snapshotting on every edit would reset the baseline and lose the cost
    accrued so far."""
    harness.make_beads_project()
    harness.set_bd_json("list", CLAIMED)
    harness.write_state("sess-1", {
        "current_task": "bd-a1b2",
        "claimed_at": "2026-08-05T21:00:00.000Z",
        "snapshot": {"cost": 1.0, "tokens": 100, "ok": True},
    })
    _gate(harness)
    state = harness.read_state("sess-1")
    assert state["snapshot"]["cost"] == 1.0, "existing baseline must be preserved"
    assert harness.npx_calls() == [], "no ccusage spawn when already tracking"


def test_switching_to_a_different_claimed_task_takes_a_fresh_baseline(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", CLAIMED)
    harness.set_ccusage_session("sess-1", cost=9.0, tokens=900)
    harness.write_state("sess-1", {
        "current_task": "bd-OLD",
        "snapshot": {"cost": 1.0, "tokens": 100, "ok": True},
    })
    _gate(harness)
    state = harness.read_state("sess-1")
    assert state["current_task"] == "bd-a1b2"
    assert state["snapshot"]["cost"] == 9.0


def test_gate_does_not_spawn_ccusage_on_the_deny_path(harness):
    """The hot path must stay cheap: no npx when there is nothing to attribute."""
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    _gate(harness)
    assert harness.npx_calls() == []


def test_multiple_claimed_tasks_attribute_to_the_most_recent(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [
        {"id": "bd-old", "status": "in_progress", "updated_at": "2026-08-05T10:00:00Z"},
        {"id": "bd-new", "status": "in_progress", "updated_at": "2026-08-05T21:00:00Z"},
    ])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=200)
    _gate(harness)
    assert harness.read_state("sess-1")["current_task"] == "bd-new"


# ── robustness ──────────────────────────────────────────────────────────────

def test_malformed_payload_fails_open_without_traceback(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    res = harness.run_hook("gate_edits.py", "not-a-dict")
    assert res.rc == 0
    assert res.permission_decision != "deny"
    assert "Traceback" not in res.stderr


def test_empty_stdin_fails_open(harness):
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent.parent / "hooks" / "scripts" / "gate_edits.py"
    proc = subprocess.run([sys.executable, str(script)], input="", capture_output=True,
                          text=True, cwd=str(harness.project), env=harness.env(), timeout=30)
    assert proc.returncode == 0
    assert "deny" not in proc.stdout


def test_gate_uses_the_payload_cwd_not_the_process_cwd(harness):
    """A subagent's hook can run with a different cwd than the payload names."""
    project = harness.make_beads_project()
    harness.set_bd_json("list", [])
    res = harness.run_hook("gate_edits.py",
                           pre_tool_payload(cwd=project),
                           cwd=harness.tmp)
    assert res.permission_decision == "deny", "workspace should be found via payload cwd"


def test_gate_completes_well_within_its_timeout(harness):
    import time

    harness.make_beads_project()
    harness.set_bd_json("list", CLAIMED)
    harness.write_state("sess-1", {"current_task": "bd-a1b2",
                                   "snapshot": {"cost": 1.0, "tokens": 1, "ok": True}})
    started = time.time()
    _gate(harness)
    assert time.time() - started < 5.0
