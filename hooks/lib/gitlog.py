#!/usr/bin/env python3
"""Reading commits, so the audit can ask which work never became a task.

The gate stops an ``Edit``; it cannot stop a ``sed -i``, a heredoc, or a commit
made from another terminal. Commits are the durable evidence that work happened,
so comparing them against claim windows is the only way to find work that was
never tracked at all.

Two details are deliberate:

**Timestamps come back as ``%ct`` (unix seconds), not ``%cI``.** Git's strict-ISO
format carries a numeric offset (``+02:00``), and ``abacus_time.parse_iso`` only
strips a trailing ``Z`` — an offset would silently fail to parse and every commit
would become unplaceable in time. Converting from epoch seconds here removes the
timezone question entirely.

**git is never spawned unless a ``.git`` directory is actually there.** Not for
speed: a ``git`` invocation from inside a hook or a test is a real subprocess
against a real repository, and the audit must be able to run in a directory that
has nothing to do with version control without touching it.

Two readers, deliberately different. :func:`recent_commits` answers the audit's
retrospective question and passes ``--no-merges``, because a merge there would
double-count work already listed beneath it. :func:`new_commits` answers capture's
question — what landed since the watermark — and **includes merges**, because a
merge or squash commit is precisely how a branch's work arrives, and it is the
commit most likely to be the point of a task. Erasing that difference in either
direction is a silent behaviour change, so both are asserted in the tests.

Nothing here writes. Not to the repository, not to git's config, not a hook — a
write into the user's repository is the adr/012 class of action, and capture does
not need one: asking git is deterministic where parsing a commit's stdout is not.
"""

import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import abacus_time  # noqa: E402

DEFAULT_SINCE = "30 days ago"
DEFAULT_LIMIT = 500
TIMEOUT_S = 5

# The commit trailer by which a commit *declares* which tasks it belongs to. Git
# parses trailers itself, so the strongest tier of evidence needs no regex.
DEFAULT_TRAILER_KEY = "Beads-Task"

# ASCII unit separator: cannot occur in a sha, a timestamp, or a commit subject,
# unlike every printable delimiter a subject line might legitimately contain.
_SEP = "\x1f"


def git_cmd():
    override = os.environ.get("ABACUS_GIT_CMD")
    if override:
        return override.split()
    found = shutil.which("git")
    return [found] if found else None


def has_repo(start_dir=None):
    """True if `start_dir` or an ancestor holds a ``.git``.

    Walks upward for the same reason ``beads.has_workspace`` does: a hook's cwd is
    routinely a subdirectory of the repository root.
    """
    path = os.path.abspath(start_dir or os.getcwd())
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return True
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent


def _iso(epoch_text):
    # Delegated rather than reimplemented: commit edges store an epoch and read
    # it back, so a second copy of this conversion could disagree with the first.
    return abacus_time.iso_from_epoch(epoch_text)


def _git(args, cwd=None):
    """Run git and return stdout, or None on any failure at all.

    "Not a repository", "git is missing", "git is wedged" and "git said no" all
    collapse to None on purpose: every caller's next move is the same, and the
    one thing none of them may do is guess. Never raises.
    """
    if not has_repo(cwd):
        return None
    cmd = git_cmd()
    if not cmd:
        return None
    try:
        proc = subprocess.run(
            cmd + list(args),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,  # an inherited pipe hangs forever
            timeout=TIMEOUT_S,
            cwd=cwd,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout or ""


def recent_commits(cwd=None, since=DEFAULT_SINCE, limit=DEFAULT_LIMIT):
    """``[{"sha", "at", "subject"}]``, newest first, or ``[]``.

    Returns ``[]`` — not None — for every failure and for "not a repository"
    alike, because the caller's next move is the same either way: run the other
    detectors and say nothing about untracked commits. The audit is allowed to
    miss things; it is not allowed to invent them.

    ``--no-merges`` is right *here* and wrong in :func:`new_commits`; see the
    module docstring.
    """
    stdout = _git([
        "log", "--no-merges", "-n", str(int(limit)),
        "--since", str(since),
        "--pretty=format:%H" + _SEP + "%ct" + _SEP + "%s",
    ], cwd=cwd)
    if stdout is None:
        return []

    out = []
    for line in stdout.splitlines():
        parts = line.split(_SEP)
        if len(parts) < 2:
            continue
        at = _iso(parts[1])
        if at is None:
            continue
        out.append({
            "sha": parts[0],
            "at": at,
            "subject": parts[2] if len(parts) > 2 else "",
        })
    return out


def repo_root(cwd=None):
    """The repository's top-level directory, or None.

    The watermark is keyed on this rather than on the hook's cwd, so that a
    commit made from a subdirectory is measured against the same mark as one made
    from the root. ``--show-toplevel`` also resolves the case ``has_repo`` cannot
    read: a worktree or submodule where ``.git`` is a file pointing elsewhere.
    """
    stdout = _git(["rev-parse", "--show-toplevel"], cwd=cwd)
    if stdout is None:
        return None
    root = stdout.strip()
    return root or None


def head(cwd=None):
    """The full sha at HEAD, or None when there is nothing to mark.

    None covers a repository with no commits yet and a detached-but-unresolvable
    HEAD alike. A freshly initialised repository is a perfectly ordinary thing to
    seed a watermark for, so this must answer rather than raise.
    """
    stdout = _git(["rev-parse", "HEAD"], cwd=cwd)
    if stdout is None:
        return None
    sha = stdout.strip()
    return sha or None


def _declared(field, trailer_key=DEFAULT_TRAILER_KEY):
    """Task ids from a rendered trailer field.

    Git has already done the parsing — repeated trailer lines arrive joined by
    the separator we asked for, and a single line naming three tasks arrives as
    one comma-separated value. Both flatten to the same list, which is the whole
    reason this tier can express m:n at all.
    """
    del trailer_key  # git filtered by key; kept for signature symmetry
    return [part.strip() for part in (field or "").split(",") if part.strip()]


def new_commits(since_sha, cwd=None, limit=DEFAULT_LIMIT,
                trailer_key=DEFAULT_TRAILER_KEY):
    """``[{"sha", "at", "subject", "declared"}]`` for ``since_sha..HEAD``, oldest first.

    Oldest first because edges are written in the order the work happened.

    **No watermark means no commits.** An empty ``since_sha`` returns ``[]``
    rather than falling back to a bare ``git log``, which would report the whole
    history and attribute every commit the repository has ever had to whatever
    task happens to be claimed. That is the single worst outcome available to
    this feature, so it is refused twice — here, and again in the caller.

    A ``since_sha`` git can no longer reach — rebased away, amended, or absent
    from a shallow clone — makes git exit non-zero, which returns ``[]``. Not the
    whole history: dropping the range on error is the same catastrophe by a
    different route.

    ``limit`` is a ceiling, not a cap the caller must live with: it may return
    exactly ``limit`` entries, so a caller enforcing a maximum should ask for one
    more than it will accept and refuse the over-large answer.
    """
    if not since_sha:
        return []
    stdout = _git([
        "log", "--reverse", "-n", str(int(limit)),
        "%s..HEAD" % since_sha,
        # Deliberately no --no-merges: a merge commit is how a branch's work
        # lands. The trailers atom must carry an inline separator, or its default
        # newline would break the one-line-per-commit shape this parses.
        "--pretty=format:%H" + _SEP + "%ct" + _SEP + "%s" + _SEP
        + "%(trailers:key=" + trailer_key + ",valueonly,separator=%x2C)",
    ], cwd=cwd)
    if stdout is None:
        return []

    out = []
    for line in stdout.splitlines():
        parts = line.split(_SEP)
        if len(parts) < 2:
            continue
        at = _iso(parts[1])
        if at is None:
            continue
        out.append({
            "sha": parts[0],
            "at": at,
            "subject": parts[2] if len(parts) > 2 else "",
            "declared": _declared(parts[3] if len(parts) > 3 else ""),
        })
    return out
