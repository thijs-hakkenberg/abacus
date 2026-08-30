#!/usr/bin/env python3
"""Per-session state for cost attribution. Pure stdlib, Python 3.9-compatible.

This is deliberately NOT a task database — the beads DB is the store of record
for what work exists and what is in progress (adr/001). All this holds is the
bookkeeping needed to turn two ccusage readings into one task's cost:

    {"session_id", "current_task", "claimed_at", "snapshot": {...},
     "tool_calls", "beads_plugin_present", ...}

One JSON file per session under ``$ABACUS_STATE_DIR`` (default
``~/.claude/abacus/``), because sessions are independent and a file
per session means two concurrent sessions never contend for the same write.

Writes are atomic (temp file + ``os.replace``): a PostToolUse watcher and the
next PreToolUse gate can genuinely overlap, and a half-written state file would
make the gate read garbage. Reads are total — a corrupt or missing file reads
as ``{}`` rather than raising, since a hook has no supervisor to report an
exception to and must never break the user's session over its own bookkeeping.
"""

import json
import os
import re
import tempfile

DEFAULT_STATE_DIR = os.path.join("~", ".claude", "abacus")
CONFIG_BASENAME = "config.json"

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def state_dir():
    """Resolve the state directory, creating it if needed."""
    raw = os.environ.get("ABACUS_STATE_DIR") or DEFAULT_STATE_DIR
    path = os.path.abspath(os.path.expanduser(raw))
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
    except OSError:
        pass
    return path


def _safe_session_id(session_id):
    """Collapse anything path-like in a session id.

    session_id arrives from the hook payload. Claude Code sends a UUID, but this
    module must not depend on that for filesystem safety: a value like
    ``../../.ssh/authorized_keys`` has to land inside the state dir as an inert
    filename, not escape it. Dots are collapsed too, not just separators — the
    resulting name then cannot contain ``..`` at all, which is a property that
    can be asserted directly rather than reasoned about.
    """
    sid = _UNSAFE.sub("_", str(session_id or "unknown"))
    return sid[:120] or "unknown"


def path_for(session_id):
    return os.path.join(state_dir(), "session-{}.json".format(_safe_session_id(session_id)))


def load(session_id):
    """Return the session's state dict; ``{}`` if absent, unreadable or corrupt."""
    try:
        with open(path_for(session_id), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save(session_id, state):
    """Atomically write the session's state. Returns True on success."""
    target = path_for(session_id)
    directory = os.path.dirname(target)
    try:
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".session-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)  # atomic within a filesystem
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        return False
    return True


def update(session_id, changes):
    """Shallow-merge `changes` into the stored state and persist it."""
    state = load(session_id)
    state.update(changes)
    save(session_id, state)
    return state


def prune(max_age_days=14):
    """Delete session state files older than `max_age_days`.

    Only ``session-*.json`` is eligible — config.json lives in the same
    directory and is user-authored, not disposable bookkeeping.
    """
    import time

    cutoff = time.time() - (max_age_days * 86400)
    removed = []
    directory = state_dir()
    try:
        names = os.listdir(directory)
    except OSError:
        return removed
    for name in names:
        if not (name.startswith("session-") and name.endswith(".json")):
            continue
        full = os.path.join(directory, name)
        try:
            if os.path.getmtime(full) < cutoff:
                os.unlink(full)
                removed.append(full)
        except OSError:
            continue
    return removed
