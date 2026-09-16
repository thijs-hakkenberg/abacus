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


def test_a_top_level_json_value_that_is_not_a_document_is_skipped(harness, log):
    """A tail read can land mid-array from an unrelated log shape; ignore it rather
    than crash the whole scan over one malformed line."""
    with open(str(log), "w", encoding="utf-8") as f:
        f.write(json.dumps([1, 2, 3]) + "\n")
        f.write(json.dumps(_event("s1", "tool_result", "2026-08-05T10:00:05.000Z",
                                  tool_name="Edit", duration_ms="100")) + "\n")
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r)))
""" % str(log))
    assert out["tool_calls"] == 1


def test_an_attribute_value_that_is_not_a_dict_is_skipped(harness, log):
    """OTLP wraps scalars as {stringValue|intValue|...: x}; a bare scalar value
    is malformed and that one attribute is dropped rather than raising."""
    doc = {"resourceLogs": [{"scopeLogs": [{"logRecords": [{
        "attributes": [
            {"key": "session.id", "value": "s1"},
            {"key": "event.name", "value": {"stringValue": "tool_result"}},
            {"key": "event.timestamp", "value": {"stringValue": "2026-08-05T10:00:05.000Z"}},
            {"key": "duration_ms", "value": {"stringValue": "100"}},
        ],
    }]}]}]}
    with open(str(log), "w", encoding="utf-8") as f:
        f.write(json.dumps(doc) + "\n")
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r)))
""" % str(log))
    # session.id never resolved (the malformed attribute was dropped), so this
    # record never matches session "s1" -- it counts toward nothing.
    assert out["tool_calls"] == 0


def test_an_attribute_value_dict_with_no_scalar_inside_sets_nothing(harness, log):
    """An OTLP value wrapper with no key at all (`{}`) yields no candidate to
    unwrap; the attribute is silently absent rather than set to None."""
    doc = {"resourceLogs": [{"scopeLogs": [{"logRecords": [{
        "attributes": [
            {"key": "session.id", "value": {"stringValue": "s1"}},
            {"key": "event.name", "value": {}},
            {"key": "event.timestamp", "value": {"stringValue": "2026-08-05T10:00:05.000Z"}},
        ],
    }]}]}]}
    with open(str(log), "w", encoding="utf-8") as f:
        f.write(json.dumps(doc) + "\n")
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r)))
""" % str(log))
    # event.name never resolved, so neither the tool_result nor api_request branch fires.
    assert out["tool_calls"] == 0
    assert out["api_calls"] == 0


def test_a_record_with_no_attributes_is_not_yielded(harness, log):
    empty_record = {"attributes": []}
    real_record = _event("s1", "tool_result", "2026-08-05T10:00:05.000Z",
                          tool_name="Edit", duration_ms="100")["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    doc = {"resourceLogs": [{"scopeLogs": [{"logRecords": [empty_record, real_record]}]}]}
    with open(str(log), "w", encoding="utf-8") as f:
        f.write(json.dumps(doc) + "\n")
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r)))
""" % str(log))
    assert out["tool_calls"] == 1


def test_a_non_numeric_duration_counts_as_zero_rather_than_erroring(harness, log):
    _write_log(str(log), [
        _event("s1", "tool_result", "2026-08-05T10:00:05.000Z", tool_name="Edit",
               duration_ms="not-a-number"),
    ])
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r)))
""" % str(log))
    assert out["tool_calls"] == 1
    assert out["tool_ms"] == 0


def test_an_event_that_is_neither_tool_result_nor_api_request_is_ignored(harness, log):
    _write_log(str(log), [
        _event("s1", "user_prompt_submit", "2026-08-05T10:00:05.000Z"),
    ])
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r)))
""" % str(log))
    assert out["tool_calls"] == 0
    assert out["api_calls"] == 0


def test_an_api_request_without_a_model_attribute_contributes_no_model(harness, log):
    _write_log(str(log), [
        _event("s1", "api_request", "2026-08-05T10:00:05.000Z", duration_ms="500"),
    ])
    out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r)))
""" % str(log))
    assert out["api_calls"] == 1
    assert out["models"] == []


def test_a_file_that_turns_unreadable_mid_scan_returns_empty_stats(harness, log):
    """`os.path.isfile` at the top can pass and the open() a few lines later can
    still fail -- e.g. a permission change lands between the two. Either way this
    module's contract is best-effort (adr/003): swallow it, return {}."""
    _write_log(str(log), [
        _event("s1", "tool_result", "2026-08-05T10:00:05.000Z", tool_name="Edit", duration_ms="100"),
    ])
    os.chmod(str(log), 0o000)
    try:
        out = _probe(harness, """
import otel, json
print(json.dumps(otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z", %r)))
""" % str(log))
    finally:
        os.chmod(str(log), 0o644)
    assert out == {}


def test_path_none_and_a_broken_config_lookup_is_swallowed(lib_path, monkeypatch):
    """`window_stats(path=None)` reads `otel_events_path` from config. A config
    module that cannot even be read must not raise past this best-effort reader."""
    import sys

    import otel

    class _BrokenConfig(object):
        def load_config(self):
            raise RuntimeError("config store is unreadable")

    monkeypatch.setitem(sys.modules, "abacus_config", _BrokenConfig())
    out = otel.window_stats("s1", "2026-08-05T10:00:00.000Z")
    assert out == {}


def test_path_none_falls_back_to_the_configured_otel_events_path(lib_path, harness, monkeypatch, log):
    monkeypatch.setenv("ABACUS_STATE_DIR", str(harness.state_dir))
    harness.write_config({"otel_events_path": str(log)})
    _write_log(str(log), [
        _event("s1", "tool_result", "2026-08-05T10:00:05.000Z", tool_name="Edit", duration_ms="100"),
    ])
    import otel

    out = otel.window_stats("s1", "2026-08-05T10:00:00.000Z", "2026-08-05T10:01:00.000Z")
    assert out["tool_calls"] == 1
