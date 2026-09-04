"""The premortem for adr/015's Decision 1, run against a real beads database.

One metadata key per commit is only reversible-at-the-right-granularity if bd will
actually hold that many keys on one issue. adr/analysis/015 records this as
Assumption 1 and rates the evidence for it **low**: every other metadata mechanic
in this repo was verified, but no per-issue key-count or size ceiling had been
looked for, and a ceiling reached silently after a hundred commits would not
present as a bug — it would present as edges quietly going missing.

So this is a falsifier, not a regression test. It asserts the four properties
Decision 1 rests on, at a scale a long-lived task would actually reach:

1. 200 ``abacus_commit_<sha12>`` keys write and read back from one issue;
2. their values round-trip verbatim, so a basis is never silently mangled;
3. they coexist with the ``abacus_cost_*`` keys attribution writes to the same
   issue — the keys share a namespace and a merge;
4. **one** key can be removed with ``--unset-metadata`` without disturbing the
   rest, which is the whole warrant for choosing per-key granularity over a
   packed list, and is checked through both read paths (``show`` and
   ``list --all``, the one the audit uses).

**Opt-in, deliberately.** The suite's stated guarantee is that no test touches a
real beads database, and this one does — it runs `bd init` and writes. A
``skipif`` on `bd` being present would silently break that guarantee on every
developer machine that has bd installed, which is most of them. So it needs an
explicit switch:

    ABACUS_REAL_BD_TESTS=1 python3 -m pytest tests/integration/test_bd_metadata_ceiling.py -v

Everything it touches lives under `tmp_path` with ``BEADS_DIR`` pointed inside it,
so it cannot reach the developer's own database even when enabled.

Result on bd 1.1.2 (2026-09-03): all four hold. 200 keys in ~6s of `bd update`
calls, 10 boundaries' worth; no ceiling found.
"""

import json
import os
import shutil
import subprocess

import pytest

KEYS = 200
BATCH = 20  # one boundary's worth, so a failure names the batch it broke on

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("ABACUS_REAL_BD_TESTS") != "1",
        reason="writes to a real beads database; set ABACUS_REAL_BD_TESTS=1 to run",
    ),
    pytest.mark.skipif(shutil.which("bd") is None, reason="bd is not installed"),
    pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed"),
]


def _run(args, cwd, env):
    proc = subprocess.run(
        args, cwd=str(cwd), env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=120,
    )
    return proc


def _metadata(issue, cwd, env, subcommand):
    """The issue's metadata, read back through one of the two read paths."""
    args = ["bd", "show", issue, "--json"] if subcommand == "show" \
        else ["bd", "list", "--all", "--json"]
    proc = _run(args, cwd, env)
    assert proc.returncode == 0, "bd %s failed: %s" % (subcommand, proc.stderr)
    rows = json.loads(proc.stdout)
    if not isinstance(rows, list):
        rows = [rows]
    row = [r for r in rows if r.get("id") == issue]
    assert row, "%s did not return %s" % (subcommand, issue)
    return row[0].get("metadata") or {}


@pytest.fixture
def real_beads(tmp_path):
    """A throwaway workspace: real git, real `bd init`, nothing shared."""
    root = tmp_path / "probe"
    root.mkdir()
    env = dict(os.environ)
    env["BEADS_DIR"] = str(root / ".beads")
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull

    assert _run(["git", "init", "-q", "."], root, env).returncode == 0
    init = _run(["bd", "init"], root, env)
    assert init.returncode == 0, "bd init failed: %s" % (init.stderr or init.stdout)

    created = _run(["bd", "create", "metadata ceiling probe", "--json"], root, env)
    assert created.returncode == 0, "bd create failed: %s" % created.stderr
    payload = json.loads(created.stdout)
    if isinstance(payload, list):
        payload = payload[0]
    issue = payload.get("id") or (payload.get("issue") or {}).get("id")
    assert issue, "could not read the created issue's id from %r" % created.stdout
    return root, env, issue


def test_two_hundred_commit_edges_fit_on_one_issue(real_beads):
    root, env, issue = real_beads
    value = "observed:sess-probe:1757000000"
    expected = set()

    for batch in range(KEYS // BATCH):
        args = ["bd", "update", issue]
        for i in range(BATCH):
            key = "abacus_commit_%012x" % (batch * BATCH + i)
            expected.add(key)
            args += ["--set-metadata", "%s=%s" % (key, value)]
        proc = _run(args, root, env)
        assert proc.returncode == 0, "batch %d of %d refused: %s" % (
            batch, KEYS // BATCH, proc.stderr or proc.stdout)

    metadata = _metadata(issue, root, env, "show")
    written = {k for k in metadata if k.startswith("abacus_commit_")}
    assert written == expected, "%d of %d keys survived the round trip" % (
        len(written), KEYS)
    # Property 2: verbatim, not merely present. A basis that arrived mangled would
    # make every edge unreadable while every key looked fine.
    assert {metadata[k] for k in written} == {value}


def test_commit_edges_coexist_with_the_attribution_keys(real_beads):
    """They share a namespace and a merge, so this is not a given."""
    root, env, issue = real_beads
    _run(["bd", "update", issue,
          "--set-metadata", "abacus_commit_%012x" % 1 + "=observed:sess-probe:1757000000"],
         root, env)
    proc = _run(["bd", "update", issue,
                 "--set-metadata", "abacus_cost_usd_estimate=0.8123",
                 "--set-metadata", "abacus_cost_basis=ccusage-local-list-rate"],
                root, env)
    assert proc.returncode == 0, proc.stderr

    metadata = _metadata(issue, root, env, "show")
    assert metadata.get("abacus_cost_basis") == "ccusage-local-list-rate"
    assert metadata.get("abacus_cost_usd_estimate") == 0.8123
    assert "abacus_commit_000000000001" in metadata


def test_one_edge_can_be_withdrawn_without_disturbing_the_others(real_beads):
    """The warrant for per-key granularity, checked rather than assumed.

    Both read paths, because the audit reads `list --all` and the report reads
    `show`; a key that vanished from one and not the other would make the two
    disagree about what was recorded.
    """
    root, env, issue = real_beads
    keys = ["abacus_commit_%012x" % i for i in range(5)]
    args = ["bd", "update", issue]
    for key in keys:
        args += ["--set-metadata", "%s=observed:sess-probe:1757000000" % key]
    assert _run(args, root, env).returncode == 0

    proc = _run(["bd", "update", issue, "--unset-metadata", keys[0]], root, env)
    assert proc.returncode == 0, "--unset-metadata refused: %s" % proc.stderr

    for subcommand in ("show", "list"):
        metadata = _metadata(issue, root, env, subcommand)
        assert keys[0] not in metadata, "%s still reports the withdrawn edge" % subcommand
        for key in keys[1:]:
            assert key in metadata, "%s lost %s along with the withdrawn one" % (
                subcommand, key)
