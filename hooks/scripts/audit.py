#!/usr/bin/env python3
"""The audit: find where work and attribution came apart, and optionally repair it.

Not a hook. Nothing in ``hooks.json`` points here — it is invoked deliberately, by
the ``/abacus:audit`` command, by the ``task-audit`` skill, or by the
``abacus-auditor`` agent. It still lives in ``hooks/scripts/`` beside the hooks and
still keeps their discipline (``guard()``, exit 0, stdlib only), because it runs
inside an agent turn where a traceback reads as a broken tool call.

    audit.py [--json] [--fix] [--stale-after-h N] [--since "<git date>"]

Two subprocesses at most: one ``bd list --all --json`` for the whole workspace, and
one ``git log`` when there is a repository to read. The detectors are pure and live
in ``hooks/lib/audit.py``; this script is the I/O around them.

**Why this script exists rather than the skill just running bd.** ``abacus_*`` keys
are constructed in exactly one module (``hooks/lib/attribution.py``), so a skill
that assembled ``--set-metadata`` flags from prose would become a second writer,
free to drift from the real one and unbound by its rules. Routing the write through
here keeps one constructor.

**What ``--fix`` will and will not touch.** Only gaps the detectors marked
``fixable``: a closed issue with no attribution, and one left marked partial. Both
have exactly one correct repair. A stale claim and an untracked commit are reported
and never written, because deciding what they mean is a judgement about intent —
closing a stale claim would mark work done that is not done. That judgement belongs
to the agent or the user, which is why the JSON carries every gap and not just the
fixable ones.

**A failed read is reported as a failed read.** ``{"ok": false, "reason": ...}``,
never ``{"ok": true, "gaps": []}``. Telling someone their tracking is complete
because bd could not be reached is the same class of wrong answer as a $0.00 cost
estimate, and it would talk them out of looking.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import abacus_config  # noqa: E402
import abacus_time  # noqa: E402
import attribution  # noqa: E402
import audit as audit_lib  # noqa: E402
import beads  # noqa: E402
import gitlog  # noqa: E402
import hook_io  # noqa: E402

# What each gap kind is called in the human report. The JSON uses the kind slugs.
_HEADINGS = {
    audit_lib.KIND_UNCLAIMED: "nothing claimed",
    audit_lib.KIND_STALE_CLAIM: "stale claim",
    audit_lib.KIND_UNFINALISED: "closed but never finalised",
    audit_lib.KIND_UNATTRIBUTED: "closed with no attribution",
    audit_lib.KIND_UNTRACKED_COMMITS: "commits outside every claim window",
}


def parse_args(argv):
    """Options, hand-rolled so an unknown flag cannot exit non-zero.

    ``argparse`` would exit 2 with usage on stderr, which inside an agent turn is
    indistinguishable from the script being broken. An unrecognised flag here is
    ignored and the audit still runs.
    """
    opts = {"json": False, "fix": False, "stale_after_h": None, "since": None}
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg in ("--json", "-j"):
            opts["json"] = True
        elif arg == "--fix":
            opts["fix"] = True
        elif arg == "--stale-after-h" and rest:
            try:
                opts["stale_after_h"] = float(rest.pop(0))
            except ValueError:
                pass
        elif arg == "--since" and rest:
            opts["since"] = rest.pop(0)
    return opts


def collect(cwd, cfg, opts, now):
    """(report, issues_by_id). ``report["ok"]`` False means nothing was concluded."""
    if not beads.has_workspace(cwd):
        return {"ok": False, "reason": (
            "No beads workspace here, so there is nothing to audit. "
            "`bd init` creates one."
        )}, {}

    if not beads.available():
        return {"ok": False, "reason": (
            "bd is not on PATH, so the task store could not be read. This is not "
            "evidence that nothing is missing."
        )}, {}

    listing = beads.list_all(cwd=cwd)
    if not listing["available"]:
        return {"ok": False, "reason": (
            "`bd list --all --json` did not return a readable workspace, so no "
            "conclusion can be drawn about what is tracked."
        )}, {}

    issues = listing["issues"]
    audit_cfg = cfg.get("audit") or {}
    stale_after_h = opts["stale_after_h"]
    if stale_after_h is None:
        stale_after_h = audit_cfg.get("stale_after_h", audit_lib.DEFAULT_STALE_AFTER_H)
    since = opts["since"] or audit_cfg.get("commit_window", gitlog.DEFAULT_SINCE)

    commits = gitlog.recent_commits(cwd=cwd, since=since)
    gaps = audit_lib.audit(issues, now=now, commits=commits,
                           stale_after_h=stale_after_h)

    report = {
        "ok": True,
        "checked_at": now,
        "issues_seen": len(issues),
        "commits_seen": len(commits),
        "gaps": gaps,
        "fixable": len([g for g in gaps if g.get("fixable")]),
        "fixed": [],
        "fix_failed": [],
    }
    return report, {str(i.get("id")): i for i in issues}


def apply_fixes(report, by_id, cwd, now):
    """Write the one correct repair for every fixable gap. Mutates `report`."""
    for gap in report["gaps"]:
        if not gap.get("fixable"):
            continue
        issue_id = gap.get("issue_id")
        issue = by_id.get(str(issue_id))
        if not issue:
            continue
        meta = attribution.backfill_metadata(issue, now=now)
        if beads.set_metadata(issue_id, meta, cwd=cwd):
            report["fixed"].append(issue_id)
        else:
            # Loud in the report rather than silent: believing a gap was closed
            # when the write bounced is worse than knowing it is still open.
            report["fix_failed"].append(issue_id)
    return report


def render(report, fixed_requested):
    """The report as prose. Text is the default; agents pass ``--json``."""
    lines = []
    if not report.get("ok"):
        lines.append("abacus audit: could not check.")
        lines.append("  %s" % report.get("reason", "unknown reason"))
        return "\n".join(lines)

    gaps = report["gaps"]
    if not gaps:
        lines.append("abacus audit: no gaps. %d issue(s) checked, %d commit(s) in window."
                     % (report["issues_seen"], report["commits_seen"]))
        return "\n".join(lines)

    lines.append("abacus audit: %d gap(s) across %d issue(s)."
                 % (len(gaps), report["issues_seen"]))
    lines.append("")
    for gap in gaps:
        label = _HEADINGS.get(gap["kind"], gap["kind"])
        ident = " ".join(str(p) for p in (gap.get("issue_id"), gap.get("title")) if p)
        head = "  %s%s" % (label, (" — " + ident) if ident else "")
        if gap.get("fixable"):
            head += "  [fixable]"
        lines.append(head)
        lines.append("      %s" % gap["detail"])
        if gap.get("shas"):
            for sha, subject in zip(gap["shas"][:10], gap.get("subjects") or []):
                lines.append("        %s  %s" % (str(sha)[:8], subject))
            if len(gap["shas"]) > 10:
                lines.append("        … and %d more" % (len(gap["shas"]) - 10))
    lines.append("")

    if fixed_requested:
        if report["fixed"]:
            lines.append("Repaired %d issue(s): %s" % (len(report["fixed"]),
                                                       ", ".join(report["fixed"])))
        if report["fix_failed"]:
            lines.append("Could NOT write to %d issue(s): %s"
                         % (len(report["fix_failed"]), ", ".join(report["fix_failed"])))
        if not report["fixed"] and not report["fix_failed"]:
            lines.append("Nothing was repairable without a judgement call.")
        lines.append("Backfilled figures carry abacus_backfilled=true and, where no "
                     "measurement survived, abacus_cost_basis=unavailable with no "
                     "dollar figure — not a zero.")
    elif report["fixable"]:
        lines.append("%d of these can be repaired with --fix. The rest need a decision."
                     % report["fixable"])
    else:
        lines.append("None of these can be repaired automatically; each needs a decision.")
    return "\n".join(lines)


def main():
    opts = parse_args(sys.argv[1:])
    # The payload is optional: this runs as a command, not on an event. When one
    # is present its cwd is the better answer than os.getcwd(), for the same
    # reason the hooks prefer it.
    payload = hook_io.read_payload()
    cwd = hook_io.payload_cwd(payload)
    # Deliberately not gated on abacus_config.is_disabled(). The kill switch stops
    # the plugin acting on its own; it should not stop a read the user asked for.
    cfg = abacus_config.load_config()
    now = abacus_time.now_iso()

    report, by_id = collect(cwd, cfg, opts, now)
    if report.get("ok") and opts["fix"]:
        apply_fixes(report, by_id, cwd, now)

    if opts["json"]:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render(report, opts["fix"]) + "\n")
    return 0


if __name__ == "__main__":
    hook_io.guard(main)
