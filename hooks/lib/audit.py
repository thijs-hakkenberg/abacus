#!/usr/bin/env python3
"""Detects where tracked work and its attribution came apart.

The gate stops an *edit* from happening untracked. It cannot stop a `sed -i`, a
heredoc, or a commit made from another terminal, and it has nothing to say about a
task that was closed while ccusage was unreadable. So gaps accumulate: work with
no task, tasks with no figures, figures banked but never finalised. This module
finds them.

Five detectors, one shape. Each returns gaps as plain dicts so the CLI can emit
them as JSON and an agent can act on them without re-deriving anything:

    {"kind": ..., "issue_id": ..., "title": ..., "detail": ..., "fixable": bool}

Everything here is a pure function over the list ``bd list --all --json`` returns
plus a list of commits. No subprocess, no clock unless one is passed in — the
caller supplies ``now``. That is what makes the awkward cases (a missing
``started_at``, a legacy metadata prefix, a malformed blob) testable at all.

**The recurring hazard is the false positive.** A gap reported on an issue that is
actually fine costs a pointless write to the store of record, and under ``--fix``
that write happens with nobody looking — the audit corrupting the data it exists
to protect. So every detector's ambiguous case resolves to *no gap*: an unreadable
timestamp is not evidence of staleness, an unrecognised ``abacus_schema`` is not
evidence of absence, a commit that cannot be placed in time is not evidence of
untracked work. Silence is the safe answer, and the audit is allowed to miss
things.

Only two of the five gaps are ``fixable``. Both are metadata repairs with exactly
one correct outcome. What to do about a stale claim or an untracked afternoon is a
judgement about intent, and this module does not make judgements — it reports, and
the agent or the user decides.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import abacus_time  # noqa: E402
import attribution  # noqa: E402

KIND_UNCLAIMED = "unclaimed"
KIND_STALE_CLAIM = "stale-claim"
KIND_UNFINALISED = "unfinalised"
KIND_UNATTRIBUTED = "unattributed"
KIND_UNTRACKED_COMMITS = "untracked-commits"

# Ordering of the report. Cheapest to act on first, and the one that blocks the
# user right now (nothing claimed) at the top.
KIND_ORDER = (
    KIND_UNCLAIMED,
    KIND_STALE_CLAIM,
    KIND_UNFINALISED,
    KIND_UNATTRIBUTED,
    KIND_UNTRACKED_COMMITS,
)

DEFAULT_STALE_AFTER_H = 24

# The one schema version this reader understands. An issue declaring anything else
# was written by a version that knows more than this one, and is left alone.
KNOWN_SCHEMA = attribution.SCHEMA_VERSION


def metadata_of(issue):
    """`issue`'s metadata with pre-rename ``tct_`` keys read as ``abacus_``.

    Routed through ``attribution`` so the shim has one implementation. Reading
    without it would report every issue tracked before 0.3.0 as unattributed, and
    ``--fix`` would then overwrite real recorded figures with ``unavailable``.
    """
    meta = issue.get("metadata")
    if not isinstance(meta, dict):
        return {}
    return attribution._normalise_prefix(meta)


def _hours_between(start_iso, end_iso):
    """Hours from `start_iso` to `end_iso`, or None if either cannot be read."""
    if not start_iso or not end_iso:
        return None
    minutes = abacus_time.minutes_between(start_iso, end_iso)
    if minutes is None:
        return None
    return minutes / 60.0


def _seconds(iso):
    at = abacus_time.parse_iso(iso) if iso else None
    return at


def _is_attributed(meta):
    """True when this plugin has written a final figure to the issue.

    ``abacus_schema`` is the marker rather than any cost key, because a task whose
    cost could not be read is still attributed — it carries
    ``abacus_cost_basis=unavailable`` and no dollar figure, deliberately (adr/005).
    Keying off the cost would report every honest unavailable as a gap and invite
    overwriting it.
    """
    return "abacus_schema" in meta


def _schema_is_known(meta):
    try:
        return int(float(meta.get("abacus_schema"))) == KNOWN_SCHEMA
    except (TypeError, ValueError):
        return False


def _gap(kind, issue_id, title, detail, fixable, **extra):
    gap = {
        "kind": kind,
        "issue_id": issue_id,
        "title": title,
        "detail": detail,
        "fixable": fixable,
    }
    gap.update(extra)
    return gap


def audit(issues, now=None, commits=(), stale_after_h=DEFAULT_STALE_AFTER_H):
    """Every gap in `issues`, ordered by kind then issue id.

    `issues` is ``bd list --all --json`` — the whole workspace including closed
    issues with their metadata, which is why one bd call is enough. `commits` is a
    list of ``{"sha", "at", "subject"}``; pass ``()`` to skip that detector
    entirely, which is what happens outside a git repository.
    """
    issues = [i for i in (issues or []) if isinstance(i, dict)]
    now = now or abacus_time.now_iso()
    gaps = []

    in_progress = [i for i in issues if i.get("status") == "in_progress"]

    if not in_progress:
        gaps.append(_gap(
            KIND_UNCLAIMED, None, None,
            "No issue is in progress, so edits are denied and any work done now "
            "would be attributed to nothing.",
            False,
        ))

    for i in in_progress:
        age_h = _hours_between(i.get("started_at"), now)
        if age_h is None or age_h < stale_after_h:
            continue
        gaps.append(_gap(
            KIND_STALE_CLAIM, i.get("id"), i.get("title"),
            "Claimed %d hours ago and still in progress. Its cost keeps "
            "accumulating against this one task." % int(age_h),
            False, age_hours=int(age_h),
        ))

    for i in issues:
        if i.get("status") != "closed":
            continue
        meta = metadata_of(i)
        issue_id, title = i.get("id"), i.get("title")

        if _is_attributed(meta):
            if not _schema_is_known(meta):
                continue  # a shape this reader does not know; decline to judge it
            if attribution._is_true(meta.get("abacus_partial")):
                gaps.append(_gap(
                    KIND_UNFINALISED, issue_id, title,
                    "Closed, but its attribution is still marked unfinished — the "
                    "spend was banked and the final figure never written.",
                    True, session_id=meta.get("abacus_session_id"),
                ))
            continue

        gaps.append(_gap(
            KIND_UNATTRIBUTED, issue_id, title,
            "Closed with no abacus attribution at all — closed outside a tracked "
            "session, or the watcher never saw the close.",
            True, session_id=None,
        ))

    untracked = _untracked_commits(issues, commits, now)
    if untracked:
        gaps.append(_gap(
            KIND_UNTRACKED_COMMITS, None, None,
            "%d commit(s) fall outside every task's claim window, so the work they "
            "contain was never tracked." % len(untracked),
            False, shas=[c["sha"] for c in untracked],
            subjects=[c.get("subject") for c in untracked],
        ))

    return sorted(gaps, key=lambda g: (KIND_ORDER.index(g["kind"]), g["issue_id"] or ""))


def _windows(issues, now):
    """Each issue's [claimed, closed] interval as epoch seconds.

    An issue still in progress has an open window ending *now* rather than a
    zero-width one — work committed under a live claim is tracked, and treating
    the window as closed at the claim instant would report all of it as untracked.
    """
    out = []
    for i in issues:
        start = _seconds(i.get("started_at"))
        if start is None:
            continue
        if i.get("status") == "closed":
            end = _seconds(i.get("closed_at")) or _seconds(i.get("updated_at"))
        else:
            end = _seconds(now)
        if end is None or end < start:
            continue
        out.append((start, end))
    return out


def _recorded_shas(issues):
    """Every commit sha12 that some issue carries an ``abacus_commit_*`` edge for.

    A set across the whole workspace, not per issue: task↔commit is m:n, so there
    is no *the* issue for a commit. The question is whether anything recorded this
    sha, and one commit closing three tasks is the normal case rather than the
    awkward one.

    Read through ``attribution.commit_edges``, which is also what makes a
    malformed value vouch for nothing — it skips a value it cannot parse, and an
    unreadable value must not quietly widen what the audit calls tracked.
    """
    out = set()
    for i in issues:
        for edge in attribution.commit_edges(metadata_of(i)):
            out.add(edge["sha12"])
    return out


def _untracked_commits(issues, commits, now):
    windows = _windows(issues, now)
    # A commit with a written edge was tracked by a mechanism the window
    # arithmetic cannot see: the edge records what was *observed* at capture, where
    # a claim window can only support what is *inferred* from timestamps (adr/015).
    # This narrows the gap set and never widens it — nothing new is reported here,
    # and nothing becomes fixable that was not.
    recorded = _recorded_shas(issues)
    out = []
    for c in commits or ():
        if not isinstance(c, dict):
            continue
        if str(c.get("sha") or "")[:attribution.SHA_LEN].lower() in recorded:
            continue
        at = _seconds(c.get("at"))
        if at is None:
            continue  # cannot place it in time, so cannot call it untracked
        if any(start <= at <= end for start, end in windows):
            continue
        out.append(c)
    return out
