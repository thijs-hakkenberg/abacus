"""RED: ccusage adapter — snapshots, pinning, timeout, caching, degradation.

The adapter is the only thing that knows how to call ccusage. Every failure mode
here must degrade to a zeroed snapshot rather than raise, because its callers are
hooks with no supervisor.
"""

import json
import os
import subprocess
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB = os.path.join(REPO_ROOT, "hooks", "lib")


def _run_probe(harness, code, **env_overrides):
    """Exercise the adapter in a subprocess so the PATH stubs apply."""
    prog = "import sys; sys.path.insert(0, %r)\n%s" % (LIB, code)
    proc = subprocess.run(
        [sys.executable, "-c", prog],
        capture_output=True, text=True, cwd=str(harness.project),
        env=harness.env(**env_overrides), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_snapshot_returns_cost_and_tokens_for_the_session(harness):
    harness.set_ccusage_session("sess-1", cost=12.5, tokens=1000)
    snap = _run_probe(harness, """
import ccusage, json
print(json.dumps(ccusage.snapshot("sess-1")))
""")
    assert snap["cost"] == 12.5
    assert snap["tokens"] == 1000
    assert snap["ok"] is True


def test_snapshot_uses_the_pinned_ccusage_version(harness):
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=1)
    _run_probe(harness, """
import ccusage, json
print(json.dumps(ccusage.snapshot("sess-1")))
""")
    call = " ".join(harness.npx_calls())
    assert "ccusage@" in call
    assert "latest" not in call


def test_snapshot_of_unknown_session_is_zeroed_but_ok(harness):
    """A session with no usage yet is a legitimate zero, not a failure."""
    harness.set_ccusage_session("other-session", cost=9.0, tokens=900)
    snap = _run_probe(harness, """
import ccusage, json
print(json.dumps(ccusage.snapshot("sess-1")))
""")
    assert snap["cost"] == 0.0
    assert snap["tokens"] == 0
    assert snap["ok"] is True


def test_snapshot_survives_null_ccusage_output(harness):
    harness.set_ccusage_raw("null")
    snap = _run_probe(harness, """
import ccusage, json
print(json.dumps(ccusage.snapshot("sess-1")))
""")
    assert snap["cost"] == 0.0
    assert snap["ok"] is False


def test_snapshot_survives_unparsable_output(harness):
    harness.set_ccusage_raw("not json at all")
    snap = _run_probe(harness, """
import ccusage, json
print(json.dumps(ccusage.snapshot("sess-1")))
""")
    assert snap["cost"] == 0.0
    assert snap["ok"] is False


def test_snapshot_survives_nonzero_exit(harness):
    harness.set_ccusage_raw("", rc=1)
    snap = _run_probe(harness, """
import ccusage, json
print(json.dumps(ccusage.snapshot("sess-1")))
""")
    assert snap["ok"] is False


def test_snapshot_times_out_rather_than_hanging(harness):
    """A wedged npx must not consume the hook's whole timeout budget."""
    harness.set_ccusage_hang(10)
    started = time.time()
    snap = _run_probe(harness, """
import ccusage, json
print(json.dumps(ccusage.snapshot("sess-1")))
""", ABACUS_CCUSAGE_TIMEOUT_S=2)
    elapsed = time.time() - started
    assert snap["ok"] is False
    assert elapsed < 8, "adapter did not enforce its own timeout (took %.1fs)" % elapsed


def test_snapshot_is_cached_so_repeat_calls_spawn_one_npx(harness):
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=300)
    _run_probe(harness, """
import ccusage, json
a = ccusage.snapshot("sess-1")
b = ccusage.snapshot("sess-1")
print(json.dumps([a, b]))
""")
    assert len(harness.npx_calls()) == 1, "second call should have hit the cache"


def test_cache_is_bypassed_when_ttl_is_zero(harness):
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=300)
    _run_probe(harness, """
import ccusage, json
a = ccusage.snapshot("sess-1")
b = ccusage.snapshot("sess-1")
print(json.dumps([a, b]))
""", ABACUS_CACHE_TTL_S=0)
    assert len(harness.npx_calls()) == 2


def test_a_fresh_snapshot_ignores_the_cache(harness):
    """The closing read of a task must never be served from the cache the claim
    populated. A task claimed and closed inside the TTL — the ordinary shape of a
    small fix — would otherwise diff to exactly zero and be recorded as having
    cost nothing, with a basis that presents it as a real measurement."""
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=300)
    out = _run_probe(harness, """
import ccusage, json
a = ccusage.snapshot("sess-1")
b = ccusage.snapshot("sess-1", fresh=True)
print(json.dumps([a, b]))
""")
    assert len(harness.npx_calls()) == 2, "fresh=True must re-read"
    assert out[1]["ok"] is True


def test_a_fresh_snapshot_still_writes_through_to_the_cache(harness):
    """So that a claim immediately following a close shares the close's reading:
    the next task then starts exactly where the previous one ended, with no gap
    that belongs to neither and no overlap charged to both."""
    harness.set_ccusage_session("sess-1", cost=3.0, tokens=300)
    _run_probe(harness, """
import ccusage, json
a = ccusage.snapshot("sess-1", fresh=True)
b = ccusage.snapshot("sess-1")
print(json.dumps([a, b]))
""")
    assert len(harness.npx_calls()) == 1, "the second, non-fresh call should hit the cache"


def test_failed_snapshots_are_not_cached(harness):
    """Caching a failure would freeze a transient npx blip in for the whole TTL."""
    harness.set_ccusage_raw("", rc=1)
    _run_probe(harness, """
import ccusage, json
a = ccusage.snapshot("sess-1")
b = ccusage.snapshot("sess-1")
print(json.dumps([a, b]))
""")
    assert len(harness.npx_calls()) == 2


def test_diff_computes_the_delta_between_two_snapshots(harness):
    out = _run_probe(harness, """
import ccusage, json
before = {"cost": 1.0, "tokens": 100, "input_tokens": 10, "output_tokens": 20,
          "cache_read_tokens": 30, "cache_creation_tokens": 40, "ok": True}
after  = {"cost": 3.5, "tokens": 450, "input_tokens": 15, "output_tokens": 70,
          "cache_read_tokens": 80, "cache_creation_tokens": 90, "ok": True}
print(json.dumps(ccusage.diff(before, after)))
""")
    assert out["cost"] == pytest.approx(2.5)
    assert out["tokens"] == 350
    assert out["input_tokens"] == 5
    assert out["output_tokens"] == 50
    assert out["cache_read_tokens"] == 50
    assert out["cache_creation_tokens"] == 50


def test_diff_never_returns_a_negative_delta(harness):
    """Snapshots come from an append-only log, so a negative delta means a
    reset or a session-id reuse — clamp rather than write nonsense metadata."""
    out = _run_probe(harness, """
import ccusage, json
print(json.dumps(ccusage.diff({"cost": 5.0, "tokens": 500, "ok": True},
                              {"cost": 1.0, "tokens": 100, "ok": True})))
""")
    assert out["cost"] == 0.0
    assert out["tokens"] == 0


def test_diff_marks_result_unreliable_when_either_snapshot_failed(harness):
    out = _run_probe(harness, """
import ccusage, json
print(json.dumps(ccusage.diff({"cost": 0.0, "tokens": 0, "ok": False},
                              {"cost": 4.0, "tokens": 400, "ok": True})))
""")
    assert out["ok"] is False


def test_offline_flag_is_passed_through_when_configured(harness):
    harness.set_ccusage_session("sess-1", cost=1.0, tokens=1)
    harness.write_config({"ccusage_offline": True})
    _run_probe(harness, """
import ccusage, json
print(json.dumps(ccusage.snapshot("sess-1")))
""")
    assert "--offline" in " ".join(harness.npx_calls())


def test_missing_npx_degrades_instead_of_raising(harness):
    os.unlink(os.path.join(str(harness.stub_dir), "npx"))
    snap = _run_probe(harness, """
import ccusage, json
print(json.dumps(ccusage.snapshot("sess-1")))
""", PATH=str(harness.stub_dir))
    assert snap["ok"] is False
    assert snap["cost"] == 0.0
