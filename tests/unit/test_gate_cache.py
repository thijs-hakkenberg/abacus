"""RED: the gate's asymmetric allow-cache.

Measured on bd 1.1.2 (2026-08-05): `bd list --status in_progress --json` against
a real embedded-Dolt workspace costs ~0.45s, which dominates the gate's ~0.53s
total. Claude edits in bursts, so the same query runs repeatedly within seconds.

The cache is deliberately ONE-SIDED, and the asymmetry is the whole point:

- An **allow** may be cached briefly. Going stale means an edit slips through for
  a second or two after the last task was closed — a trivial attribution smear.
- A **deny** is NEVER cached. Going stale would mean refusing an edit *after* the
  user has correctly claimed a task, i.e. the gate telling them to do something
  they just did. That is the failure that would make the plugin infuriating, so
  it is designed out rather than tuned.
"""

import time

import pytest

from conftest import pre_tool_payload

CLAIMED = [{"id": "bd-a1b2", "title": "t", "status": "in_progress",
            "updated_at": "2026-08-05T21:34:12Z"}]


def _gate(harness, **kw):
    return harness.run_hook("gate_edits.py", pre_tool_payload(), **kw)


def test_repeat_allow_within_ttl_skips_the_bd_query(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", CLAIMED)
    harness.write_state("sess-1", {"current_task": "bd-a1b2",
                                   "snapshot": {"cost": 1.0, "tokens": 1, "ok": True}})
    _gate(harness, ABACUS_GATE_CACHE_TTL_S=5)
    first = len(harness.bd_calls())
    _gate(harness, ABACUS_GATE_CACHE_TTL_S=5)
    assert len(harness.bd_calls()) == first, "second allow should have been served from cache"


def test_cached_allow_still_allows(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", CLAIMED)
    harness.write_state("sess-1", {"current_task": "bd-a1b2",
                                   "snapshot": {"cost": 1.0, "tokens": 1, "ok": True}})
    _gate(harness, ABACUS_GATE_CACHE_TTL_S=5)
    res = _gate(harness, ABACUS_GATE_CACHE_TTL_S=5)
    assert res.permission_decision != "deny"


def test_a_deny_is_never_served_from_cache(harness):
    """Every deny must re-ask bd, so a just-claimed task is seen immediately."""
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    _gate(harness, ABACUS_GATE_CACHE_TTL_S=5)
    first = len(harness.bd_calls())
    assert first > 0
    _gate(harness, ABACUS_GATE_CACHE_TTL_S=5)
    assert len(harness.bd_calls()) > first, "deny path must always re-query bd"


def test_claiming_a_task_takes_effect_immediately_despite_a_prior_deny(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", [])
    assert _gate(harness, ABACUS_GATE_CACHE_TTL_S=30).permission_decision == "deny"
    # The user claims a task; the very next edit must be allowed.
    harness.set_bd_json("list", CLAIMED)
    assert _gate(harness, ABACUS_GATE_CACHE_TTL_S=30).permission_decision != "deny"


def test_cache_expires_after_its_ttl(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", CLAIMED)
    harness.write_state("sess-1", {"current_task": "bd-a1b2",
                                   "snapshot": {"cost": 1.0, "tokens": 1, "ok": True}})
    _gate(harness, ABACUS_GATE_CACHE_TTL_S=1)
    first = len(harness.bd_calls())
    time.sleep(1.2)
    _gate(harness, ABACUS_GATE_CACHE_TTL_S=1)
    assert len(harness.bd_calls()) > first


def test_cache_is_scoped_per_session(harness):
    """Two sessions can be in different states; one must not answer for the other."""
    harness.make_beads_project()
    harness.set_bd_json("list", CLAIMED)
    harness.write_state("sess-1", {"current_task": "bd-a1b2",
                                   "snapshot": {"cost": 1.0, "tokens": 1, "ok": True}})
    harness.run_hook("gate_edits.py", pre_tool_payload(session_id="sess-1"),
                     ABACUS_GATE_CACHE_TTL_S=30)
    first = len(harness.bd_calls())
    harness.run_hook("gate_edits.py", pre_tool_payload(session_id="sess-OTHER"),
                     ABACUS_GATE_CACHE_TTL_S=30)
    assert len(harness.bd_calls()) > first


def test_cache_is_scoped_per_workspace(harness):
    """A claim in repo A must not open the gate for an edit in repo B."""
    harness.make_beads_project()
    harness.set_bd_json("list", CLAIMED)
    harness.write_state("sess-1", {"current_task": "bd-a1b2",
                                   "snapshot": {"cost": 1.0, "tokens": 1, "ok": True}})
    _gate(harness, ABACUS_GATE_CACHE_TTL_S=30)
    first = len(harness.bd_calls())

    other = harness.tmp / "other-repo"
    (other / ".beads").mkdir(parents=True)
    harness.run_hook("gate_edits.py", pre_tool_payload(cwd=other), cwd=other,
                     ABACUS_GATE_CACHE_TTL_S=30)
    assert len(harness.bd_calls()) > first


def test_cache_disabled_by_zero_ttl(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", CLAIMED)
    harness.write_state("sess-1", {"current_task": "bd-a1b2",
                                   "snapshot": {"cost": 1.0, "tokens": 1, "ok": True}})
    _gate(harness, ABACUS_GATE_CACHE_TTL_S=0)
    first = len(harness.bd_calls())
    _gate(harness, ABACUS_GATE_CACHE_TTL_S=0)
    assert len(harness.bd_calls()) > first


def test_closing_the_tracked_task_is_noticed_once_the_ttl_lapses(harness):
    harness.make_beads_project()
    harness.set_bd_json("list", CLAIMED)
    harness.write_state("sess-1", {"current_task": "bd-a1b2",
                                   "snapshot": {"cost": 1.0, "tokens": 1, "ok": True}})
    _gate(harness, ABACUS_GATE_CACHE_TTL_S=1)
    harness.set_bd_json("list", [])
    time.sleep(1.2)
    assert _gate(harness, ABACUS_GATE_CACHE_TTL_S=1).permission_decision == "deny"


# ── the lazy-snapshot repair path must not outlive the hook's own timeout ────

def test_the_lazy_snapshot_cannot_outlive_the_gates_hook_timeout(harness):
    """Measured on 2026-08-06 against a real workspace: a cold `npx ccusage`
    costs ~1.9s, and the gate's lazy-snapshot repair path spawns it while the
    user's edit is blocked. The gate's hooks.json timeout is 10s but ccusage's
    own default is 25s, so a slow npx would let Claude Code kill this hook
    mid-write — losing the very baseline the repair exists to create, and
    stalling the edit for the full budget first.

    The repair is also strictly optional: attribution is nice, an unblocked edit
    is not. So the gate caps ccusage far below its own timeout.
    """
    harness.make_beads_project()
    harness.set_bd_json("list", CLAIMED)
    harness.set_ccusage_hang(6)

    started = time.time()
    res = _gate(harness, ABACUS_GATE_CACHE_TTL_S=0)
    elapsed = time.time() - started

    assert res.permission_decision != "deny", "a slow ccusage must never block an edit"
    assert elapsed < 5, (
        "gate took %.1fs; the lazy snapshot must be capped well under the 10s "
        "hook timeout" % elapsed)


def test_a_snapshot_that_times_out_still_leaves_the_task_tracked(harness):
    """Losing the cost figure is acceptable; losing the fact that a task is
    current is not — the statusline and Stop reconciliation both depend on it."""
    harness.make_beads_project()
    harness.set_bd_json("list", CLAIMED)
    harness.set_ccusage_hang(6)
    _gate(harness, ABACUS_GATE_CACHE_TTL_S=0)
    state = harness.read_state("sess-1") or {}
    assert state.get("current_task") == "bd-a1b2"
