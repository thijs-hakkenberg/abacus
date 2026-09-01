"""RED: every hook, driven unacknowledged, must do nothing.

`test_consent.py` covers the record and the fingerprint. This covers the thing
that actually matters — that the invariant holds at each of the six places abacus
could act on its own:

    gate_edits.py          the only script that can deny
    session_start.py       the only script that writes to a user's repository
    watch_bd_commands.py   the attribution write
    stop_reconcile.py      the repair write
    session_end.py         the partial write, and the push to a remote
    prompt_statusline.py   the surface that asks

Each is driven as a real subprocess with the acknowledgement revoked, and the
assertion is on what reached `bd` — the recorded argv, not a return value. A
consent check that a unit test confirms and a subprocess quietly skips is worth
nothing, which is why none of these call into the library directly.

Deliberately excluded: `audit.py --fix`. Consent gates *unprompted* action, and
someone typing `/abacus:audit fix` is not being acted upon. Gating it would mean
the notice tells you to run a command that then refuses to run.
"""

import json

import pytest

from conftest import post_bash_payload, pre_tool_payload, session_payload

IN_PROGRESS = [{"id": "ab-1", "title": "tracked work", "status": "in_progress",
                "updated_at": "2026-09-01T09:00:00Z",
                "started_at": "2026-09-01T09:00:00Z"}]


def metadata_writes(harness):
    return [c for c in harness.bd_calls() if "--set-metadata" in c]


def tracking_state(harness, session="sess-1"):
    """State as it looks mid-task: a claim, with a baseline to diff against."""
    harness.write_state(session, {
        "session_id": session,
        "current_task": "ab-1",
        "current_title": "tracked work",
        "claimed_at": "2026-09-01T09:00:00Z",
        "snapshot": {"available": True, "cost": 1.0, "tokens_total": 1000,
                     "tokens_in": 10, "tokens_out": 20, "tokens_cache_read": 900,
                     "tokens_cache_write": 70, "models": ["claude-fable-5"]},
        "snapshot_source": "watch-claim",
    })


# ── the gate: no denial ─────────────────────────────────────────────────────

def test_the_gate_allows_an_untracked_edit_while_unacknowledged(harness):
    """The one case the gate exists to deny, denied only after consent.

    Enforcement arriving unannounced is the failure this whole feature prevents:
    the user installs a plugin, and the next edit in an unrelated repository is
    refused by something they have not read a word about.
    """
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    harness.revoke_acknowledgement()
    result = harness.run_hook("gate_edits.py", pre_tool_payload(cwd=harness.project))
    assert result.rc == 0
    assert result.permission_decision is None, (
        "an unacknowledged plugin must not deny: %r" % result.stdout)


def test_the_gate_denies_the_same_edit_once_acknowledged(harness):
    """The control for the test above — otherwise it would pass with no gate."""
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    result = harness.run_hook("gate_edits.py", pre_tool_payload(cwd=harness.project))
    assert result.permission_decision == "deny"


def test_block_mode_does_not_block_while_unacknowledged(harness):
    """`non_beads_project: block` is the most invasive setting there is.

    It makes every repository without a workspace un-editable, which is precisely
    the configuration nobody should discover by having an edit refused.
    """
    harness.write_config({"gate": {"non_beads_project": "block"}}, acknowledge=False)
    result = harness.run_hook("gate_edits.py", pre_tool_payload(cwd=harness.project))
    assert result.permission_decision is None


def test_the_gate_takes_no_snapshot_while_unacknowledged(harness):
    """Inert means inert: no bd, no npx, nothing on the hot path."""
    harness.make_beads_project()
    harness.set_bd_json("list", IN_PROGRESS)
    harness.revoke_acknowledgement()
    harness.run_hook("gate_edits.py", pre_tool_payload(cwd=harness.project))
    assert harness.calls() == [], "expected no subprocesses, got %r" % harness.calls()


# ── auto_init: no write to the user's repository ─────────────────────────────

def test_auto_init_creates_nothing_while_unacknowledged(harness):
    """The only code path that writes into a repository the user may not own."""
    project = harness.make_git_project()
    harness.write_config(
        {"auto_init": {"enabled": True, "roots": [str(harness.tmp)]}},
        acknowledge=False)
    harness.run_hook("session_start.py", session_payload(cwd=project), cwd=project)
    assert not any("bd init" in c for c in harness.bd_calls()), (
        "auto_init ran without consent: %r" % harness.bd_calls())


def test_auto_init_runs_once_acknowledged(harness):
    """The control: the same config, agreed to, does initialise."""
    project = harness.make_git_project()
    harness.write_config({"auto_init": {"enabled": True, "roots": [str(harness.tmp)]}})
    harness.run_hook("session_start.py", session_payload(cwd=project), cwd=project)
    assert any("bd init" in c for c in harness.bd_calls())


def test_session_start_asks_instead_of_priming(harness):
    """The install-time surface. The primer describes enforcement; if enforcement
    is paused, describing it would be a lie told at token cost."""
    harness.make_beads_project()
    harness.set_bd_json("list", IN_PROGRESS)
    harness.revoke_acknowledgement()
    result = harness.run_hook("session_start.py", session_payload(cwd=harness.project))
    context = ((result.json or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
    assert "/abacus:acknowledge" in context
    # Asserted against the primers' own wording rather than a phrase like "in
    # progress": the notice says that too, because describing the gate accurately
    # requires it. Both primer shapes are named, since which one would have fired
    # depends on whether a task is claimed — and either would be wrong here.
    assert "Close it with" not in context, (
        "an unacknowledged session must not also emit the active-task primer")
    assert "bd ready --json" not in context, (
        "an unacknowledged session must not also emit the compact primer")


# ── the prompt surface ──────────────────────────────────────────────────────

def test_the_next_prompt_asks_when_the_session_predates_the_install(harness):
    """A plugin installed mid-session never saw a SessionStart, so the first
    prompt afterwards is the earliest moment anything can say so."""
    harness.revoke_acknowledgement()
    result = harness.run_hook("prompt_statusline.py",
                              session_payload(session_id="sess-mid", event="UserPromptSubmit"))
    context = ((result.json or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
    assert "/abacus:acknowledge" in context


def test_the_prompt_surface_asks_once_and_then_stops(harness):
    """Asked on every prompt forever, a notice becomes something to scroll past.

    Once per session is the most it can be worth: the answer is durable, so a
    second ask in the same session carries no information the first did not.
    """
    harness.revoke_acknowledgement()
    first = harness.run_hook("prompt_statusline.py",
                             session_payload(session_id="sess-mid", event="UserPromptSubmit"))
    assert "/abacus:acknowledge" in first.stdout
    second = harness.run_hook("prompt_statusline.py",
                              session_payload(session_id="sess-mid", event="UserPromptSubmit"))
    assert second.stdout.strip() == "", "asked twice in one session: %r" % second.stdout


def test_the_statusline_is_silent_while_unacknowledged(harness):
    """Even with a task tracked, the notice replaces the statusline rather than
    joining it — two competing messages in one line is neither."""
    tracking_state(harness, "sess-1")
    harness.revoke_acknowledgement()
    result = harness.run_hook("prompt_statusline.py",
                              session_payload(session_id="sess-1", event="UserPromptSubmit"))
    assert "tracking ab-1" not in result.stdout


# ── the attribution writes ──────────────────────────────────────────────────

def test_a_close_writes_no_metadata_while_unacknowledged(harness):
    """Metadata on someone's issue is a write to their store of record."""
    harness.make_beads_project()
    harness.set_bd_json("show", IN_PROGRESS)
    harness.set_ccusage_session("sess-1", 3.0, 5000)
    tracking_state(harness, "sess-1")
    harness.revoke_acknowledgement()
    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("bd close ab-1", cwd=harness.project))
    assert metadata_writes(harness) == []


def test_a_close_writes_metadata_once_acknowledged(harness):
    """The control for the test above."""
    harness.make_beads_project()
    harness.set_bd_json("show", IN_PROGRESS)
    harness.set_ccusage_session("sess-1", 3.0, 5000)
    tracking_state(harness, "sess-1")
    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("bd close ab-1", cwd=harness.project))
    assert metadata_writes(harness) != []


def test_stop_reconciliation_writes_nothing_while_unacknowledged(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    harness.set_bd_json("show", [dict(IN_PROGRESS[0], status="closed")])
    harness.set_ccusage_session("sess-1", 3.0, 5000)
    tracking_state(harness, "sess-1")
    harness.revoke_acknowledgement()
    harness.run_hook("stop_reconcile.py", session_payload(session_id="sess-1", event="Stop",
                                                          cwd=harness.project))
    assert metadata_writes(harness) == []


def test_session_end_writes_nothing_while_unacknowledged(harness):
    harness.make_beads_project()
    harness.set_ccusage_session("sess-1", 3.0, 5000)
    tracking_state(harness, "sess-1")
    harness.revoke_acknowledgement()
    harness.run_hook("session_end.py", session_payload(session_id="sess-1", event="SessionEnd",
                                                       cwd=harness.project))
    assert metadata_writes(harness) == []


def test_session_end_never_reaches_a_remote_while_unacknowledged(harness):
    """`sync_on_session_end` leaves the machine. Of everything gated here this is
    the only one whose effect the user cannot inspect afterwards."""
    harness.make_beads_project()
    harness.write_config({"sync_on_session_end": "push"}, acknowledge=False)
    harness.run_hook("session_end.py", session_payload(session_id="sess-1", event="SessionEnd",
                                                       cwd=harness.project))
    assert not any("dolt push" in c for c in harness.bd_calls())


# ── what consent does NOT gate ──────────────────────────────────────────────

def test_an_explicitly_invoked_audit_still_repairs(harness):
    """`/abacus:audit fix` is the user acting, not abacus acting unprompted.

    Gating it would make the notice self-defeating: it would name commands that
    refuse to run until you agree to something the commands themselves do not do.
    """
    harness.make_beads_project()
    harness.set_bd_json("list", [
        dict(IN_PROGRESS[0], id="ab-2", started_at=None),
        {"id": "ab-0", "title": "old work", "status": "closed",
         "created_at": "2026-09-01T08:00:00Z", "started_at": "2026-09-01T09:00:00Z",
         "closed_at": "2026-09-01T10:00:00Z", "updated_at": "2026-09-01T10:00:00Z"},
    ])
    harness.revoke_acknowledgement()
    result = harness.run_hook("audit.py", session_payload(), extra_args=("--fix", "--json"))
    assert result.rc == 0
    assert metadata_writes(harness) != [], "an explicit --fix must still repair"


def test_the_kill_switch_still_wins_over_the_notice(harness):
    """ABACUS_DISABLE=1 means silent, and that outranks asking to be enabled."""
    harness.revoke_acknowledgement()
    result = harness.run_hook("prompt_statusline.py",
                              session_payload(session_id="sess-1", event="UserPromptSubmit"),
                              ABACUS_DISABLE="1")
    assert result.stdout.strip() == ""


# ── acknowledging ───────────────────────────────────────────────────────────

def test_the_acknowledge_command_reports_the_current_state(harness):
    harness.revoke_acknowledgement()
    result = harness.run_hook("acknowledge.py", session_payload(), extra_args=("--json",))
    assert result.rc == 0
    data = json.loads(result.stdout)
    assert data["acknowledged"] is False
    assert data["status"] == "never"
    assert data["settings"]["gate.enabled"] is True


def test_accepting_switches_governance_on(harness):
    """End to end: the deny that did not happen, happens after agreeing."""
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    harness.revoke_acknowledgement()

    before = harness.run_hook("gate_edits.py", pre_tool_payload(cwd=harness.project))
    assert before.permission_decision is None

    accepted = harness.run_hook("acknowledge.py", session_payload(),
                                extra_args=("--accept", "--json"))
    assert json.loads(accepted.stdout)["acknowledged"] is True

    after = harness.run_hook("gate_edits.py", pre_tool_payload(cwd=harness.project))
    assert after.permission_decision == "deny"


def test_acknowledging_survives_a_cosmetic_config_change(harness):
    """The fingerprint has to be narrow enough to be worth having."""
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    harness.write_config({"statusline": False, "ccusage_version": "ccusage@99.0.0"},
                         acknowledge=False)
    harness.run_hook("acknowledge.py", session_payload(), extra_args=("--accept",))
    harness.write_config({"statusline": True, "ccusage_version": "ccusage@20.0.14"},
                         acknowledge=False)
    result = harness.run_hook("gate_edits.py", pre_tool_payload(cwd=harness.project))
    assert result.permission_decision == "deny", "a cosmetic change must not pause the gate"


def test_widening_the_scope_pauses_the_gate_again(harness):
    """Consent to `~/projects` is not consent to every repository on the machine."""
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    harness.write_config({"auto_init": {"enabled": True, "roots": ["~/projects"]}},
                         acknowledge=False)
    harness.run_hook("acknowledge.py", session_payload(), extra_args=("--accept",))
    harness.write_config({"auto_init": {"enabled": True, "roots": []}}, acknowledge=False)
    result = harness.run_hook("gate_edits.py", pre_tool_payload(cwd=harness.project))
    assert result.permission_decision is None


def test_acknowledge_exits_zero_with_no_state_directory(harness):
    """Every script in this plugin exits 0 whatever it finds."""
    result = harness.run_hook("acknowledge.py", session_payload(), extra_args=("--json",),
                              ABACUS_STATE_DIR=str(harness.tmp / "nope" / "deeper"))
    assert result.rc == 0
