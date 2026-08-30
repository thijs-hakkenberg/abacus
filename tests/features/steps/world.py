"""Shared state and helpers for the executable feature space.

A feature space is only worth keeping if it is executable. The common failure is
a ``features/`` directory of prose kept in sync by hand with a separate pytest
suite, where nothing catches the drift and a feature file can describe behaviour
the software no longer has. Here every
scenario in ``features/`` is bound to a real step definition that drives the real
hook as a subprocess, so a feature file that stops describing the software fails
the suite.

Steps accumulate their setup into a single ``world`` dict and the ``When`` step
applies it. That ordering is what lets a scenario's ``Given`` override one from
the ``Background`` — the later step simply rewrites the same stub file.

Config and environment are *merged* rather than replaced, because
``Harness.write_config`` writes the whole file: two Givens each configuring one
knob would otherwise silently discard each other's.
"""

import json
import time

import pytest

SESSION = "sess-1"

# Matches the plugin's own constants, asserted literally rather than imported:
# these are the names downstream readers depend on, so a test that imported them
# would follow a rename instead of catching it.
COST_BASIS_LOCAL = "ccusage-local-list-rate"
COST_BASIS_UNAVAILABLE = "unavailable"
TOKEN_KEYS = ("abacus_tokens_total", "abacus_tokens_in", "abacus_tokens_out",
              "abacus_tokens_cache_read", "abacus_tokens_cache_write")


def baseline_snapshot(cost, tokens=1000):
    """A state-file snapshot as ``ccusage.snapshot`` would have written it."""
    return {
        "cost": cost,
        "tokens": tokens,
        "input_tokens": 100,
        "output_tokens": 200,
        "cache_read_tokens": 300,
        "cache_creation_tokens": 400,
        "models": ["claude-fable-5"],
        "ok": True,
        "at": time.time(),
    }


def issue(issue_id, status="in_progress", title=None, metadata=None):
    body = {
        "id": issue_id,
        "title": title if title is not None else "tracked work",
        "status": status,
        "updated_at": "2026-08-06T09:00:00Z",
    }
    if metadata is not None:
        body["metadata"] = metadata
    return body


@pytest.fixture
def world(harness):
    """Everything a scenario plants, plus the result once a When has run."""
    return {
        "harness": harness,
        "session": SESSION,
        "cwd": harness.project,
        "config": {},
        "env": {},
        "payload_extra": {},
        "raw_stdin": None,
        "result": None,
    }


# ── planting helpers ────────────────────────────────────────────────────────

def merge_config(world, changes):
    """Deep-merge `changes` into the scenario's config and persist it."""
    def _merge(base, overlay):
        for key, value in overlay.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                _merge(base[key], value)
            else:
                base[key] = value
    _merge(world["config"], changes)
    world["harness"].write_config(world["config"])


def track_task(world, issue_id, cost, tokens=1000):
    """Put the session in the state a claim would have left it in."""
    world["harness"].write_state(world["session"], {
        "session_id": world["session"],
        "current_task": issue_id,
        "current_title": "tracked work",
        "claimed_at": "2026-08-06T09:00:00Z",
        "snapshot": baseline_snapshot(cost, tokens),
        "snapshot_source": "watch-claim",
    })


def plant_ccusage_cache(world, cost, tokens=1000):
    """Seed the ccusage read cache so a cached read would return `cost`.

    Only used by the quick-succession scenario, which exists because a task
    claimed and closed inside the TTL was once served its own claim's reading and
    recorded as free.
    """
    harness = world["harness"]
    snap = baseline_snapshot(cost, tokens)
    snap["at"] = time.time()
    path = harness.state_dir / "ccusage-cache.json"
    path.write_text(json.dumps({world["session"]: snap}))


# ── observing what happened ─────────────────────────────────────────────────

def metadata_writes(world):
    """Every ``bd update --set-metadata`` call, as (issue_id, {key: value})."""
    writes = []
    for call in world["harness"].bd_calls():
        parts = call.split()
        if "--set-metadata" not in parts:
            continue
        # "bd update <id> --set-metadata k=v ..." — values are whitespace-free by
        # construction (beads._metadata_token collapses them), so splitting is safe.
        target = parts[2] if len(parts) > 2 else ""
        pairs = {}
        for i, part in enumerate(parts):
            if part == "--set-metadata" and i + 1 < len(parts):
                key, _, value = parts[i + 1].partition("=")
                pairs[key] = value
        writes.append((target, pairs))
    return writes


def last_write(world):
    writes = metadata_writes(world)
    assert writes, "expected an attribution write, but none happened"
    return writes[-1][1]


def write_for(world, issue_id):
    for target, pairs in metadata_writes(world):
        if target == issue_id:
            return pairs
    raise AssertionError(
        "no attribution written to %s (writes: %s)"
        % (issue_id, [t for t, _ in metadata_writes(world)]))


def injected_context(world):
    """The ``additionalContext`` the hook emitted, or "" when it stayed silent."""
    data = world["result"].json or {}
    return (data.get("hookSpecificOutput") or {}).get("additionalContext", "")


def otel_line(session_id, event_name, timestamp, duration_ms=4000):
    """One OTLP-JSON log line as Claude Code's file exporter writes it.

    The session attribute is ``session.id`` (dotted) rather than the payload's
    ``session_id``; spelling it the payload's way yields a silent zero.
    """
    return json.dumps({"resourceLogs": [{"scopeLogs": [{"logRecords": [{"attributes": [
        {"key": "session.id", "value": {"stringValue": session_id}},
        {"key": "event.name", "value": {"stringValue": event_name}},
        {"key": "event.timestamp", "value": {"stringValue": timestamp}},
        {"key": "duration_ms", "value": {"intValue": duration_ms}},
    ]}]}]}]}) + "\n"
