"""The whole plugin, driven as Claude Code would drive it.

The unit tests each exercise one script against planted state. These drive the
real scripts in sequence and let each one's *output* be the next one's input, so
they cover the thing unit tests structurally cannot: the handoffs. Every bug found
at this level so far has lived in a seam rather than in a script — the state one
hook writes not being quite what the next one reads.

Read these as transcripts. Each test is a session that really happens.
"""

import json

import pytest

from conftest import post_bash_payload, pre_tool_payload, session_payload

TASK_A = {"id": "bd-a1b2", "title": "add the widget", "status": "in_progress",
          "updated_at": "2026-08-06T10:00:00Z"}
TASK_B = {"id": "bd-c3d4", "title": "fix the sprocket", "status": "in_progress",
          "updated_at": "2026-08-06T11:00:00Z"}


def _metadata_writes(harness):
    """Every `--set-metadata` call, parsed back into dicts, in order."""
    writes = []
    for call in harness.bd_calls():
        if "--set-metadata" not in call:
            continue
        parts = call.split()
        pairs = {}
        for i, part in enumerate(parts):
            if part == "--set-metadata" and i + 1 < len(parts):
                key, _, value = parts[i + 1].partition("=")
                pairs[key] = value
        pairs["_issue"] = parts[2] if len(parts) > 2 else ""
        writes.append(pairs)
    return writes


def _last_write(harness):
    writes = _metadata_writes(harness)
    assert writes, "expected an attribution write, but none was made"
    return writes[-1]


# ── the happy path, start to finish ─────────────────────────────────────────

def test_a_whole_tracked_task_from_deny_to_attribution(harness):
    """The transcript this plugin exists to produce:

    session opens → an edit is refused for want of a task → a task is claimed →
    the same edit is allowed → the task closes carrying its own cost.
    """
    harness.make_beads_project()

    # 1. Session opens with nothing claimed.
    harness.set_bd_json("list", [])
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=1000)
    start = harness.run_hook("session_start.py", session_payload())
    assert start.rc == 0

    # 2. An edit is attempted. Nothing is claimed, so it is refused.
    denied = harness.run_hook("gate_edits.py", pre_tool_payload())
    assert denied.permission_decision == "deny"
    assert "bd update <id> --claim" in denied.reason, \
        "a refusal without the remedy is just an obstacle"

    # 3. Claude does what the refusal told it to.
    harness.set_bd_json("show", [TASK_A])
    harness.set_bd_json("list", [TASK_A])
    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("bd update bd-a1b2 --claim --json"))
    state = harness.read_state("sess-1")
    assert state["current_task"] == "bd-a1b2"
    assert state["snapshot"]["cost"] == 1.0, "the claim must fix a baseline"

    # 4. The same edit, retried, now goes through.
    allowed = harness.run_hook("gate_edits.py", pre_tool_payload())
    assert allowed.permission_decision is None

    # 5. Work happens: the session's cumulative total rises.
    harness.set_ccusage_session("sess-1", cost=4.5, tokens=9000,
                                input_tokens=1000, output_tokens=2000)

    # 6. The task closes, and carries what it cost.
    harness.set_bd_json("show", [dict(TASK_A, status="closed")])
    harness.set_bd_json("list", [])
    harness.run_hook("watch_bd_commands.py", post_bash_payload("bd close bd-a1b2"))

    write = _last_write(harness)
    assert write["_issue"] == "bd-a1b2"
    assert write["abacus_cost_usd_estimate"] == "3.5", "4.5 now minus 1.0 at claim"
    assert write["abacus_tokens_total"] == "8000"
    assert write["abacus_partial"] == "false"
    assert write["abacus_cost_basis"] == "ccusage-local-list-rate"

    # 7. Nothing is being tracked any more, so the next edit is refused again.
    assert not harness.read_state("sess-1").get("current_task")


def test_a_short_task_is_not_recorded_as_free(harness):
    """A task claimed and closed inside the ccusage cache TTL.

    This is the ordinary shape of a small fix — claim, one edit, close, well under
    a minute. If the closing read is served from the cache the claim populated,
    the delta is exactly zero and the task is recorded as having cost nothing,
    with a `abacus_cost_basis` that presents it as a real measurement. That is the
    precise failure the design forbids everywhere else, so the closing read has to
    be fresh regardless of TTL.
    """
    harness.make_beads_project()
    harness.set_bd_json("show", [TASK_A])
    harness.set_bd_json("list", [TASK_A])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    harness.write_config({"cache_ttl_s": 600})

    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("bd update bd-a1b2 --claim --json"))
    harness.set_ccusage_session("sess-1", cost=2.9, tokens=3500)
    harness.run_hook("watch_bd_commands.py", post_bash_payload("bd close bd-a1b2"))

    write = _last_write(harness)
    assert write["abacus_cost_usd_estimate"] == "0.9"
    assert write["abacus_tokens_total"] == "1500"


# ── work that spans more than one task or session ────────────────────────────

def test_switching_tasks_without_closing_charges_each_its_own_share(harness):
    """Two tasks, one session, no close in between. Neither may absorb the
    other's spend."""
    harness.make_beads_project()
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=1000)
    harness.set_bd_json("show", [TASK_A])
    harness.set_bd_json("list", [TASK_A])
    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("bd update bd-a1b2 --claim --json"))

    # Work on A, then claim B without closing A.
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    harness.set_bd_json("show", [TASK_B])
    harness.set_bd_json("list", [TASK_B])
    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("bd update bd-c3d4 --claim --json"))

    writes = _metadata_writes(harness)
    assert writes[-1]["_issue"] == "bd-a1b2"
    assert writes[-1]["abacus_cost_usd_estimate"] == "2.0", "A's own share only"
    assert writes[-1]["abacus_partial"] == "true", "A is interrupted, not finished"
    assert harness.read_state("sess-1")["current_task"] == "bd-c3d4"

    # Work on B and close it.
    harness.set_ccusage_session("sess-1", cost=7.5, tokens=7500)
    harness.set_bd_json("show", [dict(TASK_B, status="closed")])
    harness.run_hook("watch_bd_commands.py", post_bash_payload("bd close bd-c3d4"))

    write = _last_write(harness)
    assert write["_issue"] == "bd-c3d4"
    assert write["abacus_cost_usd_estimate"] == "4.5", "7.5 minus B's 3.0 baseline"


def test_a_task_spanning_two_sessions_reports_its_whole_cost(harness):
    """Session one ends mid-task; session two finishes it. The close must report
    both sittings, or the figure understates the work by however much happened
    before the interruption."""
    harness.make_beads_project()

    # Session one: claim, work, end without closing.
    harness.set_ccusage_session("sess-1", cost=0.0, tokens=0)
    harness.set_bd_json("show", [TASK_A])
    harness.set_bd_json("list", [TASK_A])
    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("bd update bd-a1b2 --claim --json"))
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    harness.run_hook("session_end.py", session_payload(event="SessionEnd", reason="clear"))

    first = _last_write(harness)
    assert first["abacus_partial"] == "true"
    assert first["abacus_cost_usd_estimate"] == "2.0"

    # Session two: bd now reports the partial figures session one wrote, which is
    # how the accumulation finds them.
    harness.set_bd_json("show", [dict(TASK_A, metadata={
        "abacus_partial": True,
        "abacus_cost_usd_estimate": 2.0,
        "abacus_tokens_total": 2000,
        "abacus_duration_min": 15,
    })])
    harness.set_ccusage_session("sess-2", cost=0.0, tokens=0)
    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("bd update bd-a1b2 --claim --json",
                                       session_id="sess-2"))
    harness.set_ccusage_session("sess-2", cost=1.5, tokens=1500)
    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("bd close bd-a1b2", session_id="sess-2"))

    write = _last_write(harness)
    assert write["abacus_cost_usd_estimate"] == "3.5", "2.0 carried plus 1.5 this session"
    assert write["abacus_tokens_total"] == "3500"
    assert write["abacus_partial"] == "false", "now genuinely finished"


def test_a_finalised_task_closed_twice_is_not_charged_twice(harness):
    """`bd close` on an already-closed issue is harmless at the bd level and must
    be harmless here too. Only `abacus_partial=true` figures accumulate."""
    harness.make_beads_project()
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=1000)
    harness.set_bd_json("show", [TASK_A])
    harness.set_bd_json("list", [TASK_A])
    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("bd update bd-a1b2 --claim --json"))

    harness.set_ccusage_session("sess-1", cost=3.0, tokens=3000)
    harness.set_bd_json("show", [dict(TASK_A, status="closed", metadata={
        "abacus_partial": False, "abacus_cost_usd_estimate": 2.0, "abacus_tokens_total": 2000,
    })])
    harness.run_hook("watch_bd_commands.py", post_bash_payload("bd close bd-a1b2"))
    first = _last_write(harness)

    # A second close, with the first write's figures now on the issue.
    harness.run_hook("watch_bd_commands.py", post_bash_payload("bd close bd-a1b2"))
    assert _last_write(harness)["abacus_cost_usd_estimate"] == first["abacus_cost_usd_estimate"]


# ── repair paths ────────────────────────────────────────────────────────────

def test_a_claim_the_watcher_never_saw_still_gets_a_baseline(harness):
    """Claimed in another terminal, so no Bash call passed through the watcher.
    The gate notices at the next edit and starts attributing; without that the
    task would close with no baseline and be recorded at zero."""
    harness.make_beads_project()
    harness.set_bd_json("list", [TASK_A])
    harness.set_bd_json("show", [TASK_A])
    harness.set_ccusage_session("sess-1", cost=5.0, tokens=5000)

    allowed = harness.run_hook("gate_edits.py", pre_tool_payload())
    assert allowed.permission_decision is None
    state = harness.read_state("sess-1")
    assert state["current_task"] == "bd-a1b2"
    assert state["snapshot"]["cost"] == 5.0
    assert state["snapshot_source"] == "gate-lazy"

    harness.set_ccusage_session("sess-1", cost=6.25, tokens=6000)
    harness.set_bd_json("show", [dict(TASK_A, status="closed")])
    harness.run_hook("watch_bd_commands.py", post_bash_payload("bd close bd-a1b2"))
    assert _last_write(harness)["abacus_cost_usd_estimate"] == "1.25"


def test_a_close_the_watcher_never_saw_is_repaired_at_stop(harness):
    """Closed outside our view. Without the Stop pass the task keeps accruing
    every later turn's cost against an issue that finished."""
    harness.make_beads_project()
    harness.set_bd_json("list", [TASK_A])
    harness.set_bd_json("show", [TASK_A])
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=1000)
    harness.run_hook("gate_edits.py", pre_tool_payload())

    # The close happens in another terminal: bd stops reporting it in_progress.
    harness.set_bd_json("list", [])
    harness.set_bd_json("show", [dict(TASK_A, status="closed")])
    harness.set_ccusage_session("sess-1", cost=2.5, tokens=2500)
    harness.run_hook("stop_reconcile.py", session_payload(event="Stop"))

    write = _last_write(harness)
    assert write["abacus_cost_usd_estimate"] == "1.5"
    assert write["abacus_partial"] == "false", "bd says closed, so this is final"
    assert not harness.read_state("sess-1").get("current_task")


def test_an_unclaimed_task_is_reconciled_as_unfinished(harness):
    """Moved back to open rather than closed. The spend so far is real, but
    calling it final would claim the work was completed."""
    harness.make_beads_project()
    harness.set_bd_json("list", [TASK_A])
    harness.set_bd_json("show", [TASK_A])
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=1000)
    harness.run_hook("gate_edits.py", pre_tool_payload())

    harness.set_bd_json("list", [])
    harness.set_bd_json("show", [dict(TASK_A, status="open")])
    harness.set_ccusage_session("sess-1", cost=2.0, tokens=2000)
    harness.run_hook("stop_reconcile.py", session_payload(event="Stop"))

    assert _last_write(harness)["abacus_partial"] == "true"


# ── degradation ─────────────────────────────────────────────────────────────

def test_a_session_with_ccusage_broken_still_tracks_time_and_never_lies(harness):
    """No cost figure at all, rather than a zero that reads as a measurement.
    Duration does not depend on ccusage, so it is still recorded."""
    harness.make_beads_project()
    harness.set_bd_json("show", [TASK_A])
    harness.set_bd_json("list", [TASK_A])
    harness.set_ccusage_raw("not json at all", rc=1)

    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("bd update bd-a1b2 --claim --json"))
    harness.run_hook("watch_bd_commands.py", post_bash_payload("bd close bd-a1b2"))

    write = _last_write(harness)
    assert write["abacus_cost_basis"] == "unavailable"
    assert "abacus_cost_usd_estimate" not in write
    assert "abacus_tokens_total" not in write
    assert "abacus_duration_min" in write


def test_the_whole_sequence_is_inert_when_disabled(harness):
    """The kill switch has to hold across every hook, not just the gate — a
    half-disabled plugin that still writes metadata is worse than either state."""
    harness.make_beads_project()
    harness.set_bd_json("list", [TASK_A])
    harness.set_bd_json("show", [TASK_A])
    off = {"ABACUS_DISABLE": "1"}

    for script, payload in (
        ("session_start.py", session_payload()),
        ("gate_edits.py", pre_tool_payload()),
        ("watch_bd_commands.py", post_bash_payload("bd update bd-a1b2 --claim")),
        ("prompt_statusline.py", session_payload(event="UserPromptSubmit")),
        ("stop_reconcile.py", session_payload(event="Stop")),
        ("session_end.py", session_payload(event="SessionEnd")),
    ):
        res = harness.run_hook(script, payload, **off)
        assert res.rc == 0, script
        assert res.permission_decision is None, script
    assert harness.calls() == [], "nothing should have been spawned at all"


def test_no_hook_blocks_when_bd_is_missing_entirely(harness):
    """A user who uninstalls bd but leaves the plugin enabled must still be able
    to work."""
    harness.make_beads_project()
    harness.remove_bd()

    for script, payload in (
        ("session_start.py", session_payload()),
        ("gate_edits.py", pre_tool_payload()),
        ("watch_bd_commands.py", post_bash_payload("bd close bd-a1b2")),
        ("prompt_statusline.py", session_payload(event="UserPromptSubmit")),
        ("stop_reconcile.py", session_payload(event="Stop")),
        ("session_end.py", session_payload(event="SessionEnd")),
    ):
        res = harness.run_hook(script, payload)
        assert res.rc == 0, script
        assert res.permission_decision is None, script
        assert "Traceback" not in res.stderr, script


# ── commits, across the boundaries that produced them ───────────────────────
#
# The unit tests drive `commit_capture.capture` against planted state. These drive
# the seam it actually lives in: the watermark one boundary writes has to be the
# one the next boundary reads, and the state file it lives in is shared with the
# claim snapshot. Both of those are handoffs, which is the class of bug this level
# exists to catch.
#
# git is the stub here, not a real repository — the hook subprocesses get the stub
# because it prefixes their PATH. Format fidelity is `tests/unit/test_gitlog.py`'s
# job, against real git; what these assert is which boundary wrote what.

SEP = "\x1f"


def _plant_head(harness, sha):
    harness.set_git("rev-parse.HEAD", stdout="%s\n" % sha)


def _plant_new_commit(harness, sha, when, subject="do the work", declares=""):
    harness.set_git("log", stdout=SEP.join([sha, str(int(when)), subject, declares]))


def _commit_keys(harness):
    """Every `abacus_commit_*` key written, as (issue, key, value)."""
    out = []
    for call in harness.bd_calls():
        parts = call.split()
        for i, part in enumerate(parts):
            if part != "--set-metadata" or i + 1 >= len(parts):
                continue
            key, _, value = parts[i + 1].partition("=")
            if key.startswith("abacus_commit_"):
                out.append((parts[2], key, value))
    return out


def test_a_commit_made_between_a_claim_and_a_close_is_recorded_against_the_task(harness):
    """The transcript the feature exists for, end to end.

    Note what has to line up for this to pass: the claim boundary writes
    `claimed_at` and the git boundary reads it back out of the same state file to
    apply rail 2, then the close boundary writes cost onto the issue the edge is
    already on. Three writers, one file, one issue.
    """
    import time

    harness.make_beads_project()
    harness.make_git_project()
    harness.set_bd_json("show", [TASK_A])
    harness.set_bd_json("list", [TASK_A])
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=1000)
    harness.set_git("rev-parse.--show-toplevel", stdout="%s\n" % harness.project)
    _plant_head(harness, "a" * 40)

    # 1. The session opens and the repository is seen for the first time: rail 1
    #    seeds the watermark and attributes nothing.
    harness.run_hook("session_start.py", session_payload())
    assert _commit_keys(harness) == [], "first sight must attribute nothing"
    marks = (harness.read_state("sess-1") or {}).get("head_watermarks") or {}
    assert marks.get(str(harness.project)) == "a" * 40

    # 2. A task is claimed.
    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("bd update bd-a1b2 --claim --json"))
    assert (harness.read_state("sess-1") or {}).get("claimed_at")

    # 3. A commit lands during the claim.
    harness.set_ccusage_session("sess-1", cost=1.8, tokens=4000)
    _plant_head(harness, "b" * 40)
    _plant_new_commit(harness, "b" * 40, time.time() + 60)
    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("git commit -q -m 'add the widget'"))

    edges = _commit_keys(harness)
    assert len(edges) == 1, "expected exactly one edge, got %s" % (edges,)
    issue, key, value = edges[0]
    assert issue == "bd-a1b2"
    assert key == "abacus_commit_%s" % ("b" * 12)
    assert value.startswith("observed:sess-1:")

    # 4. The watermark advanced, so the same commit is not written twice.
    harness.run_hook("watch_bd_commands.py", post_bash_payload("git commit -q -m 'again'"))
    assert len(_commit_keys(harness)) == 1, "a commit must be recorded once, not once per boundary"

    # 5. Closing the task writes cost onto the same issue the edge is on.
    harness.run_hook("watch_bd_commands.py", post_bash_payload("bd close bd-a1b2"))
    write = _last_write(harness)
    assert write["_issue"] == "bd-a1b2"
    assert write["abacus_cost_basis"] == "ccusage-local-list-rate"


def test_a_commit_no_verb_matched_is_still_caught_while_the_session_is_open(harness):
    """The sweep is what makes the verb list a trigger rather than the mechanism.

    A commit made by a shell script or a Makefile target matches no verb, so the
    watcher never fires. Stop fires anyway, and the session id — the one thing a
    git hook could never know — is still known at that point.
    """
    import time

    harness.make_beads_project()
    harness.make_git_project()
    harness.set_bd_json("show", [TASK_A])
    harness.set_bd_json("list", [TASK_A])
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=1000)
    harness.set_git("rev-parse.--show-toplevel", stdout="%s\n" % harness.project)
    _plant_head(harness, "a" * 40)

    # SessionStart is what makes the sweep able to say anything at all: without a
    # watermark to diff against, rail 1 seeds and writes nothing however many
    # commits landed. The sweep repairs a missed *boundary*, never a missed session.
    harness.run_hook("session_start.py", session_payload())
    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("bd update bd-a1b2 --claim --json"))
    _plant_head(harness, "c" * 40)
    _plant_new_commit(harness, "c" * 40, time.time() + 60)

    # The command that made it says nothing about git.
    res = harness.run_hook("watch_bd_commands.py", post_bash_payload("./scripts/release.sh"))
    assert res.rc == 0
    assert _commit_keys(harness) == [], "no verb matched, so the watcher must not fire"

    harness.run_hook("stop_reconcile.py", session_payload(event="Stop"))
    edges = _commit_keys(harness)
    assert [(i, k) for i, k, _ in edges] == [("bd-a1b2", "abacus_commit_%s" % ("c" * 12))]
    assert edges[0][2].startswith("observed:sess-1:")


def test_a_pull_that_moves_head_backwards_in_time_writes_nothing(harness):
    """Rail 2 at the level it matters: fifty upstream commits are not this task's."""
    harness.make_beads_project()
    harness.make_git_project()
    harness.set_bd_json("show", [TASK_A])
    harness.set_bd_json("list", [TASK_A])
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=1000)
    harness.set_git("rev-parse.--show-toplevel", stdout="%s\n" % harness.project)
    _plant_head(harness, "a" * 40)

    harness.run_hook("watch_bd_commands.py",
                     post_bash_payload("bd update bd-a1b2 --claim --json"))
    _plant_head(harness, "d" * 40)
    harness.set_git("log", stdout="\n".join(
        SEP.join(["%040x" % (i + 1), "1700000000", "upstream %d" % i, ""])
        for i in range(20)))

    harness.run_hook("watch_bd_commands.py", post_bash_payload("git pull --rebase"))
    assert _commit_keys(harness) == []
    marks = (harness.read_state("sess-1") or {}).get("head_watermarks") or {}
    assert marks.get(str(harness.project)) == "d" * 40, "the watermark must still advance"
