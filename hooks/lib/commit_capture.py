#!/usr/bin/env python3
"""Recording which commits a task produced, via a HEAD watermark.

No Claude Code hook fires on a git commit, and the sha is not reliably in the
command's stdout — ``-q`` suppresses git's ``[branch shortsha]`` line, it is
abbreviated when present, and real commands are compound with heredocs. So
capture asks git instead, comparing HEAD against a watermark recorded earlier in
the session (adr/015). Asking git is deterministic; parsing stdout is not.

**Four callers, one function.** The watcher calls this when a Bash command
contained a verb that could have moved HEAD; SessionStart, PreCompact, Stop and
SessionEnd call it with no trigger at all. That the verb list is a cheap trigger
and *not* the correctness mechanism is only true because of those other callers:
a commit made by a shell script, a Makefile target, ``gh pr merge``, or a verb
this plugin has never heard of costs at most one boundary's delay before a sweep
collects it. Splitting this into a watcher copy and a sweep copy would let the two
drift, and the one that drifted would be the one nobody was watching.

Three rails make the ``observed`` tier honest. Two live here; the second lives in
``attribution.build_commit_edges``, because it is a property of a single commit
rather than of the HEAD move:

1. **Seed, never attribute, on first sight.** No watermark means record HEAD and
   write nothing. Without it the first git command of a session would ask for
   everything reachable from HEAD and hang the repository's entire history on
   whatever task happens to be claimed. The single most important fail-safe here.
2. *(in attribution)* A commit older than the claim cannot have been observed
   being made during it. This is what makes ``git pull`` harmless.
3. **A move larger than the cap is not one boundary's work.** A rebase or a pull;
   record nothing and advance past it.

Nothing here writes into the user's repository — not a git hook, not a config
value, not a trailer. The only write is ``abacus_*`` metadata onto beads issues
the user already has.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import attribution  # noqa: E402
import beads  # noqa: E402
import gitlog  # noqa: E402
import hook_io  # noqa: E402
import state_store  # noqa: E402

DEFAULT_MAX_PER_BOUNDARY = 50


def _commits_cfg(cfg):
    block = (cfg or {}).get("commits")
    return block if isinstance(block, dict) else {}


def _cap(commits_cfg):
    """The most commits one HEAD move may contribute.

    An unreadable value falls back to the default, because a typo should not
    silently widen what gets attributed. A value of zero or less is taken
    literally and captures nothing — someone who wants capture off has
    ``commits.enabled`` for that, so the honest reading of ``0`` is ``0``.
    """
    try:
        return int(commits_cfg.get("max_per_boundary"))
    except (TypeError, ValueError):
        return DEFAULT_MAX_PER_BOUNDARY


def _inside(root, path):
    """True if `path` is `root` or lives beneath it."""
    try:
        root = os.path.realpath(root)
        path = os.path.realpath(path)
    except Exception:
        return False
    return path == root or path.startswith(root + os.sep)


def capture(session, cwd, cfg, reseed=False):
    """Record the commits that appeared since the watermark, and re-mark it.

    ``reseed=True`` re-marks and writes nothing — for verbs that move HEAD without
    creating anything (``checkout``, ``switch``, ``reset``). The difference between
    two branches is not work this task did, and attributing it would charge the
    claim for every commit that happens to live on the branch being switched to.

    The two cheap checks come first and in this order: the beads workspace is a
    filesystem walk, so a directory abacus has no business in costs no subprocess
    at all. Stop fires at the end of every turn, which makes that ordering the
    difference between a quiet plugin and two ``git`` invocations per turn
    everywhere on the machine.

    The watermark advances even when a write failed. It is the disposable side of
    the ledger: not advancing would buy an idempotent retry next boundary, but a
    persistently broken ``bd`` would then grow the range until it tripped the cap
    and captured nothing ever again. Losing one boundary is the smaller failure.
    """
    commits_cfg = _commits_cfg(cfg)
    if not commits_cfg.get("enabled", True):
        return

    # A `.beads` above a vendored checkout or a submodule governs the *outer*
    # project. Writing this repository's commits onto its issues would attribute
    # work to a project the commits are not part of.
    workspace = beads.workspace_root(cwd)
    if not workspace:
        return
    root = gitlog.repo_root(cwd)
    if not root or not _inside(root, workspace):
        return
    current = gitlog.head(cwd)
    if not current:
        return  # an empty repository: nothing to mark, nothing to diff

    state = state_store.load(session)
    marks = state.get("head_watermarks")
    marks = dict(marks) if isinstance(marks, dict) else {}
    previous = marks.get(root)

    def remark():
        marks[root] = current
        state_store.update(session, {"head_watermarks": marks})

    # Rail 1, plus the two cases where there is provably nothing to look at.
    if not previous or previous == current or reseed:
        remark()
        return

    cap = _cap(commits_cfg)
    commits = gitlog.new_commits(
        previous, cwd=cwd, limit=cap + 1,
        trailer_key=commits_cfg.get("trailer_key") or gitlog.DEFAULT_TRAILER_KEY)

    if len(commits) > cap:  # rail 3
        hook_io.log("HEAD moved by more than %d commits at once; recording no "
                    "edges for that move" % cap)
        remark()
        return

    edges = attribution.build_commit_edges(
        commits, session,
        current_task=state.get("current_task"),
        claimed_at=state.get("claimed_at"))
    for issue_id in sorted(edges):
        if not beads.set_metadata(issue_id, edges[issue_id], cwd=cwd):
            hook_io.log("could not write commit edges to %s" % issue_id)
    remark()
