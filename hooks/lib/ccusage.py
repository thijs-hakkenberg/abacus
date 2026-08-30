#!/usr/bin/env python3
"""ccusage adapter — the one place that knows how to price a session.

ccusage is the cost/token source of truth (adr/003). It offers no library API
and no MCP server, so the only interface is ``npx ccusage claude session --json``.

This module exposes exactly two operations, which is all per-task attribution
needs:

    snapshot(session_id) -> cumulative cost/tokens for that session, right now
    diff(before, after)  -> what one task consumed between two snapshots

Why snapshot-diff and not per-message summing: ccusage's session report already
walks the main transcript *and* the ``subagents/`` transcripts underneath it,
deduplicating on ``(message.id, requestId)``. Verified against a real 5-subagent
session — 251 raw usage lines collapsed to 88 unique pairs (69 main + 19
subagent, zero overlap) — so a session total already includes fan-out work that
per-message accounting in a hook would have to re-implement and would get wrong.

Adapted from an earlier ccusage adapter the author wrote for session-level
reporting, with two changes that a hot-path caller requires and the original did
not need:

1. **An explicit timeout.** The original had none; an npx cold start or a wedged
   network resolve would hang until the hook's own timeout killed it, losing the
   attribution write that was supposed to follow.
2. **A short-TTL cache.** Task boundaries can fire in quick succession (close
   one task, claim the next), and each uncached call is a fresh npx spawn.

Both differences are deliberate: this module degrades to a zeroed snapshot with
``ok=False`` on *every* failure path rather than raising, because its callers are
hooks that must never break a user's session over cost bookkeeping. That is the
opposite of the original's ``raise SystemExit`` behaviour, which was correct for
a user-initiated MCP tool call and is wrong here.
"""

import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import abacus_config  # noqa: E402
import state_store  # noqa: E402

ZERO = {
    "cost": 0.0,
    "tokens": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "models": [],
    "ok": False,
}

_MEMO = {}


def _zero(ok=False):
    snap = dict(ZERO)
    snap["ok"] = ok
    snap["at"] = time.time()
    return snap


def _cache_path(session_id):
    return os.path.join(state_store.state_dir(), "ccusage-cache.json")


def _read_cache(session_id, ttl):
    if ttl <= 0:
        return None
    entry = _MEMO.get(session_id)
    if entry and (time.time() - entry.get("at", 0)) <= ttl:
        return entry
    try:
        with open(_cache_path(session_id), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    entry = (data or {}).get(session_id)
    if not isinstance(entry, dict):
        return None
    if (time.time() - float(entry.get("at") or 0)) > ttl:
        return None
    return entry


def _write_cache(session_id, snap):
    _MEMO[session_id] = snap
    path = _cache_path(session_id)
    try:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data[session_id] = snap
        # Keep the file small — stale entries are dead weight, not history.
        cutoff = time.time() - 3600
        data = {k: v for k, v in data.items()
                if isinstance(v, dict) and float(v.get("at") or 0) > cutoff}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        pass  # cache is an optimisation; losing it must not fail the call


def _int_env(name, default):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _run_session_report(cfg):
    """Return parsed ccusage session JSON, or None on any failure."""
    npx = shutil.which("npx")
    if not npx:
        return None
    timeout = _int_env("ABACUS_CCUSAGE_TIMEOUT_S", int(cfg.get("ccusage_timeout_s") or 25))
    override = os.environ.get("ABACUS_CCUSAGE_CMD")
    if override:
        cmd = override.split() + ["claude", "session", "--json"]
    else:
        cmd = [npx, "-y", cfg["ccusage_version"], "claude", "session", "--json"]
    cmd += ["--mode", cfg.get("ccusage_mode") or "calculate"]
    if cfg.get("ccusage_offline"):
        cmd.append("--offline")
    try:
        # stdin=DEVNULL is load-bearing, not defensive: a hook's stdin is the
        # payload pipe from Claude Code, and a child that inherits it can block
        # forever waiting on a pipe nobody will close. An earlier tool of the
        # author's hit exactly this against MCP stdio and timed out at 90s.
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None


def snapshot(session_id, cfg=None, fresh=False):
    """Cumulative cost/tokens for `session_id` as of now.

    Returns a dict that always has the ZERO keys plus ``ok``. ``ok=False`` means
    "could not read ccusage", which is different from a genuine zero: a brand-new
    session that ccusage has not seen yet returns ``ok=True`` with zeros, and the
    caller can safely diff against it.

    ``fresh=True`` bypasses the read cache, and the *closing* read of a task must
    always use it. A task claimed and closed inside the TTL — which is the
    ordinary shape of a small fix — would otherwise be served the very reading its
    own claim populated, diff to exactly zero, and be recorded as free with a
    basis that presents it as a measurement. It still writes through, so a claim
    immediately following a close shares the close's reading and the next task
    starts exactly where the previous one ended.
    """
    cfg = cfg or abacus_config.load_config()
    ttl = _int_env("ABACUS_CACHE_TTL_S", int(cfg.get("cache_ttl_s") or 30))

    if not fresh:
        cached = _read_cache(session_id, ttl)
        if cached is not None:
            return cached

    data = _run_session_report(cfg)
    if not isinstance(data, dict):
        # Note: not cached. A transient npx failure must not freeze a bogus
        # zero in for the whole TTL and mis-attribute the next task to $0.
        return _zero(ok=False)

    snap = _zero(ok=True)
    for row in data.get("sessions") or []:
        if row.get("sessionId") != session_id:
            continue
        snap.update({
            "cost": float(row.get("totalCost") or 0.0),
            "tokens": int(row.get("totalTokens") or 0),
            "input_tokens": int(row.get("inputTokens") or 0),
            "output_tokens": int(row.get("outputTokens") or 0),
            "cache_read_tokens": int(row.get("cacheReadTokens") or 0),
            "cache_creation_tokens": int(row.get("cacheCreationTokens") or 0),
            "models": row.get("modelsUsed") or [],
        })
        break
    snap["at"] = time.time()
    _write_cache(session_id, snap)
    return snap


_DELTA_KEYS = (
    "cost",
    "tokens",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
)


def diff(before, after):
    """What was consumed between two snapshots.

    Deltas are clamped at zero. Snapshots come from an append-only transcript,
    so a cumulative total can only grow; a negative delta means the baseline was
    not what we thought (session-id reuse, a cleared transcript, a failed
    reading) and clamping is honest where a negative dollar figure on a task
    would not be. ``ok`` is False if either input was unreliable, so callers can
    choose to record tokens-only or skip the write.
    """
    before = before or {}
    after = after or {}
    out = {}
    for key in _DELTA_KEYS:
        try:
            delta = float(after.get(key) or 0) - float(before.get(key) or 0)
        except (TypeError, ValueError):
            delta = 0.0
        delta = max(0.0, delta)
        out[key] = delta if key == "cost" else int(delta)
    out["models"] = after.get("models") or before.get("models") or []
    out["ok"] = bool(before.get("ok")) and bool(after.get("ok"))
    return out


def main():
    """CLI smoke test: `ccusage.py <session-id>`."""
    if len(sys.argv) < 2:
        print("usage: ccusage.py <session-id>", file=sys.stderr)
        return 2
    print(json.dumps(snapshot(sys.argv[1]), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
