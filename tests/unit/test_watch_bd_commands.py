"""RED: the PostToolUse Bash watcher — the attribution engine.

This is where cost actually gets attached to a task. It watches Bash commands for
`bd update --claim` (take a baseline) and `bd close` (diff the baseline and write
metadata onto the issue).

Three properties dominate the tests:

1. **It is nearly free on the 95% of Bash calls that are not bd.** No subprocess
   is spawned on that path. Measured end-to-end at ~0.09s, of which ~0.07s is
   bare interpreter startup — the plugin's own share is a string scan, and
   deferring the library imports was measured as no faster than the noise floor.
2. **Its parse must not hallucinate a claim.** `echo "bd update x --claim"` is not
   a claim, and treating it as one would silently mis-attribute a whole task.
3. **The metadata write is a contract.** Downstream readers (`bd show --json`,
   the cost-report skill, any future EA rollup) depend on the exact key names,
   so they are asserted literally rather than via a helper that could drift with
   the implementation.
"""

import json

import pytest

from conftest import post_bash_payload

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
                     "cache_creation_tokens": 400, "models": ["claude-fable-5"],
                     "ok": True},
    }
    state.update(extra)
    return state


def _watch(harness, command, **kw):
    return harness.run_hook("watch_bd_commands.py", post_bash_payload(command), **kw)


def _metadata_from_calls(harness):
    """Parse the k=v pairs out of whichever `bd update --set-metadata` call ran."""
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


# ── the prefilter ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "pytest tests/ -q",
    "git commit -m 'fix: thing'",
    "ls -la",
    "npm run build",
])
def test_non_bd_commands_spawn_nothing(harness, command):
    harness.make_beads_project()
    res = _watch(harness, command)
    assert res.rc == 0
    assert harness.calls() == [], "the 95% path must not spawn a subprocess"


def test_a_bd_read_command_is_not_a_task_boundary(harness):
    """`bd list`/`bd show`/`bd ready` change nothing and must not snapshot."""
    harness.make_beads_project()
    _watch(harness, "bd ready --json")
    assert harness.npx_calls() == []
    assert harness.read_state("sess-1") in (None, {})


def test_the_word_bd_inside_a_quoted_string_is_not_a_claim(harness):
    """A regex over the raw command would fire here. Tokenising must not."""
    harness.make_beads_project()
    _watch(harness, 'echo "bd update bd-FAKE --claim"')
    state = harness.read_state("sess-1") or {}
    assert state.get("current_task") != "bd-FAKE"


def test_a_similarly_named_executable_is_not_bd(harness):
    harness.make_beads_project()
    _watch(harness, "bdiff update bd-a1b2 --claim")
    state = harness.read_state("sess-1") or {}
    assert state.get("current_task") != "bd-a1b2"


def test_an_env_prefixed_invocation_is_still_bd(harness):
    """`BEADS_DIR=/x bd close ...` is a normal way to target another workspace.
    Missing it would drop the attribution write entirely."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "CLAUDE_SESSION_ID=abc bd close bd-a1b2")
    assert [c for c in harness.bd_calls() if "--set-metadata" in c]


# ── status= is the other spelling of claim and close ────────────────────────

def test_setting_status_closed_is_treated_as_a_close(harness):
    """Verified on bd 1.1.2: `bd update <id> --status closed` genuinely closes
    the issue. Watching only `bd close` would silently lose this task's cost."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "bd update bd-a1b2 --status closed")
    meta = _metadata_from_calls(harness)
    assert meta.get("abacus_cost_usd_estimate") == "2.0"
    assert not harness.read_state("sess-1").get("current_task")


def test_setting_status_in_progress_is_treated_as_a_claim(harness):
    harness.make_beads_project()
    harness.set_bd_json("show", [ISSUE])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    _watch(harness, "bd update bd-a1b2 --status in_progress")
    assert harness.read_state("sess-1")["current_task"] == "bd-a1b2"


def test_setting_an_unrelated_status_is_not_a_boundary(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [ISSUE])
    _watch(harness, "bd update bd-a1b2 --status blocked")
    assert not [c for c in harness.bd_calls() if "--set-metadata" in c]
    assert harness.read_state("sess-1")["current_task"] == "bd-a1b2"


# ── claim detection ─────────────────────────────────────────────────────────

def test_a_claim_records_the_task_and_takes_a_baseline(harness):
    harness.make_beads_project()
    harness.set_bd_json("show", [ISSUE])
    harness.set_ccusage_session("sess-1", cost=2.5, tokens=2500)
    _watch(harness, "bd update bd-a1b2 --claim --json")
    state = harness.read_state("sess-1")
    assert state["current_task"] == "bd-a1b2"
    assert state["snapshot"]["cost"] == 2.5
    assert state["claimed_at"], "a claim must record when it happened"


def test_a_claim_records_the_issue_title_for_the_statusline(harness):
    harness.make_beads_project()
    harness.set_bd_json("show", [ISSUE])
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=100)
    _watch(harness, "bd update bd-a1b2 --claim")
    assert harness.read_state("sess-1")["current_title"] == "implement the thing"


def test_a_claim_is_recorded_even_when_bd_show_fails(harness):
    """The id came from a command the user actually ran. Losing the baseline
    because the title lookup failed would silently cost the task its attribution."""
    harness.make_beads_project()
    harness.set_bd("show", stdout="", rc=1)
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=100)
    _watch(harness, "bd update bd-a1b2 --claim")
    assert harness.read_state("sess-1")["current_task"] == "bd-a1b2"


def test_a_claim_inside_a_chained_command_is_seen(harness):
    harness.make_beads_project()
    harness.set_bd_json("show", [ISSUE])
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=100)
    _watch(harness, "cd /tmp/somewhere && bd update bd-a1b2 --claim --json")
    assert harness.read_state("sess-1")["current_task"] == "bd-a1b2"


# ── multi-line commands ─────────────────────────────────────────────────────
#
# A newline separates two commands exactly as `;` does, and `cd <dir>` on one
# line with the real command on the next is the single most common shape a
# multi-step Bash call takes. Every scenario below was observed silently losing
# its attribution: `\n` was listed in the separator set, but `whitespace_split`
# makes shlex classify newline as whitespace, so it was never emitted as a token
# and the whole command collapsed into one segment beginning with `cd`.
#
# The two guards at the end matter as much as the bug: the obvious fix — split
# the string on "\n" before tokenising — reintroduces a *false* boundary in both
# of them, and a hallucinated close writes cost onto the wrong issue.

def test_a_claim_on_its_own_line_is_seen(harness):
    harness.make_beads_project()
    harness.set_bd_json("show", [ISSUE])
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=100)
    _watch(harness, "cd /tmp/somewhere\nbd update bd-a1b2 --claim --json")
    state = harness.read_state("sess-1") or {}
    assert state.get("current_task") == "bd-a1b2"


def test_a_close_on_its_own_line_is_seen(harness):
    """The shape that left a real task marked abacus_partial despite being closed."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "cd /tmp/somewhere\nbd close bd-a1b2 --reason 'done'")
    assert _metadata_from_calls(harness).get("abacus_cost_usd_estimate") == "2.0"


def test_a_close_after_several_leading_lines_is_seen(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "set -e\ncd /tmp/somewhere\ngit status\nbd close bd-a1b2\n")
    assert _metadata_from_calls(harness).get("abacus_cost_usd_estimate") == "2.0"


def test_a_backslash_continuation_is_one_command(harness):
    """A trailing `\\` joins the next line — the id is still the first positional."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "bd close bd-a1b2 \\\n  --reason 'done'")
    meta = _metadata_from_calls(harness)
    assert meta.get("abacus_cost_usd_estimate") == "2.0"


def test_a_continuation_before_the_id_does_not_steal_the_id_slot(harness):
    """shlex renders an escaped newline as a whitespace token rather than eliding
    it. Left in place it becomes the first positional, so the claim would track a
    task id of "\\n" — the subsequent close then matches nothing and the whole
    task's cost is lost."""
    harness.make_beads_project()
    harness.set_bd_json("show", [ISSUE])
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=100)
    _watch(harness, "bd update \\\n  bd-a1b2 --claim --json")
    assert (harness.read_state("sess-1") or {}).get("current_task") == "bd-a1b2"


def test_a_continuation_before_the_id_does_not_derail_a_close(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "bd close \\\n  bd-a1b2")
    assert _metadata_from_calls(harness).get("abacus_cost_usd_estimate") == "2.0"


def test_a_newline_inside_a_quoted_reason_does_not_hide_the_close(harness):
    """A long `--reason` legitimately contains newlines. Splitting the raw string
    on "\\n" would leave an unterminated quote, shlex would refuse the line, and
    a task that really closed would keep no cost at all."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "bd close bd-a1b2 --reason 'did the thing\nand the other thing'")
    assert _metadata_from_calls(harness).get("abacus_cost_usd_estimate") == "2.0"


def test_a_bd_command_inside_a_heredoc_body_is_not_a_boundary(harness):
    """Writing a runbook that mentions `bd close` must not close anything. The
    heredoc body is data being written to a file, not a command being run."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "cat > /tmp/runbook.md <<'EOF'\nbd close bd-a1b2\nEOF")
    assert not [c for c in harness.bd_calls() if "--set-metadata" in c]
    assert harness.read_state("sess-1")["current_task"] == "bd-a1b2"


def test_a_real_command_after_a_heredoc_terminator_is_still_seen(harness):
    """Skipping the heredoc body must resume at the delimiter, not swallow the
    rest of the script."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "cat > /tmp/note.md <<'EOF'\nsome notes\nEOF\nbd close bd-a1b2")
    assert _metadata_from_calls(harness).get("abacus_cost_usd_estimate") == "2.0"


def test_a_claim_with_no_explicit_id_resolves_via_bd(harness):
    """`bd update --claim` targets bd's last-touched issue; ask bd which."""
    harness.make_beads_project()
    harness.set_bd_json("list", [ISSUE])
    harness.set_bd_json("show", [ISSUE])
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=100)
    _watch(harness, "bd update --claim")
    assert harness.read_state("sess-1")["current_task"] == "bd-a1b2"


def test_an_update_without_claim_is_not_a_boundary(harness):
    harness.make_beads_project()
    harness.set_bd_json("show", [ISSUE])
    _watch(harness, "bd update bd-a1b2 --priority 1")
    assert harness.npx_calls() == [], "no baseline needed for a metadata-only edit"


def test_re_claiming_the_same_task_preserves_the_original_baseline(harness):
    """`--claim` is idempotent in bd; re-snapshotting would zero the cost so far."""
    harness.make_beads_project()
    harness.set_bd_json("show", [ISSUE])
    harness.write_state("sess-1", _claimed_state())
    harness.set_ccusage_session("sess-1", cost=99.0, tokens=99000)
    _watch(harness, "bd update bd-a1b2 --claim")
    assert harness.read_state("sess-1")["snapshot"]["cost"] == 1.0


def test_claiming_a_second_task_finalises_the_first(harness):
    """Switching tasks without closing must not silently discard the first
    task's accrued cost."""
    harness.make_beads_project()
    harness.set_bd_json("show", [{"id": "bd-c3d4", "title": "next", "status": "in_progress"}])
    harness.write_state("sess-1", _claimed_state())
    harness.set_ccusage_session("sess-1", cost=4.0, tokens=4000)
    _watch(harness, "bd update bd-c3d4 --claim")
    state = harness.read_state("sess-1")
    assert state["current_task"] == "bd-c3d4"
    assert state["snapshot"]["cost"] == 4.0, "the new task starts from here"
    writes = [c for c in harness.bd_calls() if "--set-metadata" in c]
    assert any("bd-a1b2" in c for c in writes), "the abandoned task should be written out"


# ── close: the attribution write ────────────────────────────────────────────

def test_closing_the_tracked_task_writes_the_cost_delta(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.5, tokens=5000,
                               input_tokens=600, output_tokens=900,
                               cache_read_tokens=1200, cache_creation_tokens=1500)
    _watch(harness, "bd close bd-a1b2")
    meta = _metadata_from_calls(harness)
    assert meta["abacus_cost_usd_estimate"] == "2.5", "3.5 now minus 1.0 baseline"
    assert meta["abacus_tokens_total"] == "4000"
    assert meta["abacus_tokens_in"] == "500"
    assert meta["abacus_tokens_out"] == "700"
    assert meta["abacus_tokens_cache_read"] == "900"
    assert meta["abacus_tokens_cache_write"] == "1100"


def test_the_metadata_key_set_is_the_documented_contract(harness):
    """Downstream readers depend on these names; assert them literally."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.5, tokens=5000)
    _watch(harness, "bd close bd-a1b2")
    meta = _metadata_from_calls(harness)
    for key in ("abacus_cost_usd_estimate", "abacus_cost_basis", "abacus_tokens_total",
                "abacus_tokens_in", "abacus_tokens_out", "abacus_tokens_cache_read",
                "abacus_tokens_cache_write", "abacus_duration_min", "abacus_session_id",
                "abacus_partial", "abacus_schema"):
        assert key in meta, "missing contract key %s" % key
    assert meta["abacus_schema"] == "1"
    assert meta["abacus_session_id"] == "sess-1"
    assert meta["abacus_partial"] == "false"


def test_the_cost_is_labelled_as_a_local_estimate_not_billing(harness):
    """A bare dollar figure next to a project label reads as an invoice. The
    basis key travels with the number so it cannot be quoted as billing."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.5, tokens=5000)
    _watch(harness, "bd close bd-a1b2")
    meta = _metadata_from_calls(harness)
    assert meta["abacus_cost_basis"] == "ccusage-local-list-rate"
    assert meta["abacus_cost_usd_estimate"], "the estimate itself is still recorded"


def test_the_write_targets_the_closed_issue(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.5, tokens=5000)
    _watch(harness, "bd close bd-a1b2")
    write = [c for c in harness.bd_calls() if "--set-metadata" in c]
    assert write, "no metadata write happened"
    assert "update bd-a1b2" in write[0]


def test_duration_is_derived_from_the_claim_timestamp(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state(claimed_at="2026-08-05T21:00:00Z"))
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    _watch(harness, "bd close bd-a1b2", ABACUS_NOW="2026-08-05T21:42:00Z")
    assert _metadata_from_calls(harness)["abacus_duration_min"] == "42"


def test_closing_clears_the_current_task(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    _watch(harness, "bd close bd-a1b2")
    state = harness.read_state("sess-1")
    assert not state.get("current_task"), "a closed task must stop accruing cost"


def test_close_with_no_id_uses_the_tracked_task(harness):
    """`bd close` alone closes bd's last-touched issue, which in practice is the
    one this session claimed."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    _watch(harness, "bd close")
    write = [c for c in harness.bd_calls() if "--set-metadata" in c]
    assert write and "bd-a1b2" in write[0]


def test_closing_an_untracked_task_does_not_steal_the_current_baseline(harness):
    """Cost accrued under bd-a1b2 belongs to bd-a1b2. Closing an unrelated
    issue must not have that spend charged to it."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [{"id": "bd-OTHER", "title": "unrelated", "status": "closed"}])
    harness.set_ccusage_session("sess-1", cost=50.0, tokens=50000)
    _watch(harness, "bd close bd-OTHER")
    meta = _metadata_from_calls(harness)
    assert "abacus_cost_usd_estimate" not in meta or meta.get("abacus_cost_usd_estimate") == "0.0"
    assert harness.read_state("sess-1")["current_task"] == "bd-a1b2", "still tracking"


def test_closing_several_issues_attributes_only_to_the_tracked_one(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "bd close bd-OTHER bd-a1b2")
    writes = [c for c in harness.bd_calls() if "--set-metadata" in c]
    assert len(writes) == 1
    assert "bd-a1b2" in writes[0]


def test_close_without_a_baseline_writes_no_cost_figure(harness):
    """No baseline means no honest delta. Recording the whole session total
    against one task would be a fabrication."""
    harness.make_beads_project()
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=40.0, tokens=40000)
    _watch(harness, "bd close bd-a1b2")
    meta = _metadata_from_calls(harness)
    assert meta.get("abacus_cost_usd_estimate") in (None, "0.0")


def test_a_claim_then_close_in_one_command_is_handled_in_order(harness):
    harness.make_beads_project()
    harness.set_bd_json("show", [ISSUE])
    harness.set_ccusage_session("sess-1", cost=5.0, tokens=5000)
    _watch(harness, "bd update bd-a1b2 --claim && bd close bd-a1b2")
    writes = [c for c in harness.bd_calls() if "--set-metadata" in c]
    assert writes, "the close must still produce an attribution write"
    assert "bd-a1b2" in writes[0]


# ── ccusage degradation ─────────────────────────────────────────────────────

def test_when_ccusage_is_unavailable_the_cost_is_omitted_not_zeroed(harness):
    """A $0.00 on a task that took an hour is a wrong answer presented as fact;
    an absent key is an honest one."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_raw("", rc=1)
    _watch(harness, "bd close bd-a1b2")
    meta = _metadata_from_calls(harness)
    assert "abacus_cost_usd_estimate" not in meta
    assert meta.get("abacus_cost_basis") == "unavailable"
    assert "abacus_duration_min" in meta, "duration does not depend on ccusage"


def test_a_ccusage_timeout_still_writes_what_it_knows(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_hang(3)
    _watch(harness, "bd close bd-a1b2", ABACUS_CCUSAGE_TIMEOUT_S=1)
    meta = _metadata_from_calls(harness)
    assert "abacus_duration_min" in meta
    assert meta.get("abacus_cost_basis") == "unavailable"


# ── partial accumulation ────────────────────────────────────────────────────

def test_a_partial_write_from_a_previous_session_is_accumulated(harness):
    """SessionEnd writes abacus_partial=true for a task still open. When that task
    is finally closed in a later session, the two spends must add up rather than
    the second overwriting the first."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed", metadata={
        "abacus_partial": True,
        "abacus_cost_usd_estimate": 7.0,
        "abacus_tokens_total": 10000,
        "abacus_duration_min": 30,
    })])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "bd close bd-a1b2", ABACUS_NOW="2026-08-05T21:10:00Z")
    meta = _metadata_from_calls(harness)
    assert meta["abacus_cost_usd_estimate"] == "9.0", "7.0 carried + 2.0 this session"
    assert meta["abacus_tokens_total"] == "12000"
    assert meta["abacus_duration_min"] == "40", "30 carried + 10 this session"
    assert meta["abacus_partial"] == "false", "the task is now complete"


def test_a_completed_task_is_not_double_counted_on_a_second_close(harness):
    """Only abacus_partial=true metadata is carried forward. A finalised figure
    must not be added to again."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed", metadata={
        "abacus_partial": False,
        "abacus_cost_usd_estimate": 7.0,
        "abacus_tokens_total": 10000,
    })])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "bd close bd-a1b2")
    meta = _metadata_from_calls(harness)
    assert meta["abacus_cost_usd_estimate"] == "2.0", "this session's delta only"


# ── metadata written under the pre-rename `tct_` prefix ─────────────────────
#
# Tasks attributed before the rename carry `tct_*` keys. Reads must understand
# them, or a task left partial across the upgrade is orphaned: its accumulated
# spend becomes invisible and the closing write reports only the final session.
# Writes stay `abacus_*` only — supporting two write prefixes would be a fork.

def test_a_partial_written_under_the_legacy_prefix_is_accumulated(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed", metadata={
        "tct_partial": True,
        "tct_cost_usd_estimate": 7.0,
        "tct_tokens_total": 10000,
        "tct_duration_min": 30,
    })])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "bd close bd-a1b2", ABACUS_NOW="2026-08-05T21:10:00Z")
    meta = _metadata_from_calls(harness)
    assert meta["abacus_cost_usd_estimate"] == "9.0", "7.0 carried from tct_ + 2.0 now"
    assert meta["abacus_tokens_total"] == "12000"
    assert meta["abacus_duration_min"] == "40", "30 carried from tct_ + 10 now"
    assert meta["abacus_partial"] == "false"


def test_a_finalised_legacy_write_is_not_double_counted(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed", metadata={
        "tct_partial": False,
        "tct_cost_usd_estimate": 7.0,
    })])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "bd close bd-a1b2")
    meta = _metadata_from_calls(harness)
    assert meta["abacus_cost_usd_estimate"] == "2.0", "a closed legacy figure is left alone"


def test_the_current_prefix_wins_when_an_issue_carries_both(harness):
    """A task finalised post-upgrade keeps its stale `tct_partial=true` alongside
    the `abacus_partial=false` that superseded it. The current key decides, or
    every subsequent close would add to a figure already banked."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed", metadata={
        "tct_partial": True,
        "tct_cost_usd_estimate": 7.0,
        "abacus_partial": False,
        "abacus_cost_usd_estimate": 9.0,
    })])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "bd close bd-a1b2")
    meta = _metadata_from_calls(harness)
    assert meta["abacus_cost_usd_estimate"] == "2.0", "abacus_partial=false settles it"


def test_no_legacy_prefixed_key_is_ever_written(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed", metadata={
        "tct_partial": True, "tct_cost_usd_estimate": 7.0})])
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    _watch(harness, "bd close bd-a1b2")
    meta = _metadata_from_calls(harness)
    assert not [k for k in meta if k.startswith("tct_")], (
        "reads understand the old prefix; writes must only ever emit the new one")


# ── OTEL enrichment ────────────────────────────────────────────────────────

def test_otel_activity_is_attached_when_the_event_log_is_available(harness, tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(_otel_line("sess-1", "tool_result", "2026-08-05T21:10:00Z", 4000)
                      + _otel_line("sess-1", "tool_result", "2026-08-05T21:11:00Z", 8000)
                      + _otel_line("sess-1", "api_request", "2026-08-05T21:12:00Z", 48000))
    harness.make_beads_project()
    harness.write_config({"otel_events_path": str(events)})
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    _watch(harness, "bd close bd-a1b2", ABACUS_NOW="2026-08-05T22:00:00Z")
    meta = _metadata_from_calls(harness)
    assert meta["abacus_tool_calls"] == "2"
    assert meta["abacus_active_min"] == "1"


def test_a_missing_otel_log_omits_those_keys_without_failing(harness):
    harness.make_beads_project()
    harness.write_config({"otel_events_path": "/nonexistent/events.jsonl"})
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    res = _watch(harness, "bd close bd-a1b2")
    assert res.rc == 0
    meta = _metadata_from_calls(harness)
    assert "abacus_tool_calls" not in meta
    assert "abacus_cost_usd_estimate" in meta, "cost must not depend on OTEL"


def test_a_log_with_no_events_for_this_session_omits_the_activity_keys(harness, tmp_path):
    """Caught in a real E2E run: the log existed but held nothing for this
    session, and zeros were written. `abacus_tool_calls: 0` on a task that ran 40
    tools reads as a measurement, not as "OTEL had nothing to say" — the same
    dishonest-zero problem the cost path already avoids."""
    events = tmp_path / "events.jsonl"
    events.write_text(_otel_line("SOMEONE-ELSE", "tool_result", "2026-08-05T21:10:00Z", 4000))
    harness.make_beads_project()
    harness.write_config({"otel_events_path": str(events)})
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    _watch(harness, "bd close bd-a1b2", ABACUS_NOW="2026-08-05T22:00:00Z")
    meta = _metadata_from_calls(harness)
    assert "abacus_tool_calls" not in meta
    assert "abacus_active_min" not in meta
    assert "abacus_cost_usd_estimate" in meta, "cost is unaffected"


def test_otel_enrichment_can_be_switched_off(harness, tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(_otel_line("sess-1", "tool_result", "2026-08-05T21:10:00Z", 4000))
    harness.make_beads_project()
    harness.write_config({"otel_events_path": str(events), "otel_enrichment": False})
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    _watch(harness, "bd close bd-a1b2")
    assert "abacus_tool_calls" not in _metadata_from_calls(harness)


# ── robustness ─────────────────────────────────────────────────────────────

def test_the_watcher_never_emits_a_permission_decision(harness):
    """PostToolUse runs after the fact; blocking here would be meaningless and
    the envelope shape differs from PreToolUse."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    res = _watch(harness, "bd close bd-a1b2")
    assert res.permission_decision is None
    assert res.rc == 0


def test_an_unparsable_command_fails_open(harness):
    harness.make_beads_project()
    res = _watch(harness, 'bd close "unterminated')
    assert res.rc == 0
    assert "Traceback" not in res.stderr


def test_a_malformed_payload_fails_open(harness):
    harness.make_beads_project()
    res = harness.run_hook("watch_bd_commands.py", {"tool_name": "Bash"})
    assert res.rc == 0
    assert "Traceback" not in res.stderr


def test_a_non_bash_tool_is_ignored(harness):
    harness.make_beads_project()
    res = harness.run_hook("watch_bd_commands.py",
                           {"session_id": "sess-1", "tool_name": "Edit",
                            "tool_input": {"file_path": "/tmp/x"}})
    assert res.rc == 0
    assert harness.calls() == []


def test_the_watcher_is_inert_when_the_plugin_is_disabled(harness):
    harness.make_beads_project()
    res = _watch(harness, "bd update bd-a1b2 --claim", ABACUS_DISABLE="1")
    assert res.rc == 0
    assert harness.calls() == []


def test_a_failed_metadata_write_does_not_crash_the_hook(harness):
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())
    harness.set_bd_json("show", [dict(ISSUE, status="closed")])
    harness.set_bd("update", stdout="Error: no such issue", rc=1)
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    res = _watch(harness, "bd close bd-a1b2")
    assert res.rc == 0


# ── helpers ────────────────────────────────────────────────────────────────

def _otel_line(session_id, event_name, timestamp, duration_ms):
    """One OTLP-JSON log line as the file exporter writes it."""
    return json.dumps({"resourceLogs": [{"scopeLogs": [{"logRecords": [{"attributes": [
        {"key": "session.id", "value": {"stringValue": session_id}},
        {"key": "event.name", "value": {"stringValue": event_name}},
        {"key": "event.timestamp", "value": {"stringValue": timestamp}},
        {"key": "duration_ms", "value": {"intValue": duration_ms}},
        {"key": "model", "value": {"stringValue": "claude-fable-5"}},
    ]}]}]}]}) + "\n"
