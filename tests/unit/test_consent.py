"""RED: the acknowledgement that has to precede any unprompted action.

abacus does four things nobody asked for in the moment: it denies a tool call, it
creates a `.beads/` inside a repository, it writes metadata onto an issue, and it
can push to a remote as a session closes. A plugin installed user-wide should not
begin doing those on the strength of having been installed.

So there is one invariant, and this file exists to hold it: **until the governing
settings are acknowledged, abacus performs no write and no denial.** The rest is
bookkeeping about what "the governing settings" means and when consent goes stale.
"""

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB = os.path.join(REPO_ROOT, "hooks", "lib")


def _probe(harness, code, **env_overrides):
    prog = "import sys; sys.path.insert(0, %r)\n%s" % (LIB, code)
    proc = subprocess.run(
        [sys.executable, "-c", prog], capture_output=True, text=True,
        cwd=str(harness.project), env=harness.env(**env_overrides), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ── what counts as a governing setting ──────────────────────────────────────

def test_governing_settings_cover_every_unprompted_action(harness):
    """The fingerprint must span each of the four things abacus does unasked."""
    out = _probe(harness, """
import consent, json
print(json.dumps(sorted(consent.governing_settings())))
""")
    assert out == [
        "auto_init.enabled",
        "auto_init.roots",
        "auto_init.stealth",
        "gate.enabled",
        "gate.non_beads_project",
        "sync_on_session_end",
    ]


def test_a_cosmetic_setting_is_not_governing(harness):
    """Bumping ccusage or turning off the statusline must not re-ask for consent.

    A notice that reappears for changes the user did not consent *to* is a notice
    people learn to dismiss without reading, which is worse than not asking.
    """
    harness.write_config({"ccusage_version": "ccusage@20.0.14", "statusline": True})
    before = _probe(harness, """
import consent, json
print(json.dumps({"fp": consent.fingerprint()}))
""")["fp"]
    harness.write_config({"ccusage_version": "ccusage@99.0.0", "statusline": False})
    after = _probe(harness, """
import consent, json
print(json.dumps({"fp": consent.fingerprint()}))
""")["fp"]
    assert before == after


@pytest.mark.parametrize("cfg", [
    {"gate": {"enabled": False}},
    {"gate": {"non_beads_project": "block"}},
    {"auto_init": {"enabled": True}},
    {"auto_init": {"roots": []}},
    {"auto_init": {"stealth": False}},
    {"sync_on_session_end": "push"},
], ids=["gate.enabled", "gate.non_beads_project", "auto_init.enabled",
        "auto_init.roots", "auto_init.stealth", "sync_on_session_end"])
def test_changing_a_governing_setting_changes_the_fingerprint(harness, cfg):
    harness.write_config({})
    base = _probe(harness, """
import consent, json
print(json.dumps({"fp": consent.fingerprint()}))
""")["fp"]
    harness.write_config(cfg)
    changed = _probe(harness, """
import consent, json
print(json.dumps({"fp": consent.fingerprint()}))
""")["fp"]
    assert changed != base, "%r must invalidate an existing acknowledgement" % cfg


def test_roots_order_is_not_a_meaningful_change(harness):
    """Consent is to a *set* of roots; reordering them grants nothing new."""
    harness.write_config({"auto_init": {"roots": ["~/a", "~/b"]}})
    one = _probe(harness, """
import consent, json
print(json.dumps({"fp": consent.fingerprint()}))
""")["fp"]
    harness.write_config({"auto_init": {"roots": ["~/b", "~/a"]}})
    two = _probe(harness, """
import consent, json
print(json.dumps({"fp": consent.fingerprint()}))
""")["fp"]
    assert one == two


# ── the three states ────────────────────────────────────────────────────────

def test_a_fresh_install_is_not_acknowledged(harness):
    harness.revoke_acknowledgement()
    out = _probe(harness, """
import consent, json
print(json.dumps({"status": consent.status(), "ack": consent.is_acknowledged()}))
""")
    assert out == {"status": "never", "ack": False}


def test_acknowledging_records_consent_for_the_current_settings(harness):
    harness.revoke_acknowledgement()
    harness.write_config({"auto_init": {"enabled": True}}, acknowledge=False)
    out = _probe(harness, """
import consent, json
consent.acknowledge()
print(json.dumps({"status": consent.status(), "ack": consent.is_acknowledged()}))
""")
    assert out == {"status": "acknowledged", "ack": True}


def test_the_record_is_readable_by_a_human(harness):
    """A consent record nobody can read is a checkbox, not a record.

    The settings are stored verbatim beside the fingerprint so that what was
    agreed to can be inspected later without recomputing a hash.
    """
    harness.revoke_acknowledgement()
    harness.write_config({"auto_init": {"enabled": True, "roots": ["~/work"]}},
                         acknowledge=False)
    _probe(harness, """
import consent, json
consent.acknowledge()
print(json.dumps({"ok": True}))
""")
    record = json.loads((harness.state_dir / "acknowledged.json").read_text())
    assert record["settings"]["auto_init.enabled"] is True
    assert record["settings"]["auto_init.roots"] == ["~/work"]
    assert record["fingerprint"]
    assert record["acknowledged_at"]


def test_widening_the_roots_after_acknowledging_pauses_governance(harness):
    """The scenario the fingerprint exists for: consent given for one scope,
    silently reused for a wider one."""
    harness.revoke_acknowledgement()
    harness.write_config({"auto_init": {"enabled": True, "roots": ["~/projects"]}},
                         acknowledge=False)
    _probe(harness, """
import consent, json
consent.acknowledge()
print(json.dumps({"ok": True}))
""")
    harness.write_config({"auto_init": {"enabled": True, "roots": []}}, acknowledge=False)
    out = _probe(harness, """
import consent, json
print(json.dumps({
    "status": consent.status(),
    "ack": consent.is_acknowledged(),
    "changed": consent.changed_keys(),
}))
""")
    assert out["status"] == "changed"
    assert out["ack"] is False
    assert out["changed"] == ["auto_init.roots"]


def test_an_unreadable_record_is_not_an_acknowledgement(harness):
    """Both of the plugin's safety directions agree here, unusually.

    Elsewhere "cannot tell" has to pick between failing open (the gate) and
    failing closed (auto_init). Here they coincide: an unreadable record means no
    consent, which means no denial *and* no write.
    """
    harness.revoke_acknowledgement()
    (harness.state_dir / "acknowledged.json").write_text("{ this is not json")
    out = _probe(harness, """
import consent, json
print(json.dumps({"status": consent.status(), "ack": consent.is_acknowledged()}))
""")
    assert out["ack"] is False
    assert out["status"] == "never"


def test_a_record_with_no_fingerprint_is_not_an_acknowledgement(harness):
    harness.revoke_acknowledgement()
    (harness.state_dir / "acknowledged.json").write_text(json.dumps({"acknowledged_at": "x"}))
    out = _probe(harness, """
import consent, json
print(json.dumps({"ack": consent.is_acknowledged()}))
""")
    assert out["ack"] is False


# ── the notice ──────────────────────────────────────────────────────────────

def test_the_notice_states_what_will_happen_and_how_to_agree(harness):
    harness.revoke_acknowledgement()
    harness.write_config({
        "gate": {"non_beads_project": "block"},
        "auto_init": {"enabled": True, "roots": ["~/projects"]},
    }, acknowledge=False)
    out = _probe(harness, """
import consent, json
print(json.dumps({"notice": consent.notice()}))
""")
    notice = out["notice"]
    assert "/abacus:acknowledge" in notice, "the notice must name the way to agree"
    assert "~/projects" in notice, "it must name the scope it is asking about"
    assert "not governing" in notice.lower(), (
        "it must say abacus is doing nothing yet — otherwise it reads as a warning "
        "about something already happening"
    )


def test_the_notice_names_what_changed_when_consent_went_stale(harness):
    harness.revoke_acknowledgement()
    harness.write_config({"auto_init": {"enabled": True, "roots": ["~/projects"]}},
                         acknowledge=False)
    _probe(harness, """
import consent, json
consent.acknowledge()
print(json.dumps({"ok": True}))
""")
    harness.write_config({"auto_init": {"enabled": True, "roots": []}}, acknowledge=False)
    out = _probe(harness, """
import consent, json
print(json.dumps({"notice": consent.notice()}))
""")
    assert "auto_init.roots" in out["notice"], (
        "a re-ask must say which setting changed, or it is indistinguishable from a bug"
    )


def test_the_notice_is_empty_once_acknowledged(harness):
    """Nothing is injected into context in the steady state. Enforcement that
    costs tokens on every prompt forever is the thing this plugin avoids."""
    out = _probe(harness, """
import consent, json
print(json.dumps({"notice": consent.notice()}))
""")
    assert out["notice"] == ""
