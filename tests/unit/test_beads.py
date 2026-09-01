"""RED: beads wrapper — the bd CLI contract the gate depends on.

Contract measured against bd 1.1.2 on 2026-08-05:
  valid workspace, nothing in progress -> rc=0, stdout "[]"
  no beads database resolvable         -> rc=1, stdout "", stderr "Error: no beads database found"
  bd show <id> --json                 -> a JSON *array* of one issue, not an object
Distinguishing rc=1 from an empty array is the whole ballgame: empty means
"nothing claimed, block the edit", rc=1 means "no database, fail open".
"""

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB = os.path.join(REPO_ROOT, "hooks", "lib")

IN_PROGRESS = [{
    "id": "bd-probe-an8", "title": "probe task one", "status": "in_progress",
    "priority": 2, "issue_type": "task", "assignee": "dev",
    "updated_at": "2026-08-05T21:34:12Z", "started_at": "2026-08-05T21:34:12Z",
}]


def _probe(harness, code, **env_overrides):
    prog = "import sys; sys.path.insert(0, %r)\n%s" % (LIB, code)
    proc = subprocess.run(
        [sys.executable, "-c", prog], capture_output=True, text=True,
        cwd=str(harness.project), env=harness.env(**env_overrides), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_in_progress_returns_the_claimed_issues(harness):
    harness.set_bd_json("list", IN_PROGRESS)
    out = _probe(harness, """
import beads, json
print(json.dumps(beads.in_progress()))
""")
    assert out["available"] is True
    assert [i["id"] for i in out["issues"]] == ["bd-probe-an8"]


def test_empty_array_means_nothing_claimed_and_bd_is_available(harness):
    harness.set_bd_json("list", [])
    out = _probe(harness, """
import beads, json
print(json.dumps(beads.in_progress()))
""")
    assert out["available"] is True
    assert out["issues"] == []


def test_no_database_is_reported_as_unavailable_not_as_empty(harness):
    """rc=1 must NOT look like "nothing in progress" — that would block edits
    in every repo without a beads workspace."""
    harness.set_bd("list", stdout="", rc=1)
    out = _probe(harness, """
import beads, json
print(json.dumps(beads.in_progress()))
""")
    assert out["available"] is False
    assert out["issues"] == []


def test_missing_bd_binary_is_reported_as_unavailable(harness):
    harness.remove_bd()
    out = _probe(harness, """
import beads, json
print(json.dumps(beads.in_progress()))
""")
    assert out["available"] is False


def test_unparsable_bd_output_is_unavailable(harness):
    harness.set_bd("list", stdout="not json", rc=0)
    out = _probe(harness, """
import beads, json
print(json.dumps(beads.in_progress()))
""")
    assert out["available"] is False


def test_show_unwraps_the_single_element_array(harness):
    harness.set_bd_json("show", IN_PROGRESS)
    out = _probe(harness, """
import beads, json
print(json.dumps(beads.show("bd-probe-an8")))
""")
    assert out["id"] == "bd-probe-an8"
    assert out["status"] == "in_progress"


def test_show_of_unknown_issue_returns_none(harness):
    harness.set_bd("show", stdout="[]", rc=0)
    out = _probe(harness, """
import beads, json
print(json.dumps({"result": beads.show("nope")}))
""")
    assert out["result"] is None


def test_set_metadata_passes_each_pair_as_its_own_flag(harness):
    harness.set_bd("update", stdout="ok", rc=0)
    _probe(harness, """
import beads, json
ok = beads.set_metadata("bd-1", {"abacus_tokens_total": 100, "abacus_cost_usd_estimate": 0.5})
print(json.dumps({"ok": ok}))
""")
    call = " ".join(harness.bd_calls())
    assert "--set-metadata abacus_tokens_total=100" in call
    assert "--set-metadata abacus_cost_usd_estimate=0.5" in call


def test_set_metadata_reports_failure_without_raising(harness):
    harness.set_bd("update", stdout="", rc=1)
    out = _probe(harness, """
import beads, json
print(json.dumps({"ok": beads.set_metadata("bd-1", {"a": 1})}))
""")
    assert out["ok"] is False


def test_set_metadata_with_no_pairs_makes_no_call(harness):
    harness.set_bd("update", stdout="ok", rc=0)
    _probe(harness, """
import beads, json
print(json.dumps({"ok": beads.set_metadata("bd-1", {})}))
""")
    assert harness.bd_calls() == []


def test_metadata_values_are_serialised_without_spaces(harness):
    """A value reaching bd as two argv words would silently truncate."""
    harness.set_bd("update", stdout="ok", rc=0)
    _probe(harness, """
import beads, json
print(json.dumps({"ok": beads.set_metadata("bd-1", {"abacus_models": ["claude-fable-5", "claude-opus-5"]})}))
""")
    call = [c for c in harness.bd_calls() if "--set-metadata" in c][0]
    pair = call.split("--set-metadata ")[1].strip()
    assert " " not in pair, "metadata value must be a single argv token, got %r" % pair


def test_workspace_detected_via_dot_beads_directory(harness):
    project = harness.make_beads_project()
    out = _probe(harness, """
import beads, json
print(json.dumps({"has": beads.has_workspace(%r)}))
""" % str(project))
    assert out["has"] is True


def test_workspace_detected_in_a_parent_directory(harness):
    project = harness.make_beads_project()
    nested = project / "src" / "deep"
    nested.mkdir(parents=True)
    out = _probe(harness, """
import beads, json
print(json.dumps({"has": beads.has_workspace(%r)}))
""" % str(nested))
    assert out["has"] is True, "a subdirectory of a beads repo is still tracked"


def test_no_workspace_when_no_dot_beads_anywhere_above(harness):
    bare = harness.tmp / "bare"
    bare.mkdir()
    out = _probe(harness, """
import beads, json
print(json.dumps({"has": beads.has_workspace(%r)}))
""" % str(bare))
    assert out["has"] is False


def test_dot_beads_at_home_is_not_a_workspace(harness):
    """`bd` puts an `eventsData/` sidecar in `~/.beads`, and it holds no database.

    Observed on 2026-09-01: `~/.beads/eventsData/` existed, `bd list` from `$HOME`
    answered "no beads database found", and yet the upward walk stopped there and
    declared every repository on the machine tracked — which silently disabled
    `auto_init` everywhere. adr/012 already refuses to *create* a workspace at
    `$HOME` because it would capture every session beneath it; detection has to
    honour the same boundary or the rail only holds in one direction.
    """
    (harness.home / ".beads" / "eventsData").mkdir(parents=True, exist_ok=True)
    repo = harness.home / "projects" / "fresh-clone"
    repo.mkdir(parents=True)
    out = _probe(harness, """
import beads, json
print(json.dumps({"has": beads.has_workspace(%r)}))
""" % str(repo))
    assert out["has"] is False, "a .beads at $HOME must not make every repo below it tracked"


def test_dot_beads_at_home_still_allows_a_real_workspace_below_it(harness):
    """The $HOME stop must not swallow a genuine workspace on the way up."""
    (harness.home / ".beads").mkdir(parents=True, exist_ok=True)
    repo = harness.home / "projects" / "tracked"
    (repo / ".beads").mkdir(parents=True)
    nested = repo / "src" / "deep"
    nested.mkdir(parents=True)
    out = _probe(harness, """
import beads, json
print(json.dumps({"has": beads.has_workspace(%r)}))
""" % str(nested))
    assert out["has"] is True


def test_beads_dir_env_var_counts_as_a_workspace(harness):
    bare = harness.tmp / "bare2"
    bare.mkdir()
    out = _probe(harness, """
import beads, json
print(json.dumps({"has": beads.has_workspace(%r)}))
""" % str(bare), BEADS_DIR=str(harness.tmp / "elsewhere" / ".beads"))
    assert out["has"] is True


def test_prime_returns_the_hook_json_passthrough(harness):
    harness.set_bd("prime", stdout=json.dumps(
        {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "ctx"}}), rc=0)
    out = _probe(harness, """
import beads, json
print(json.dumps(beads.prime()))
""")
    assert out["hookSpecificOutput"]["additionalContext"] == "ctx"


def test_prime_failure_returns_none(harness):
    harness.set_bd("prime", stdout="", rc=1)
    out = _probe(harness, """
import beads, json
print(json.dumps({"result": beads.prime()}))
""")
    assert out["result"] is None


def test_close_reason_is_passed_as_a_single_token(harness):
    harness.set_bd("close", stdout="ok", rc=0)
    _probe(harness, """
import beads, json
print(json.dumps({"ok": beads.close("bd-1", reason="done well")}))
""")
    assert any("bd-1" in c for c in harness.bd_calls())
