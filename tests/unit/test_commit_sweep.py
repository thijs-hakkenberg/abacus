"""RED: the lifecycle half of commit capture — seed, sweep, and the audit.

The watcher in ``test_commit_capture.py`` only sees HEAD moves it recognised a
verb for. That verb list is a cheap trigger, and it is knowably incomplete: a
commit made by a shell script, a Makefile target, a ``gh pr merge`` or a verb this
plugin has never heard of moves HEAD with nothing in the Bash command to match.

So the watermark is swept at two more points, and seeded at a third:

- **SessionStart / PreCompact** seed it. On a fresh session there is no watermark,
  so this is rail 1 and writes nothing. On a resume or a compaction there *is*
  one, so the same call sweeps — which is the right answer, because dropping the
  watermark mid-session would silently discard a boundary's worth of commits.
- **Stop** sweeps at the end of every turn, while the session is still open and
  its id is therefore still known. This is the only surface that can attribute an
  unrecognised commit to the session that made it.
- **SessionEnd** sweeps once more, before the partial write clears the claim.

The load-bearing assertion in this module is that the Stop sweep does **not** sit
behind ``current_task``. A ``declared`` trailer names its own tasks and needs no
claim; gating the sweep on one would make the strongest tier of evidence the only
one that could be missed.

And the audit has to learn about all of it, in the narrowing direction only: a
commit carrying a written edge is no longer *untracked*. No new gap kind, nothing
newly ``fixable`` — adr/013 stands.
"""

import pytest

from conftest import session_payload

BASE = "ba5eba5e" * 5
C1 = "c0ffee01" * 5
C2 = "dec0de02" * 5
LATER = "f00dbabe" * 5

CLAIMED_AT = "2026-08-05T21:00:00Z"      # epoch 1785963600
AFTER_1 = 1785964200                      # 21:10:00Z
AFTER_2 = 1785964800                      # 21:20:00Z

SEP = "\x1f"


def git_log(*rows):
    return "".join(
        SEP.join([sha, str(epoch), subject, trailers]) + "\n"
        for sha, epoch, subject, trailers in rows
    )


def commit_row(sha, epoch, subject="do the work", declares=""):
    return (sha, epoch, subject, declares)


ISSUE = {
    "id": "bd-a1b2",
    "title": "implement the thing",
    "status": "in_progress",
    "started_at": CLAIMED_AT,
    "updated_at": CLAIMED_AT,
}


def _claimed_state(**extra):
    state = {
        "session_id": "sess-1",
        "current_task": "bd-a1b2",
        "current_title": "implement the thing",
        "claimed_at": CLAIMED_AT,
        "snapshot": {"cost": 1.0, "tokens": 1000, "ok": True},
    }
    state.update(extra)
    return state


@pytest.fixture
def repo(harness):
    """A project that is both a git repository and a beads workspace."""
    harness.make_beads_project()
    harness.make_git_project()
    harness.set_git("rev-parse.--show-toplevel", stdout=str(harness.project) + "\n")
    harness.set_git("rev-parse.HEAD", stdout=LATER + "\n")
    return harness


def _edges(harness):
    """``{issue_id: {key: value}}`` parsed out of the recorded bd argv."""
    out = {}
    for call in harness.bd_calls():
        parts = call.split()
        if "--set-metadata" not in parts or parts[1] != "update":
            continue
        issue = parts[2]
        for i, part in enumerate(parts):
            if part == "--set-metadata" and i + 1 < len(parts):
                key, _, value = parts[i + 1].partition("=")
                if key.startswith("abacus_commit_"):
                    out.setdefault(issue, {})[key] = value
    return out


def _watermark(harness, session="sess-1"):
    state = harness.read_state(session) or {}
    return (state.get("head_watermarks") or {}).get(str(harness.project))


# ── SessionStart seeds ──────────────────────────────────────────────────────


def test_session_start_seeds_the_watermark(repo):
    """The seed has to happen somewhere that is not a git command.

    Without it, the first ``git commit`` of a session is the seed, so that
    boundary's commits are lost. Seeding at SessionStart means only commits made
    *before* Claude opened are outside the window, which is exactly right — they
    belong to whatever produced them, not to this session.
    """
    repo.set_bd_json("list", [])
    repo.run_hook("session_start.py", session_payload())

    assert _watermark(repo) == LATER
    assert _edges(repo) == {}, "a seed is not an attribution"


def test_session_start_seeds_nothing_before_the_settings_are_acknowledged(repo):
    """Consent gates the seed too, though the seed itself writes no metadata.

    Not because a watermark is dangerous — it is a line in our own state dir — but
    because a watermark planted while abacus was inert becomes the baseline for a
    sweep the moment consent is given, and that sweep would then attribute
    commits made during the ungoverned period (adr/014).
    """
    repo.revoke_acknowledgement()
    repo.run_hook("session_start.py", session_payload())

    assert _watermark(repo) is None
    assert repo.git_calls() == []


def test_session_start_outside_a_beads_workspace_seeds_nothing(harness):
    """A git repository with no workspace is not ours to watch."""
    harness.make_git_project()
    harness.set_git("rev-parse.--show-toplevel", stdout=str(harness.project) + "\n")
    harness.set_git("rev-parse.HEAD", stdout=LATER + "\n")

    harness.run_hook("session_start.py", session_payload())

    assert _watermark(harness) is None


def test_precompact_sweeps_rather_than_dropping_the_watermark(repo):
    """A compaction is the middle of a session, not the start of one.

    Re-seeding here would discard every commit made since the last boundary — and
    a long session that compacts is exactly the session most likely to have some.
    So PreCompact takes the same path as everything else: a watermark that is
    already there gets swept, not overwritten.
    """
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))
    repo.set_bd_json("list", [ISSUE])

    repo.run_hook("session_start.py",
                  session_payload(event="PreCompact"),
                  extra_args=("--precompact",))

    assert _edges(repo) == {
        "bd-a1b2": {"abacus_commit_c0ffee01c0ff": "observed:sess-1:%d" % AFTER_1},
    }
    assert _watermark(repo) == LATER


# ── the Stop sweep ──────────────────────────────────────────────────────────


def test_stop_sweeps_a_commit_no_verb_list_matched(repo):
    """The reason this sweep exists.

    ``./release.sh`` moves HEAD and contains no git verb the watcher tokenised.
    Stop is the last surface that still knows the session id, so it is the last
    chance to record the edge at all rather than leaving it to the audit to report
    as an untracked gap forever.
    """
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1),
                                       commit_row(C2, AFTER_2)))
    repo.set_bd_json("list", [ISSUE])

    repo.run_hook("stop_reconcile.py", session_payload(event="Stop"))

    assert _edges(repo) == {
        "bd-a1b2": {
            "abacus_commit_c0ffee01c0ff": "observed:sess-1:%d" % AFTER_1,
            "abacus_commit_dec0de02dec0": "observed:sess-1:%d" % AFTER_2,
        },
    }


def test_stop_sweeps_even_with_no_task_claimed(repo):
    """The load-bearing assertion of this module.

    ``stop_reconcile`` returns early when nothing is being tracked, because
    finalising costs a ``bd`` call per turn for nothing. The sweep must sit
    *above* that return: a ``Beads-Task:`` trailer names its own tasks and needs
    no claim, so gating on ``current_task`` would make the strongest tier of
    evidence the only one a sweep could miss.
    """
    repo.write_state("sess-1", {
        "session_id": "sess-1",
        "head_watermarks": {str(repo.project): BASE},
    })
    repo.set_git("log", stdout=git_log(
        commit_row(C1, AFTER_1, subject="fix: two at once",
                   declares="bd-zz9,bd-yy8")))

    repo.run_hook("stop_reconcile.py", session_payload(event="Stop"))

    key = "abacus_commit_c0ffee01c0ff"
    assert _edges(repo) == {
        "bd-yy8": {key: "declared:sess-1:%d" % AFTER_1},
        "bd-zz9": {key: "declared:sess-1:%d" % AFTER_1},
    }


def test_stop_sweeps_before_it_finalises_a_closed_task(repo):
    """Order matters in one direction only.

    ``bd close`` in another terminal makes Stop finalise and clear the claim. A
    sweep after that clearing would find no ``current_task`` and drop the
    ``observed`` edge for a commit the task genuinely produced.
    """
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_bd_json("list", [])
    repo.set_bd_json("show", [dict(ISSUE, status="closed")])
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))
    repo.set_ccusage_session("sess-1", cost=3.0, tokens=3000)

    repo.run_hook("stop_reconcile.py", session_payload(event="Stop"))

    edges = _edges(repo)
    assert edges == {
        "bd-a1b2": {"abacus_commit_c0ffee01c0ff": "observed:sess-1:%d" % AFTER_1},
    }, "the claim was still readable when the sweep ran"


def test_stop_stays_cheap_in_a_repo_with_nothing_to_sweep(repo):
    """Stop fires on the end of every turn, so the quiet path must stay quiet.

    Two ``rev-parse`` calls to establish that HEAD has not moved, and no ``bd``
    at all. The existing guarantee — that a project with nothing tracked spawns
    nothing — is preserved in the shape that still holds once a git repository is
    present: nothing is *written*.
    """
    repo.write_state("sess-1", {
        "session_id": "sess-1",
        "head_watermarks": {str(repo.project): LATER},
    })

    repo.run_hook("stop_reconcile.py", session_payload(event="Stop"))

    assert repo.bd_calls() == []
    assert not [c for c in repo.git_calls() if " log " in c or c.endswith(" log")]


def test_stop_respects_the_loop_guard_before_sweeping(repo):
    """``stop_hook_active`` means we are already inside a Stop. Recursing here
    would sweep once per nested invocation."""
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    repo.run_hook("stop_reconcile.py",
                  session_payload(event="Stop", stop_hook_active=True))

    assert repo.calls() == []


def test_stop_sweeps_nothing_before_the_settings_are_acknowledged(repo):
    repo.revoke_acknowledgement()
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    repo.run_hook("stop_reconcile.py", session_payload(event="Stop"))

    assert _edges(repo) == {}
    assert repo.git_calls() == []


# ── the SessionEnd sweep ────────────────────────────────────────────────────


def test_session_end_sweeps_before_recording_the_partial(repo):
    """The last commit of a session is the one most likely to be missed.

    A user who commits and immediately closes the window gets no Stop after that
    commit. SessionEnd is the final surface with the session id in hand, and it
    has to sweep before ``clear_current`` removes the claim the edge needs.
    """
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_bd_json("show", [ISSUE])
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))
    repo.set_ccusage_session("sess-1", cost=6.0, tokens=6000)

    repo.run_hook("session_end.py",
                  session_payload(event="SessionEnd", reason="clear"))

    assert _edges(repo) == {
        "bd-a1b2": {"abacus_commit_c0ffee01c0ff": "observed:sess-1:%d" % AFTER_1},
    }


def test_session_end_sweeps_with_no_task_claimed(repo):
    """Same reasoning as the Stop case: a declared trailer needs no claim."""
    repo.write_state("sess-1", {
        "session_id": "sess-1",
        "head_watermarks": {str(repo.project): BASE},
    })
    repo.set_git("log", stdout=git_log(
        commit_row(C1, AFTER_1, declares="bd-zz9")))

    repo.run_hook("session_end.py",
                  session_payload(event="SessionEnd", reason="clear"))

    assert _edges(repo) == {
        "bd-zz9": {"abacus_commit_c0ffee01c0ff": "declared:sess-1:%d" % AFTER_1},
    }


def test_session_end_sweeps_nothing_before_the_settings_are_acknowledged(repo):
    repo.revoke_acknowledgement()
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    repo.run_hook("session_end.py",
                  session_payload(event="SessionEnd", reason="clear"))

    assert _edges(repo) == {}
    assert repo.git_calls() == []


def test_a_disabled_plugin_sweeps_nothing(repo):
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    for script, payload in (
        ("session_start.py", session_payload()),
        ("stop_reconcile.py", session_payload(event="Stop")),
        ("session_end.py", session_payload(event="SessionEnd", reason="clear")),
    ):
        repo.run_hook(script, payload, ABACUS_DISABLE="1")

    assert repo.calls() == []


def test_commits_disabled_sweeps_nothing_but_still_reconciles(repo):
    """``commits.enabled: false`` switches off capture, not attribution."""
    repo.write_config({"commits": {"enabled": False}})
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_bd_json("list", [])
    repo.set_bd_json("show", [dict(ISSUE, status="closed")])
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))
    repo.set_ccusage_session("sess-1", cost=3.0, tokens=3000)

    repo.run_hook("stop_reconcile.py", session_payload(event="Stop"))

    assert _edges(repo) == {}
    assert [c for c in repo.bd_calls() if "--set-metadata" in c], \
        "cost attribution is a separate feature and keeps working"


# ── the audit narrows ───────────────────────────────────────────────────────


@pytest.fixture
def audit(lib_path):
    import audit as module

    return module


def _outside(sha, at="2026-08-30T03:00:00Z"):
    """A commit at 3am, outside the one claim window the fixtures below use."""
    return {"sha": sha, "at": at, "subject": "fix: something at 3am"}


CLOSED = {
    "id": "bd-a1b2",
    "title": "tracked work",
    "status": "closed",
    "started_at": "2026-08-31T09:30:00Z",
    "closed_at": "2026-08-31T10:00:00Z",
    "updated_at": "2026-08-31T10:00:00Z",
    "metadata": {"abacus_schema": 1, "abacus_partial": False,
                 "abacus_cost_basis": "unavailable", "abacus_session_id": "sess-1"},
}
NOW = "2026-08-31T12:00:00Z"


def _with_edge(sha12, basis="observed"):
    meta = dict(CLOSED["metadata"])
    meta["abacus_commit_%s" % sha12] = "%s:sess-1:1785964200" % basis
    return dict(CLOSED, metadata=meta)


def test_a_commit_with_a_written_edge_is_no_longer_untracked(audit):
    """The point of the whole feature, from the audit's side.

    Yesterday's report counted 133 commits outside every claim window. A commit
    that now carries an edge was tracked — by a mechanism the window arithmetic
    cannot see, because the edge records what was *observed* rather than what is
    *inferred* from timestamps.
    """
    issues = [_with_edge(C1[:12])]
    gaps = audit.audit(issues, now=NOW, commits=[_outside(C1)])

    assert [g["kind"] for g in gaps
            if g["kind"] == audit.KIND_UNTRACKED_COMMITS] == []


def test_an_edge_for_a_different_commit_suppresses_nothing(audit):
    """The narrowing is per-sha. An issue carrying one edge does not vouch for
    every commit in the repository."""
    issues = [_with_edge(C1[:12])]
    gaps = audit.audit(issues, now=NOW, commits=[_outside(C1), _outside(C2)])

    untracked = [g for g in gaps if g["kind"] == audit.KIND_UNTRACKED_COMMITS]
    assert len(untracked) == 1
    assert untracked[0]["shas"] == [C2]


def test_an_edge_written_before_the_rename_still_counts(audit):
    """Reads understand ``tct_``; writes never emit it. An install that captured
    commits under the old name must not have them reported as untracked."""
    meta = dict(CLOSED["metadata"])
    meta["tct_commit_%s" % C1[:12]] = "observed:sess-1:1785964200"
    gaps = audit.audit([dict(CLOSED, metadata=meta)], now=NOW,
                       commits=[_outside(C1)])

    assert [g for g in gaps if g["kind"] == audit.KIND_UNTRACKED_COMMITS] == []


def test_an_edge_on_any_issue_counts_not_only_the_matching_one(audit):
    """task↔commit is m:n, so there is no "the" issue for a commit. The question
    the audit asks is whether *anything* recorded this sha."""
    other = dict(_with_edge(C1[:12]), id="bd-other")
    gaps = audit.audit([dict(CLOSED, id="bd-a1b2"), other], now=NOW,
                       commits=[_outside(C1)])

    assert [g for g in gaps if g["kind"] == audit.KIND_UNTRACKED_COMMITS] == []


def test_a_malformed_edge_value_vouches_for_nothing(audit):
    """``commit_edges`` skips a value it cannot read, and a skipped edge must not
    silently suppress a gap — that would be an unreadable value quietly widening
    what the audit calls tracked."""
    meta = dict(CLOSED["metadata"])
    meta["abacus_commit_%s" % C1[:12]] = "garbage"
    gaps = audit.audit([dict(CLOSED, metadata=meta)], now=NOW,
                       commits=[_outside(C1)])

    assert [g["shas"] for g in gaps
            if g["kind"] == audit.KIND_UNTRACKED_COMMITS] == [[C1]]
