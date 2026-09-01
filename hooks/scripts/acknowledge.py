#!/usr/bin/env python3
"""The consent surface: show what abacus would do, and record an answer.

Not a hook. Nothing in ``hooks.json`` points here — it is invoked by
``/abacus:acknowledge``, and it is the only way to switch governance on. Until it
has been run, the gate denies nothing, ``auto_init`` creates nothing, and no
``abacus_*`` key is written to any issue (adr/014).

    acknowledge.py [--show] [--accept] [--revoke] [--json]

``--show`` is the default, and it is the default deliberately: a bare invocation
must be safe to run out of curiosity. Consent that can be given by mistyping is
not consent, so the recording only happens when ``--accept`` is passed explicitly.

**Why a script and not prose in the command file.** The fingerprint is a hash of
the settings that actually govern behaviour, read through the same accessors the
hooks use — so what is agreed to is exactly what will run. A command that wrote
the record itself would be a second implementation of that agreement, free to
drift from the one the hooks check.

Exits 0 whatever it finds, like everything else here. A consent surface that
crashes is a plugin that can never be switched on.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import abacus_config  # noqa: E402
import consent  # noqa: E402
import hook_io  # noqa: E402

ACCEPTED = """abacus: acknowledged. Governance is on.

From now on it will deny Edit/Write/NotebookEdit/MultiEdit when no beads task is
in progress, and attribute cost, tokens and duration to whichever task is claimed.

Change your mind with  /abacus:acknowledge revoke   ·   silence one shell with
ABACUS_DISABLE=1. Changing a setting that governs behaviour will ask again."""

REVOKED = """abacus: acknowledgement withdrawn. It is inert again — no denials, no
workspace creation, no writes to any issue — until you run /abacus:acknowledge."""

WRITE_FAILED = """abacus: could not record the acknowledgement, so nothing changed
and abacus stays inert. The state directory (%s) was not writable."""


def parse_args(argv):
    """Options, hand-rolled so an unknown flag cannot exit non-zero (see audit.py)."""
    opts = {"json": False, "accept": False, "revoke": False}
    for arg in argv:
        if arg in ("--json", "-j"):
            opts["json"] = True
        elif arg in ("--accept", "accept", "--yes", "-y"):
            opts["accept"] = True
        elif arg in ("--revoke", "revoke", "--withdraw"):
            opts["revoke"] = True
        # --show is the absence of the others, so it needs no branch.
    return opts


def _report(cfg, action=None, ok=True):
    """The machine-readable answer. Dotted keys, matching the fingerprint's."""
    state = consent.status(cfg)
    report = {
        "acknowledged": state == "acknowledged",
        "status": state,
        "settings": consent.governing_settings(cfg),
        "record_path": consent.acknowledgement_path(),
    }
    if action:
        report["action"] = action
        report["ok"] = ok
    if state == "changed":
        # Which keys moved is the whole reason a re-ask is not just noise.
        report["changed"] = consent.changed_keys(cfg)
    return report


def main():
    opts = parse_args(sys.argv[1:])
    # No payload is required — this runs as a command, not on an event — but when
    # Claude Code supplies one, reading it keeps stdin from being left full.
    hook_io.read_payload()
    cfg = abacus_config.load_config()

    action, ok, message = None, True, None

    if opts["revoke"]:
        action, ok = "revoke", consent.revoke()
        # A missing record and a deleted one leave the user in the same place, so
        # both report the same thing rather than dressing one up as a failure.
        message = REVOKED
    elif opts["accept"]:
        action = "accept"
        ok = consent.acknowledge(cfg)
        message = ACCEPTED if ok else WRITE_FAILED % os.path.dirname(
            consent.acknowledgement_path())
    else:
        message = consent.notice(cfg) or (
            "abacus: acknowledged and governing. Withdraw with "
            "/abacus:acknowledge revoke.")

    report = _report(cfg, action=action, ok=ok)
    if opts["json"]:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(message + "\n")
    return 0


if __name__ == "__main__":
    hook_io.guard(main)
