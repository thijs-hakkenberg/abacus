"""RED: OTEL enrichment — tool counts and active time from the events log.

OTEL is strictly supplementary (adr/003): it adds *duration* texture that ccusage
does not carry, and every failure path must silently omit keys rather than raise.
Attribute names verified against a real collector log on 2026-08-05 — note
``session.id`` is dotted, not ``session_id``.
"""

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB = os.path.join(REPO_ROOT, "hooks", "lib")


def _event(session_id, name, ts, **attrs):
    base = {"session.id": session_id, "event.name": name, "event.timestamp": ts}
    base.update({k.replace("__", "."): v for k, v in attrs.items()})
    return {"resourceLogs": [{"scopeLogs": [{"logRecords": [{
        "body": {"stringValue": "claude_code." + name},
        "attributes": [{"key": k, "value": {"stringValue": str(v)}} for k, v in base.items()],
    }]}]}]}


def _write_log(path, events):
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def _probe(harness, code, **env_overrides):
    prog = "import sys; sys.path.insert(0, %r)\n%s" % (LIB, code)
    proc = subprocess.run(
        [sys.executable, "-c", prog], capture_output=True, text=True,
        cwd=str(harness.project), env=harness.env(**env_overrides), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture
def log(harness):
    path = harness.tmp / "events.jsonl"
    path.write_text("")
    return path


def test_counts_tool_results_within_the_window(harness, log):
    _write_log(str(log), [
        _event("s1", "tool_result", "2026-08-05T10:00:05.000Z", tool_name="Edit", duration_ms="100"),
        _event("s1", "tool_result", "2026-08-05T10:00:10.000Z", tool_name="Bash", duration_ms="200"),
    ])
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r)))
""" % str(log))
    assert out["tool_calls"] == 2
    assert out["tool_ms"] == 300


def test_events_outside_the_window_are_excluded(harness, log):
    _write_log(str(log), [
        _event("s1", "tool_result", "2026-08-05T09:00:00.000Z", tool_name="Edit", duration_ms="100"),
        _event("s1", "tool_result", "2026-08-05T10:00:05.000Z", tool_name="Edit", duration_ms="100"),
        _event("s1", "tool_result", "2026-08-05T11:00:00.000Z", tool_name="Edit", duration_ms="100"),
    ])
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r)))
""" % str(log))
    assert out["tool_calls"] == 1


def test_other_sessions_are_excluded(harness, log):
    _write_log(str(log), [
        _event("s1", "tool_result", "2026-08-05T10:00:05.000Z", tool_name="Edit", duration_ms="100"),
        _event("other", "tool_result", "2026-08-05T10:00:06.000Z", tool_name="Edit", duration_ms="999"),
    ])
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r)))
""" % str(log))
    assert out["tool_calls"] == 1
    assert out["tool_ms"] == 100


def test_models_are_collected_from_api_requests(harness, log):
    _write_log(str(log), [
        _event("s1", "api_request", "2026-08-05T10:00:05.000Z", model="claude-fable-5", duration_ms="500"),
        _event("s1", "api_request", "2026-08-05T10:00:06.000Z", model="claude-opus-5", duration_ms="500"),
        _event("s1", "api_request", "2026-08-05T10:00:07.000Z", model="claude-fable-5", duration_ms="500"),
    ])
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r)))
""" % str(log))
    assert sorted(out["models"]) == ["claude-fable-5", "claude-opus-5"]
    assert out["api_calls"] == 3


def test_active_minutes_are_derived_from_summed_durations(harness, log):
    _write_log(str(log), [
        _event("s1", "tool_result", "2026-08-05T10:00:05.000Z", tool_name="Bash", duration_ms="60000"),
        _event("s1", "api_request", "2026-08-05T10:00:06.000Z", model="m", duration_ms="60000"),
    ])
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r)))
""" % str(log))
    assert out["active_min"] == 2


def test_missing_log_file_returns_empty_stats_not_an_error(harness):
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", "/nope/absent.jsonl")))
""")
    assert out == {}


def test_corrupt_lines_are_skipped_without_failing(harness, log):
    with open(str(log), "w", encoding="utf-8") as f:
        f.write("{ this is not json\n")
        f.write(json.dumps(_event("s1", "tool_result", "2026-08-05T10:00:05.000Z",
                                  tool_name="Edit", duration_ms="100")) + "\n")
        f.write("\n")
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r)))
""" % str(log))
    assert out["tool_calls"] == 1


def test_only_the_tail_of_a_huge_log_is_read(harness, log):
    """The real log is tens of MB and grows without bound; reading it whole would
    blow the hook's time budget."""
    filler = _event("s1", "tool_result", "2026-08-05T09:00:00.000Z", tool_name="Old", duration_ms="1")
    with open(str(log), "w", encoding="utf-8") as f:
        for _ in range(4000):
            f.write(json.dumps(filler) + "\n")
        f.write(json.dumps(_event("s1", "tool_result", "2026-08-05T10:00:05.000Z",
                                  tool_name="Edit", duration_ms="100")) + "\n")
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r, tail_bytes=20000)))
""" % str(log))
    assert out["tool_calls"] == 1
    assert out["bytes_scanned"] <= 21000


def test_open_ended_window_accepts_a_missing_end(harness, log):
    _write_log(str(log), [
        _event("s1", "tool_result", "2026-08-05T10:00:05.000Z", tool_name="Edit", duration_ms="100"),
    ])
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", None, %r)))
""" % str(log))
    assert out["tool_calls"] == 1


def test_unparsable_window_bounds_yield_empty_stats(harness, log):
    _write_log(str(log), [
        _event("s1", "tool_result", "2026-08-05T10:00:05.000Z", tool_name="Edit", duration_ms="100"),
    ])
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "not-a-timestamp", None, %r)))
""" % str(log))
    assert out == {}
