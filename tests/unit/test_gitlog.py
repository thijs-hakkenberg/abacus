"""The git read surface, exercised against a real repository.

Every other external tool in this suite is stubbed. git is not, here, and the
reason is specific: ``gitlog`` exists to *parse git's output*, so a stub echoing
the format the test already assumed would pass no matter what git actually
prints. These tests build a real repository with the local binary — no network, so
the suite stays offline — and read it back.

``recent_commits`` shipped without any test at all; the first half of this module
is that missing coverage, written before the new functions were added so a
regression in it cannot hide behind them.

The recurring hazard throughout is the same one the module docstring names: a
failure that returns something plausible instead of nothing. A missing git, a
shallow clone, an unresolvable watermark and a directory that is not a repository
must all come back empty, because the caller's next move — say nothing about
commits — is the same in every case, and inventing an answer is how attribution
lands on the wrong task.
"""

import os
import subprocess

import pytest

from conftest import needs_git

pytestmark = needs_git


@pytest.fixture
def gitlog(lib_path):
    import gitlog as module

    return module


# ── has_repo: the guard that keeps git unspawned ────────────────────────────


def test_has_repo_is_false_for_a_directory_with_no_git_anywhere_above(gitlog, tmp_path):
    # tmp_path has no .git and neither does /var/folders/... above it. If this
    # ever became true the audit would start spawning git in directories that
    # have nothing to do with version control.
    assert gitlog.has_repo(str(tmp_path)) is False


def test_has_repo_walks_upward_from_a_subdirectory(gitlog, git_repo):
    deep = git_repo.path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert gitlog.has_repo(str(deep)) is True


def test_has_repo_accepts_a_git_file_as_a_worktree_or_submodule_marker(gitlog, tmp_path):
    """In a worktree or a submodule ``.git`` is a *file*, not a directory.

    ``os.path.exists`` is what makes this work; an ``os.path.isdir`` here would
    silently exclude every linked worktree.
    """
    root = tmp_path / "linked"
    root.mkdir()
    (root / ".git").write_text("gitdir: /somewhere/else/.git/worktrees/linked\n")
    assert gitlog.has_repo(str(root)) is True


# ── recent_commits: the pre-existing, previously untested reader ─────────────


def test_recent_commits_returns_newest_first_with_iso_timestamps(gitlog, git_repo):
    git_repo.commit("first", when=1_700_000_000)
    git_repo.commit("second", when=1_700_000_600)

    out = gitlog.recent_commits(cwd=str(git_repo.path), since="1970-01-01")

    assert [c["subject"] for c in out] == ["second", "first"]
    assert out[0]["at"] == "2023-11-14T22:23:20Z"
    assert len(out[0]["sha"]) == 40


def test_recent_commits_honours_the_limit(gitlog, git_repo):
    for i in range(4):
        git_repo.commit("c%d" % i)
    out = gitlog.recent_commits(cwd=str(git_repo.path), since="1970-01-01", limit=2)
    assert len(out) == 2


def test_recent_commits_excludes_merges(gitlog, git_repo):
    """``--no-merges`` is right for the audit and wrong for capture.

    Asserted rather than assumed, because ``new_commits`` deliberately does the
    opposite and the difference between the two is easy to erase by accident.
    """
    git_repo.commit("base")
    git_repo.merge_commit("landed a branch")

    subjects = [c["subject"] for c in
                gitlog.recent_commits(cwd=str(git_repo.path), since="1970-01-01")]
    assert "landed a branch" not in subjects


def test_recent_commits_survives_a_subject_containing_the_separator_candidates(
        gitlog, git_repo):
    # A subject with tabs, pipes and colons would break any printable delimiter;
    # the unit separator cannot appear in one.
    subject = "fix: a|b\tc — 100% done"
    git_repo.commit(subject)
    out = gitlog.recent_commits(cwd=str(git_repo.path), since="1970-01-01")
    assert out[0]["subject"] == subject


def test_recent_commits_is_empty_in_a_repository_with_no_commits(gitlog, git_repo):
    assert gitlog.recent_commits(cwd=str(git_repo.path), since="1970-01-01") == []


def test_recent_commits_is_empty_outside_a_repository(gitlog, tmp_path):
    assert gitlog.recent_commits(cwd=str(tmp_path)) == []


def test_recent_commits_is_empty_when_git_is_not_on_path(gitlog, git_repo, monkeypatch):
    git_repo.commit("one")
    monkeypatch.setattr(gitlog.shutil, "which", lambda _name: None)
    monkeypatch.delenv("ABACUS_GIT_CMD", raising=False)
    assert gitlog.recent_commits(cwd=str(git_repo.path)) == []


def test_recent_commits_is_empty_when_git_exits_non_zero(gitlog, git_repo, monkeypatch):
    # `sh -c false log ...` runs `false` with the git arguments as $0.. — a
    # broken git that is nonetheless present and executable.
    git_repo.commit("one")
    monkeypatch.setenv("ABACUS_GIT_CMD", "/bin/sh -c false")
    assert gitlog.recent_commits(cwd=str(git_repo.path)) == []


def test_recent_commits_is_empty_when_git_times_out(gitlog, git_repo, monkeypatch):
    git_repo.commit("one")

    def slow(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="git", timeout=gitlog.TIMEOUT_S)

    monkeypatch.setattr(gitlog.subprocess, "run", slow)
    assert gitlog.recent_commits(cwd=str(git_repo.path)) == []


def test_recent_commits_never_spawns_git_outside_a_repository(gitlog, tmp_path, monkeypatch):
    """The order matters: ``has_repo`` gates the subprocess, not the reverse."""
    calls = []
    monkeypatch.setattr(gitlog.subprocess, "run",
                        lambda *a, **kw: calls.append(a) or None)
    gitlog.recent_commits(cwd=str(tmp_path))
    assert calls == []


# ── repo_root: what the watermark is keyed on ───────────────────────────────


def test_repo_root_resolves_from_a_subdirectory(gitlog, git_repo):
    git_repo.commit("one")
    deep = git_repo.path / "x" / "y"
    deep.mkdir(parents=True)

    root = gitlog.repo_root(cwd=str(deep))

    assert root is not None
    assert os.path.realpath(root) == os.path.realpath(str(git_repo.path))


def test_repo_root_resolves_in_a_repository_with_no_commits(gitlog, git_repo):
    """The watermark is keyed on the root, so it must resolve before HEAD does."""
    assert gitlog.repo_root(cwd=str(git_repo.path)) is not None


def test_repo_root_is_none_outside_a_repository(gitlog, tmp_path):
    assert gitlog.repo_root(cwd=str(tmp_path)) is None


def test_repo_root_is_none_when_git_is_broken(gitlog, git_repo, monkeypatch):
    git_repo.commit("one")
    monkeypatch.setenv("ABACUS_GIT_CMD", "/bin/sh -c false")
    assert gitlog.repo_root(cwd=str(git_repo.path)) is None


# ── head: the watermark itself ──────────────────────────────────────────────


def test_head_is_the_full_sha(gitlog, git_repo):
    sha = git_repo.commit("one")
    assert gitlog.head(cwd=str(git_repo.path)) == sha


def test_head_is_none_before_the_first_commit(gitlog, git_repo):
    """An unborn HEAD must read as "no watermark yet", not as an error.

    Seeding a fresh repository is the commonest first sight of one, and a raise
    here would surface inside a hook.
    """
    assert gitlog.head(cwd=str(git_repo.path)) is None


def test_head_is_none_outside_a_repository(gitlog, tmp_path):
    assert gitlog.head(cwd=str(tmp_path)) is None


def test_head_resolves_a_detached_head(gitlog, git_repo):
    first = git_repo.commit("one")
    git_repo.commit("two")
    git_repo.run("checkout", "-q", "--detach", first)
    assert gitlog.head(cwd=str(git_repo.path)) == first


# ── new_commits: what landed since the watermark ────────────────────────────


def test_new_commits_returns_only_what_came_after_the_watermark(gitlog, git_repo):
    base = git_repo.commit("before", when=1_700_000_000)
    git_repo.commit("after one", when=1_700_000_100)
    git_repo.commit("after two", when=1_700_000_200)

    out = gitlog.new_commits(base, cwd=str(git_repo.path))

    assert [c["subject"] for c in out] == ["after one", "after two"]
    assert "before" not in [c["subject"] for c in out]


def test_new_commits_is_oldest_first(gitlog, git_repo):
    """Order is load-bearing: edges are written in the order the work happened."""
    base = git_repo.commit("base")
    git_repo.commit("a")
    git_repo.commit("b")

    assert [c["subject"] for c in gitlog.new_commits(base, cwd=str(git_repo.path))] == \
        ["a", "b"]


def test_new_commits_includes_merge_commits(gitlog, git_repo):
    """The deliberate divergence from ``recent_commits``.

    A merge commit is how a branch's work lands, so excluding it would drop the
    single commit most likely to be the point of a task.
    """
    base = git_repo.commit("base")
    git_repo.merge_commit("land the branch")

    subjects = [c["subject"] for c in gitlog.new_commits(base, cwd=str(git_repo.path))]
    assert "land the branch" in subjects


def test_new_commits_carries_iso_timestamps_converted_from_epoch(gitlog, git_repo):
    """``%ct`` seconds, not ``%cI`` — an offset would silently fail to parse."""
    base = git_repo.commit("base", when=1_700_000_000)
    git_repo.commit("later", when=1_700_000_600)

    out = gitlog.new_commits(base, cwd=str(git_repo.path))

    assert out[0]["at"] == "2023-11-14T22:23:20Z"


def test_new_commits_parses_a_beads_task_trailer(gitlog, git_repo):
    base = git_repo.commit("base")
    git_repo.commit("does the work", trailers="Beads-Task: abacus-7")

    out = gitlog.new_commits(base, cwd=str(git_repo.path))

    assert out[0]["declared"] == ["abacus-7"]


def test_new_commits_parses_several_declared_tasks_from_one_commit(gitlog, git_repo):
    """The only tier that can express true m:n — one commit, three tasks."""
    base = git_repo.commit("base")
    git_repo.commit("closes three",
                    trailers="Beads-Task: abacus-7, abacus-8, abacus-9")

    assert gitlog.new_commits(base, cwd=str(git_repo.path))[0]["declared"] == \
        ["abacus-7", "abacus-8", "abacus-9"]


def test_new_commits_parses_repeated_trailer_lines(gitlog, git_repo):
    base = git_repo.commit("base")
    git_repo.commit("closes two",
                    trailers="Beads-Task: abacus-7\nBeads-Task: abacus-8")

    assert gitlog.new_commits(base, cwd=str(git_repo.path))[0]["declared"] == \
        ["abacus-7", "abacus-8"]


def test_new_commits_declares_nothing_for_a_commit_with_no_trailer(gitlog, git_repo):
    base = git_repo.commit("base")
    git_repo.commit("plain work")

    assert gitlog.new_commits(base, cwd=str(git_repo.path))[0]["declared"] == []


def test_new_commits_ignores_an_unrelated_trailer(gitlog, git_repo):
    base = git_repo.commit("base")
    git_repo.commit("plain work",
                    trailers="Co-Authored-By: Someone <s@example.invalid>")

    assert gitlog.new_commits(base, cwd=str(git_repo.path))[0]["declared"] == []


def test_new_commits_is_empty_when_head_has_not_moved(gitlog, git_repo):
    base = git_repo.commit("base")
    assert gitlog.new_commits(base, cwd=str(git_repo.path)) == []


def test_new_commits_is_empty_without_a_watermark(gitlog, git_repo):
    """Rail 1, enforced here as well as in the caller.

    With no watermark the honest answer is "nothing observed", not "everything
    since the beginning of the repository" — which is what an unguarded range
    would return, attributing the entire history to whatever is claimed.
    """
    git_repo.commit("one")
    git_repo.commit("two")

    assert gitlog.new_commits(None, cwd=str(git_repo.path)) == []
    assert gitlog.new_commits("", cwd=str(git_repo.path)) == []


def test_new_commits_is_empty_when_the_watermark_no_longer_resolves(gitlog, git_repo):
    """A rebase, an amend or a shallow clone leaves a sha git cannot reach.

    git exits non-zero, and the answer is nothing — not "everything", which is
    what dropping the range on error would produce.
    """
    git_repo.commit("one")
    orphan = "0" * 40

    assert gitlog.new_commits(orphan, cwd=str(git_repo.path)) == []


def test_new_commits_is_empty_outside_a_repository(gitlog, tmp_path):
    assert gitlog.new_commits("0" * 40, cwd=str(tmp_path)) == []


def test_new_commits_is_empty_when_git_is_not_on_path(gitlog, git_repo, monkeypatch):
    base = git_repo.commit("base")
    git_repo.commit("after")
    monkeypatch.setattr(gitlog.shutil, "which", lambda _name: None)
    monkeypatch.delenv("ABACUS_GIT_CMD", raising=False)
    assert gitlog.new_commits(base, cwd=str(git_repo.path)) == []


def test_new_commits_is_empty_when_git_times_out(gitlog, git_repo, monkeypatch):
    base = git_repo.commit("base")
    git_repo.commit("after")

    def slow(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="git", timeout=gitlog.TIMEOUT_S)

    monkeypatch.setattr(gitlog.subprocess, "run", slow)
    assert gitlog.new_commits(base, cwd=str(git_repo.path)) == []


def test_new_commits_can_return_more_than_a_cap_so_the_caller_can_detect_it(
        gitlog, git_repo):
    """The caller's cap needs to see *over* the cap, not be clipped to it.

    Rail 3 declines a HEAD move larger than ``max_per_boundary``; if this
    clipped at the limit the caller could never tell an over-large move from an
    exactly-sized one, and a 500-commit pull would be attributed as 50.
    """
    base = git_repo.commit("base")
    for i in range(5):
        git_repo.commit("c%d" % i)

    assert len(gitlog.new_commits(base, cwd=str(git_repo.path), limit=3)) == 3
    assert len(gitlog.new_commits(base, cwd=str(git_repo.path), limit=4)) == 4


def test_new_commits_survives_a_subject_containing_the_separator_candidates(
        gitlog, git_repo):
    base = git_repo.commit("base")
    subject = "feat: a|b\tc: 100% — done"
    git_repo.commit(subject, trailers="Beads-Task: abacus-7")

    out = gitlog.new_commits(base, cwd=str(git_repo.path))

    assert out[0]["subject"] == subject
    assert out[0]["declared"] == ["abacus-7"]
