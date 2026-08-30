#!/usr/bin/env python3
"""Hook plumbing: read the payload, emit the envelope, never crash the session.

Claude Code hands a hook its payload as JSON on stdin and reads its decision
from stdout. Two rules follow from that, and they are the reason this module
exists rather than each script rolling its own:

- **stdout is a protocol, not a log.** A stray ``print`` would corrupt the hook
  envelope, so diagnostics go to stderr only.
- **A hook has no supervisor.** An uncaught exception surfaces to the user as a
  broken tool call in the middle of their work. Every script wraps its body in
  ``guard()``, which converts any unexpected failure into a silent, allowing
  exit — the plugin fails open by construction rather than by discipline.

The one deliberate exception is the PreToolUse gate's *deny*, which is an
intended decision rather than a failure and travels as JSON on stdout with a
zero exit code (adr/002).
"""

import json
import os
import sys


def read_payload():
    """Parse the hook payload from stdin. Always returns a dict."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def session_id(payload):
    return str(payload.get("session_id") or payload.get("sessionId") or "unknown")


def payload_cwd(payload):
    """The directory the tool call actually relates to.

    Prefer the payload's ``cwd`` over ``os.getcwd()``: a subagent's hook can be
    invoked from a different working directory than the one the edit targets, and
    a workspace lookup against the wrong directory silently mis-decides.
    """
    for key in ("cwd", "project_dir", "projectDir"):
        value = payload.get(key)
        if value:
            expanded = os.path.expanduser(str(value))
            if os.path.isdir(expanded):
                return expanded
    return os.getcwd()


def log(message):
    """Diagnostics to stderr, where they cannot corrupt the stdout protocol."""
    try:
        sys.stderr.write("[abacus] %s\n" % message)
        sys.stderr.flush()
    except Exception:
        pass


def emit(obj):
    """Write a hook envelope to stdout."""
    try:
        sys.stdout.write(json.dumps(obj))
        sys.stdout.flush()
    except Exception:
        pass


def deny(reason, event="PreToolUse"):
    """Emit a deny decision. Exit code stays 0 — the JSON *is* the decision."""
    emit({"hookSpecificOutput": {
        "hookEventName": event,
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }})


def additional_context(text, event="SessionStart"):
    """Emit context for the model to see (SessionStart / UserPromptSubmit)."""
    if not text:
        return
    emit({"hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": text,
    }})


def guard(main_fn):
    """Run `main_fn`, swallowing every failure into a clean exit 0.

    ``ABACUS_DEBUG=1`` re-raises instead, so tests and development can see the real
    traceback rather than a silently-allowed edit.
    """
    try:
        result = main_fn()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 — a hook must not propagate
        if os.environ.get("ABACUS_DEBUG"):
            raise
        log("internal error (failing open): %s: %s" % (type(exc).__name__, exc))
        sys.exit(0)
    sys.exit(int(result or 0))
