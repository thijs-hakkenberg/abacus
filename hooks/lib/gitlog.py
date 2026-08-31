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
"""

import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DEFAULT_SINCE = "30 days ago"
DEFAULT_LIMIT = 500
TIMEOUT_S = 5

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
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(epoch_text)))
    except (TypeError, ValueError, OSError):
        return None


def recent_commits(cwd=None, since=DEFAULT_SINCE, limit=DEFAULT_LIMIT):
    """``[{"sha", "at", "subject"}]``, newest first, or ``[]``.

    Returns ``[]`` — not None — for every failure and for "not a repository"
    alike, because the caller's next move is the same either way: run the other
    detectors and say nothing about untracked commits. The audit is allowed to
    miss things; it is not allowed to invent them.
    """
    if not has_repo(cwd):
        return []
    cmd = git_cmd()
    if not cmd:
        return []
    args = cmd + [
        "log", "--no-merges", "-n", str(int(limit)),
        "--since", str(since),
        "--pretty=format:%H" + _SEP + "%ct" + _SEP + "%s",
    ]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,  # an inherited pipe hangs forever
            timeout=TIMEOUT_S,
            cwd=cwd,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []

    out = []
    for line in (proc.stdout or "").splitlines():
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
