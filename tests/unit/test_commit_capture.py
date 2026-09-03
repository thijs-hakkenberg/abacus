"""RED: capturing commits — the HEAD watermark and its three rails.

No Claude Code hook fires on a git commit, and the sha is not reliably in the
command's stdout (`-q` suppresses it, and real commands are compound). So capture
asks git instead: the watcher notices that a command *could* have moved HEAD, and
compares HEAD against a watermark recorded earlier in the session.

The verb list is a trigger, not the correctness mechanism. **The watermark is.**
That is what makes the three rails the substance of this module:

1. **Seed, never attribute, on first sight.** With no watermark, record HEAD and
   write nothing. Without this, the first git command in a session attributes the
   repository's entire history to whatever task is claimed. It is the single most
   important fail-safe here, and it is tested first.
2. **A commit older than the claim cannot have been observed being made during
   it.** This is what makes `git pull` harmless rather than catastrophic.
3. **A HEAD move larger than the cap is a rebase or a pull, not this session's
   work.** Record nothing, advance the watermark.

Every failure — no repository, no git, git broken, no workspace, a workspace that
belongs to a different repository — writes nothing and exits 0.
"""

import pytest

from conftest import post_bash_payload

BASE = "ba5eba5e" * 5
C1 = "c0ffee01" * 5
C2 = "dec0de02" * 5
LATER = "f00dbabe" * 5

CLAIMED_AT = "2026-08-05T21:00:00Z"      # epoch 1785963600
AFTER_1 = 1785964200                      # 21:10:00Z
AFTER_2 = 1785964800                      # 21:20:00Z
BEFORE = 1785960000                       # 20:00:00Z — predates the claim

SEP = "\x1f"


def git_log(*rows):
    """Stub stdout in the shape `gitlog.new_commits` asks git for.

    ``sha \x1f epoch \x1f subject \x1f trailers`` — the unit separator, because a
    commit subject can contain every printable delimiter but not that.
    """
    return "".join(
        SEP.join([sha, str(epoch), subject, trailers]) + "\n"
        for sha, epoch, subject, trailers in rows
    )


def commit_row(sha, epoch, subject="do the work", declares=""):
    return (sha, epoch, subject, declares)


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


def _watch(harness, command="git commit -m 'fix: thing'", **kw):
    return harness.run_hook("watch_bd_commands.py", post_bash_payload(command), **kw)


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


# ── rail 1: seed, never attribute, on first sight ───────────────────────────


def test_the_first_git_command_seeds_the_watermark_and_writes_nothing(repo):
    """The most important test in this module.

    Without the seed rail, the first `git commit` of a session would ask for
    everything reachable from HEAD and hang the repository's whole history on the
    claimed task.
    """
    repo.write_state("sess-1", _claimed_state())
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    res = _watch(repo)

    assert res.rc == 0
    assert _edges(repo) == {}
    assert _watermark(repo) == LATER


def test_the_seed_never_asks_git_for_a_commit_range(repo):
    """Belt and braces: the range is not merely ignored, it is never requested."""
    repo.write_state("sess-1", _claimed_state())

    _watch(repo)

    assert [c for c in repo.git_calls() if c.startswith("git log")] == []


def test_a_watermark_lost_mid_session_reseeds_rather_than_reattributing(repo):
    """Deleting the state file must cost one boundary's edges, not invent any."""
    repo.write_state("sess-1", _claimed_state())  # no head_watermarks key
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo)

    assert _edges(repo) == {}
    assert _watermark(repo) == LATER


# ── the observed edge ───────────────────────────────────────────────────────


def test_a_commit_made_under_a_claim_is_written_as_observed(repo):
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo)

    assert _edges(repo) == {
        "bd-a1b2": {"abacus_commit_" + C1[:12]: "observed:sess-1:%d" % AFTER_1},
    }


def test_two_commits_in_one_boundary_become_two_keys(repo):
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1),
                                       commit_row(C2, AFTER_2)))

    _watch(repo)

    assert sorted(_edges(repo)["bd-a1b2"]) == sorted(
        ["abacus_commit_" + C1[:12], "abacus_commit_" + C2[:12]])


def test_the_watermark_advances_past_captured_commits(repo):
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo)

    assert _watermark(repo) == LATER


def test_the_range_asked_of_git_starts_at_the_watermark(repo):
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo)

    assert any("%s..HEAD" % BASE in c for c in repo.git_calls())


def test_no_edge_is_written_when_nothing_is_claimed(repo):
    """HEAD moved with no task in progress. There is nothing to attribute it to."""
    repo.write_state("sess-1", {"session_id": "sess-1",
                                "head_watermarks": {str(repo.project): BASE}})
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo)

    assert _edges(repo) == {}
    assert _watermark(repo) == LATER, "the watermark still advances"


# ── the declared edge ──────────────────────────────────────────────────────


def test_a_trailer_writes_to_every_task_it_names(repo):
    """The m:n case. One commit, three issues, basis `declared`."""
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(
        commit_row(C1, AFTER_1, declares="bd-one,bd-two,bd-three")))

    _watch(repo)

    edges = _edges(repo)
    assert sorted(edges) == ["bd-one", "bd-three", "bd-two"]
    key = "abacus_commit_" + C1[:12]
    assert all(edges[i][key].startswith("declared:") for i in edges)


def test_a_declared_commit_needs_no_claim(repo):
    repo.write_state("sess-1", {"session_id": "sess-1",
                                "head_watermarks": {str(repo.project): BASE}})
    repo.set_git("log", stdout=git_log(
        commit_row(C1, AFTER_1, declares="bd-nine")))

    _watch(repo)

    assert list(_edges(repo)) == ["bd-nine"]


# ── rail 2: older than the claim ───────────────────────────────────────────


def test_a_pull_of_commits_older_than_the_claim_writes_nothing(repo):
    """Rail 2, in the shape it actually arrives in.

    `git pull` moves HEAD by other people's commits. Every one predates the
    claim, so none of them can have been observed being made during it.
    """
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, BEFORE),
                                       commit_row(C2, BEFORE)))

    _watch(repo, "git pull --rebase")

    assert _edges(repo) == {}
    assert _watermark(repo) == LATER


def test_an_older_commit_is_dropped_while_a_newer_one_is_kept(repo):
    """Per commit, not per boundary — a merge brings both shapes at once."""
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, BEFORE),
                                       commit_row(C2, AFTER_2)))

    _watch(repo, "git merge origin/main")

    assert list(_edges(repo)["bd-a1b2"]) == ["abacus_commit_" + C2[:12]]


# ── rail 3: the per-boundary cap ───────────────────────────────────────────


def test_a_head_move_larger_than_the_cap_writes_nothing(repo):
    """A rebase or a pull, not this session's work."""
    repo.write_config({"commits": {"max_per_boundary": 2}})
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(
        commit_row(C1, AFTER_1), commit_row(C2, AFTER_1), commit_row(LATER, AFTER_2)))

    _watch(repo)

    assert _edges(repo) == {}
    assert _watermark(repo) == LATER, "and the watermark advances past them"


def test_a_head_move_exactly_at_the_cap_is_still_captured(repo):
    """The cap is a maximum, not an exclusive bound."""
    repo.write_config({"commits": {"max_per_boundary": 2}})
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1),
                                       commit_row(C2, AFTER_2)))

    _watch(repo)

    assert len(_edges(repo)["bd-a1b2"]) == 2


def test_the_cap_asks_git_for_one_more_than_it_will_accept(repo):
    """Otherwise an over-cap move is indistinguishable from an exact one."""
    repo.write_config({"commits": {"max_per_boundary": 2}})
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo)

    assert any("-n 3" in c for c in repo.git_calls())


# ── reseeding verbs ────────────────────────────────────────────────────────


@pytest.mark.parametrize("command", [
    "git checkout main",
    "git switch -c feature/x",
    "git reset --hard origin/main",
])
def test_a_verb_that_moves_head_without_creating_anything_only_reseeds(repo, command):
    """A branch switch changes HEAD without doing any work.

    Attributing the difference would charge the claimed task for every commit
    that happens to be on the other branch.
    """
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo, command)

    assert _edges(repo) == {}
    assert _watermark(repo) == LATER


# ── the trigger surface ────────────────────────────────────────────────────


@pytest.mark.parametrize("command", [
    "git commit -m 'x'",
    "git commit -q -F - <<'EOF'\nsubject\nEOF",
    "git merge --no-ff side",
    "git rebase main",
    "git cherry-pick abc123",
    "git revert HEAD",
    "git am /tmp/patch",
    "git pull",
    "cd sub && git commit -m x",
    "/usr/bin/git commit -m x",
    "GIT_AUTHOR_NAME=x git commit -m y",
])
def test_every_head_moving_verb_triggers_a_capture(repo, command):
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo, command)

    assert _edges(repo) != {}, "%r should have been a capture" % command


@pytest.mark.parametrize("command", [
    "git status",
    "git log --oneline -5",
    "git diff HEAD~1",
    "git show abc123",
    "git branch -a",
])
def test_a_read_only_git_command_is_not_a_boundary(repo, command):
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo, command)

    assert _edges(repo) == {}
    assert repo.git_calls() == [], "a read-only git command must spawn nothing"


def test_a_git_commit_documented_inside_a_heredoc_is_data(repo):
    """The heredoc body is documentation, not a command.

    Already handled by `_skip_heredoc_bodies`; asserted because a hallucinated
    boundary here would attribute an unrelated commit range.
    """
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo, "cat > README.md <<'EOF'\nRun `git commit -m x` to finish.\nEOF")

    assert repo.git_calls() == []


def test_the_word_git_inside_a_quoted_string_is_not_a_boundary(repo):
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo, "echo \"git commit -m x\"")

    assert repo.git_calls() == []


def test_a_similarly_named_executable_is_not_git(repo):
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))

    _watch(repo, "gitleaks detect --no-banner")

    assert repo.git_calls() == []


def test_a_command_holding_both_a_commit_and_a_close_captures_before_closing(repo):
    """Order is load-bearing.

    `bd close` clears `current_task`, so a capture that ran afterwards would have
    no task to attribute the commit to.
    """
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))
    repo.set_bd_json("show", [{"id": "bd-a1b2", "title": "t", "status": "closed"}])

    _watch(repo, "git commit -m x && bd close bd-a1b2")

    assert "bd-a1b2" in _edges(repo)


# ── everything that must write nothing ─────────────────────────────────────


def test_a_git_command_outside_a_repository_spawns_nothing(harness):
    """`has_repo` gates the subprocess, so the 95% path stays free."""
    harness.make_beads_project()
    harness.write_state("sess-1", _claimed_state())

    res = _watch(harness)

    assert res.rc == 0
    assert harness.calls() == []


def test_a_git_repository_with_no_beads_workspace_writes_nothing(harness):
    harness.make_git_project()
    harness.set_git("rev-parse.--show-toplevel", stdout=str(harness.project) + "\n")
    harness.set_git("rev-parse.HEAD", stdout=LATER + "\n")
    harness.write_state("sess-1", _claimed_state(
        head_watermarks={str(harness.project): BASE}))

    _watch(harness)

    assert _edges(harness) == {}


def test_a_workspace_belonging_to_a_different_repository_writes_nothing(harness):
    """`.beads` above the repository root is somebody else's bookkeeping.

    Writing this repository's commits onto that workspace's issues would attribute
    work to a project it does not belong to.
    """
    harness.make_beads_project()                       # .beads at the parent
    inner = harness.make_git_project(harness.project / "vendored")
    harness.set_git("rev-parse.--show-toplevel", stdout=str(inner) + "\n")
    harness.set_git("rev-parse.HEAD", stdout=LATER + "\n")
    harness.write_state("sess-1", _claimed_state(
        head_watermarks={str(inner): BASE}))
    harness.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(harness, cwd=inner)

    assert _edges(harness) == {}


def test_git_missing_from_path_writes_nothing_and_exits_zero(repo):
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.remove_git()

    res = _watch(repo)

    assert res.rc == 0
    assert _edges(repo) == {}


def test_a_broken_git_writes_nothing_and_exits_zero(repo):
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout="", rc=128)

    res = _watch(repo)

    assert res.rc == 0
    assert _edges(repo) == {}


def test_a_head_that_cannot_be_resolved_writes_nothing(repo):
    """An empty repository. There is no watermark to record and nothing to diff."""
    repo.write_state("sess-1", _claimed_state())
    repo.set_git("rev-parse.HEAD", stdout="", rc=128)

    res = _watch(repo)

    assert res.rc == 0
    assert _edges(repo) == {}
    assert _watermark(repo) is None


def test_capture_can_be_switched_off(repo):
    repo.write_config({"commits": {"enabled": False}})
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo)

    assert _edges(repo) == {}
    assert repo.git_calls() == [], "switched off means not even a rev-parse"


def test_an_unacknowledged_install_captures_nothing(repo):
    """Consent gates every write, and an edge is a write (adr/014).

    Revoked rather than merely un-re-acknowledged: ``commits.*`` is deliberately
    outside ``consent.GOVERNING_KEYS``, since capture denies no tool call, writes
    into no repository and reaches no remote. So switching it on does *not* make
    consent stale — re-asking for a setting like that is what trains people to
    dismiss the notice unread. A just-installed abacus is the state that must
    capture nothing.
    """
    repo.revoke_acknowledgement()
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo)

    assert _edges(repo) == {}
    assert repo.git_calls() == []


def test_the_kill_switch_captures_nothing(repo):
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={str(repo.project): BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo, ABACUS_DISABLE="1")

    assert _edges(repo) == {}
    assert repo.git_calls() == []


def test_a_watermark_for_another_repository_does_not_seed_this_one(repo):
    """Watermarks are keyed per repository root, and must not leak across them."""
    repo.write_state("sess-1", _claimed_state(
        head_watermarks={"/somewhere/else": BASE}))
    repo.set_git("log", stdout=git_log(commit_row(C1, AFTER_1)))

    _watch(repo)

    assert _edges(repo) == {}
    state = repo.read_state("sess-1")
    assert state["head_watermarks"]["/somewhere/else"] == BASE
    assert state["head_watermarks"][str(repo.project)] == LATER
