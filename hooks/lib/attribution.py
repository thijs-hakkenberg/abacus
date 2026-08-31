#!/usr/bin/env python3
"""Turning two ccusage readings into one task's recorded cost.

Four hooks need to finalise a task — the Bash watcher on ``bd close``, the same
watcher when a claim supersedes an unclosed task, Stop when a close happened
outside our view, and SessionEnd when the session ends mid-task. They must all
write the *same* keys with the *same* accumulation semantics, so the logic lives
here once rather than in four places that would drift.

Two rules are worth stating plainly, because both are about not lying:

**An unreadable cost is omitted, never zeroed.** If ccusage cannot be read the
metadata carries ``abacus_cost_basis=unavailable`` and no dollar figure at all. A
``$0.00`` against a task that ran for an hour is a wrong answer wearing the
costume of a measurement; an absent key prompts a question instead.

**The dollar figure never travels alone.** ``abacus_cost_usd_estimate`` is always
written beside ``abacus_cost_basis``, so a number computed from a local list-rate
table on one developer's machine cannot later be quoted as billing. A predecessor
tool of mine had to withdraw its dollar figures entirely for exactly this reason —
a total next to a project label reads as an invoice no matter what the surrounding
prose says. Keeping the number but never letting it travel unlabelled is the
compromise (adr/005).

Accumulation: a task interrupted by SessionEnd is written with
``abacus_partial=true``. When it is finally closed, those figures are read back and
added to, so a task spanning three sessions reports its whole cost. Only
``abacus_partial=true`` metadata is carried forward — a finalised figure is left
alone, or closing an issue twice would double it. Verified on bd 1.1.2:
``--set-metadata`` merges into existing metadata and works on closed issues, so
the read-modify-write is safe and needs no race with ``bd close``.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import beads  # noqa: E402
import ccusage  # noqa: E402
import otel  # noqa: E402
import state_store  # noqa: E402
import abacus_time  # noqa: E402

SCHEMA_VERSION = 1
COST_BASIS = "ccusage-local-list-rate"
BASIS_UNAVAILABLE = "unavailable"

# The metadata prefix this plugin wrote before it was renamed to abacus. Read
# only, never written — see ``_normalise_prefix``.
LEGACY_PREFIX = "tct_"

# metadata key -> ccusage delta key
_TOKEN_KEYS = (
    ("abacus_tokens_total", "tokens"),
    ("abacus_tokens_in", "input_tokens"),
    ("abacus_tokens_out", "output_tokens"),
    ("abacus_tokens_cache_read", "cache_read_tokens"),
    ("abacus_tokens_cache_write", "cache_creation_tokens"),
)


def _number(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_true(flag):
    return flag is True or str(flag).lower() == "true"


def _normalise_prefix(meta):
    """Rewrite pre-rename ``tct_`` keys to ``abacus_``, current keys winning.

    Every carried figure in ``build_metadata`` is read through the dict this
    returns, so normalising once here covers all of them. Reads have to
    understand the old prefix or a task left partial across the upgrade is
    orphaned — its accumulated spend becomes invisible and the closing write
    reports only the final session. Writes stay ``abacus_*`` only; supporting two
    write prefixes would be a fork, not a shim.
    """
    out = {}
    for key, value in meta.items():
        if key.startswith(LEGACY_PREFIX):
            key = "abacus_" + key[len(LEGACY_PREFIX):]
            if key in meta:
                continue  # a current key of the same name has already been set
        out[key] = value
    return out


def carried_partial(issue_id, cwd=None):
    """Unfinished figures previously written to `issue_id`, or ``{}``."""
    issue = beads.show(issue_id, cwd=cwd) or {}
    meta = issue.get("metadata")
    if not isinstance(meta, dict):
        return {}
    # A task finalised after the rename keeps its stale `tct_partial=true`
    # alongside the `abacus_partial=false` that superseded it, because
    # --set-metadata merges. So the current key decides whenever it is present;
    # only in its absence does the legacy one apply. Falling back
    # unconditionally would make every subsequent close add to a banked figure.
    flag = (meta["abacus_partial"] if "abacus_partial" in meta
            else meta.get(LEGACY_PREFIX + "partial"))
    if not _is_true(flag):
        return {}
    return _normalise_prefix(meta)


def build_metadata(session, issue_id, state, cfg, partial=False, cwd=None, now=None):
    """The ``abacus_*`` metadata for finalising `issue_id`. No side effects on bd."""
    now = now or abacus_time.now_iso()
    baseline = state.get("snapshot")
    claimed_at = state.get("claimed_at")
    carried = carried_partial(issue_id, cwd=cwd)

    meta = {
        "abacus_schema": SCHEMA_VERSION,
        "abacus_session_id": session,
        "abacus_partial": bool(partial),
        # Duration is wall-clock between claim and now, and does not depend on
        # ccusage — a task's elapsed time is knowable even when its cost is not.
        "abacus_duration_min": (int(_number(carried.get("abacus_duration_min")))
                             + abacus_time.minutes_between(claimed_at, now)),
    }

    delta = None
    if isinstance(baseline, dict) and baseline.get("ok"):
        # fresh=True is load-bearing: the baseline was itself written from a
        # cached reading moments ago on a short task, and reading the cache again
        # here would diff a value against itself and record the task as free.
        after = ccusage.snapshot(session, cfg, fresh=True)
        if after.get("ok"):
            delta = ccusage.diff(baseline, after)

    if delta is None:
        meta["abacus_cost_basis"] = BASIS_UNAVAILABLE
    else:
        meta["abacus_cost_basis"] = COST_BASIS
        meta["abacus_cost_usd_estimate"] = round(
            _number(carried.get("abacus_cost_usd_estimate")) + delta["cost"], 4)
        for key, delta_key in _TOKEN_KEYS:
            meta[key] = int(_number(carried.get(key))) + int(delta[delta_key])
        if delta.get("models"):
            meta["abacus_models"] = ",".join(str(m) for m in delta["models"])

    if cfg.get("otel_enrichment") and claimed_at:
        stats = otel.window_stats(session, claimed_at, now,
                                  path=cfg.get("otel_events_path"))
        # A readable log holding no events for this session is not evidence that
        # no tools ran — OTEL may be off, sampling, or lagging. Zero would be
        # indistinguishable from a measurement, so write only real activity.
        if stats and (stats["tool_calls"] or stats["api_calls"]):
            meta["abacus_tool_calls"] = (int(_number(carried.get("abacus_tool_calls")))
                                      + stats["tool_calls"])
            meta["abacus_active_min"] = (int(_number(carried.get("abacus_active_min")))
                                      + stats["active_min"])

    return meta


def finalise(session, issue_id, cfg, partial=False, cwd=None, now=None):
    """Write attribution for `issue_id`. Returns the metadata written."""
    state = state_store.load(session)
    meta = build_metadata(session, issue_id, state, cfg,
                          partial=partial, cwd=cwd, now=now)
    if not beads.set_metadata(issue_id, meta, cwd=cwd):
        # Logged rather than raised: the session must not break because a
        # bookkeeping write failed, and the next boundary will try again.
        import hook_io

        hook_io.log("could not write attribution metadata to %s" % issue_id)
    return meta


def _elapsed_min(issue, now=None):
    """Wall-clock minutes the issue was open, or None if it cannot be worked out.

    ``minutes_between`` returns 0 for an unparsable timestamp, which is the right
    answer for a duration that is genuinely zero and the wrong one here — a 0
    beside a task that ran all afternoon is the same species of lie as a $0.00
    cost. So the timestamps are parsed first and None is returned rather than
    falling through to a number.
    """
    start = issue.get("started_at") or issue.get("created_at")
    end = issue.get("closed_at") or issue.get("updated_at") or now
    if abacus_time.parse_iso(start) is None or abacus_time.parse_iso(end) is None:
        return None
    return abacus_time.minutes_between(start, end)


def backfill_metadata(issue, now=None):
    """Attribution for an already-closed `issue`, from what can still be read.

    This is the repair path — the audit calling after the fact, with no live
    session and no ccusage baseline to diff against. It exists here, beside
    ``build_metadata``, because ``abacus_*`` keys are constructed in exactly one
    module and a second constructor in a script is how two writers drift apart.

    What it may and may not do follows from having no measurement:

    - **It never invents a cost.** With no baseline snapshot there is nothing to
      diff, so the basis is ``unavailable`` and no dollar figure is written. The
      duration is different in kind — bd's own timestamps make it recoverable
      exactly, so it is written whenever they parse.
    - **It never discards one either.** An issue left ``abacus_partial=true``
      already has a real measured figure banked. Finalising it keeps that figure
      and only flips the flag; overwriting it with ``unavailable`` would be a
      repair that destroys the one record of what the work cost.
    - **It says it was backfilled.** ``abacus_backfilled=true`` is what lets a
      later reader tell a reconstruction from a measurement, instead of averaging
      the two together and quietly weakening every figure in the report.
    """
    raw = issue.get("metadata")
    carried = _normalise_prefix(raw) if isinstance(raw, dict) else {}

    meta = {
        "abacus_schema": SCHEMA_VERSION,
        "abacus_partial": False,
        "abacus_backfilled": True,
    }

    banked = (carried.get("abacus_cost_basis") == COST_BASIS
              and "abacus_cost_usd_estimate" in carried)
    if banked:
        meta["abacus_cost_basis"] = COST_BASIS
        meta["abacus_cost_usd_estimate"] = carried["abacus_cost_usd_estimate"]
        for key, _delta_key in _TOKEN_KEYS:
            if key in carried:
                meta[key] = carried[key]
        for key in ("abacus_models", "abacus_tool_calls", "abacus_active_min"):
            if carried.get(key):
                meta[key] = carried[key]
    else:
        meta["abacus_cost_basis"] = BASIS_UNAVAILABLE

    if carried.get("abacus_session_id"):
        meta["abacus_session_id"] = carried["abacus_session_id"]

    duration = carried.get("abacus_duration_min")
    if duration is None:
        duration = _elapsed_min(issue, now=now)
    if duration is not None:
        meta["abacus_duration_min"] = duration

    return meta


def clear_current(session, now=None):
    """Stop attributing to the current task."""
    return state_store.update(session, {
        "current_task": "",
        "current_title": "",
        "snapshot": None,
        "closed_at": now or abacus_time.now_iso(),
    })
