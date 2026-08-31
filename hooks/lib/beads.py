#!/usr/bin/env python3
"""beads (bd) wrapper — the read/write interface to the task store of record.

beads owns what work exists and what is in progress; this plugin never keeps a
parallel task table (adr/001). There is no MCP server here either: bd already
ships a CLI with ``--json`` on every read, so a second reader would only add a
surface to keep in sync (adr/004).

The bd 1.1.2 contract this module encodes, measured directly on 2026-08-05:

===========================================  ====  =========================
invocation                                   rc    stdout
===========================================  ====  =========================
list --status in_progress --json (claimed)   0     JSON array of issues
list --status in_progress --json (none)      0     ``[]``
list --status in_progress --json (no DB)     1     empty; stderr "Error: no
                                                   beads database found"
show <id> --json                             0     JSON array of ONE issue
update <id> --set-metadata k=v               0     human text, merges keys,
                                                   works on closed issues too
===========================================  ====  =========================

The rc=1-versus-``[]`` distinction is the most important line in this file. Both
mean "no issue came back", but ``[]`` means *nothing is claimed* (block the edit)
while rc=1 means *there is no database here* (fail open, never gate a repo that
does not use beads). Collapsing them would make this plugin unusable outside
beads projects.

Every function degrades rather than raises: hooks have no supervisor.
"""

import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# bd reads/writes an embedded Dolt database; a few seconds is generous for the
# local queries here, and bounding it keeps a wedged bd off the edit hot path.
DEFAULT_TIMEOUT_S = 8


def bd_cmd():
    """Resolve the bd executable, honouring the test override."""
    override = os.environ.get("ABACUS_BD_CMD")
    if override:
        return override.split()
    # shutil.which first: Windows does not consult PATHEXT for a bare name, so
    # relying on the shell to resolve "bd" is not portable.
    found = shutil.which("bd")
    return [found] if found else None


def available():
    return bd_cmd() is not None


def _run(args, timeout=DEFAULT_TIMEOUT_S, cwd=None, env_extra=None):
    """Run bd with `args`. Returns (rc, stdout, stderr); rc=127 if bd is absent."""
    cmd = bd_cmd()
    if not cmd:
        return 127, "", "bd not found on PATH"
    env = None
    if env_extra:
        env = dict(os.environ)
        env.update(env_extra)
    try:
        proc = subprocess.run(
            cmd + list(args),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,  # see ccusage._run_session_report
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "bd timed out after %ss" % timeout
    except OSError as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _run_json(args, timeout=DEFAULT_TIMEOUT_S, cwd=None):
    """Run bd expecting JSON. Returns (ok, parsed). ok=False on any failure."""
    rc, out, _err = _run(args, timeout=timeout, cwd=cwd)
    if rc != 0:
        return False, None
    try:
        return True, json.loads(out)
    except (ValueError, TypeError):
        return False, None


def in_progress(cwd=None):
    """Issues currently claimed.

    Returns ``{"available": bool, "issues": list}``. ``available`` is False when
    bd is missing, errored, or no workspace resolved — the caller must fail open
    in that case, not treat it as "nothing claimed".
    """
    ok, data = _run_json(["list", "--status", "in_progress", "--json"], cwd=cwd)
    if not ok or not isinstance(data, list):
        return {"available": False, "issues": []}
    return {"available": True, "issues": [i for i in data if isinstance(i, dict)]}


def list_all(cwd=None):
    """Every issue in the workspace, closed ones included, with their metadata.

    One call is enough for the whole audit: ``--all`` lifts the default
    open-issues filter and the rows carry ``metadata``, so there is no need for a
    per-status list or a ``bd show`` per issue. Same ``available`` contract as
    ``in_progress`` — False means *could not read*, which is not the same as an
    empty workspace and must never be reported as one.
    """
    ok, data = _run_json(["list", "--all", "--json"], cwd=cwd)
    if not ok or not isinstance(data, list):
        return {"available": False, "issues": []}
    return {"available": True, "issues": [i for i in data if isinstance(i, dict)]}


def show(issue_id, cwd=None):
    """One issue as a dict, or None.

    ``bd show --json`` returns a single-element *array*, not an object — an easy
    thing to get wrong once and then carry everywhere.
    """
    ok, data = _run_json(["show", str(issue_id), "--json"], cwd=cwd)
    if not ok:
        return None
    if isinstance(data, list):
        return data[0] if data and isinstance(data[0], dict) else None
    return data if isinstance(data, dict) else None


def _metadata_token(value):
    """Render a metadata value as a single argv token.

    A value containing whitespace would reach bd as two words and silently
    truncate, so lists/dicts are compacted and any remaining whitespace in a
    scalar is collapsed to underscores.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        text = json.dumps(value, separators=(",", ":"))
    else:
        text = str(value)
    return "_".join(text.split())


def set_metadata(issue_id, pairs, cwd=None):
    """Merge `pairs` into the issue's metadata. Returns True on success.

    Verified on bd 1.1.2: metadata merges rather than replaces, values round-trip
    with their JSON types (an int stays an int), and this works on a *closed*
    issue — which is what lets attribution be written after `bd close` lands
    rather than having to race ahead of it.
    """
    if not pairs:
        return True
    args = ["update", str(issue_id)]
    for key in sorted(pairs):
        args += ["--set-metadata", "%s=%s" % (key, _metadata_token(pairs[key]))]
    rc, _out, _err = _run(args, cwd=cwd)
    return rc == 0


def close(issue_id, reason=None, cwd=None):
    args = ["close", str(issue_id)]
    if reason:
        args += ["--reason", str(reason)]
    rc, _out, _err = _run(args, cwd=cwd)
    return rc == 0


def has_workspace(start_dir=None):
    """True if `start_dir` (or an ancestor) is inside a beads workspace.

    Walks upward because a hook's cwd is often a subdirectory of the repo root
    where ``.beads/`` lives. ``$BEADS_DIR`` short-circuits the walk, since a user
    who set it has explicitly pointed bd somewhere.
    """
    if os.environ.get("BEADS_DIR"):
        return True
    path = os.path.abspath(start_dir or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(path, ".beads")):
            return True
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent


def init(cwd=None, stealth=True, timeout=15):
    """Create a workspace in `cwd`. True only if bd can then read one back.

    A zero exit from ``bd init`` is not proof of a usable workspace: bd embeds a
    Dolt database, and a failure to open it surfaces on the first *read*. So
    success is defined as the condition the gate actually consults — ``bd list``
    resolving a database — which also means a half-created workspace reports
    failure and the session stays quiet instead of advertising a gate over
    nothing.

    ``--non-interactive`` and ``BD_NON_INTERACTIVE`` are both set: bd asks for an
    actor role when it believes a human is present, and a prompt inside a hook
    blocks until the event's timeout expires.
    """
    args = ["init"]
    if stealth:
        # Puts .beads into .git/info/exclude, so a workspace this plugin created
        # unprompted can never turn up in someone's commit.
        args.append("--stealth")
    args.append("--non-interactive")
    rc, _out, _err = _run(args, timeout=timeout, cwd=cwd,
                          env_extra={"BD_NON_INTERACTIVE": "1"})
    if rc != 0:
        return False
    return in_progress(cwd=cwd)["available"]


def prime(cwd=None):
    """`bd prime --hook-json` output for SessionStart passthrough, or None."""
    ok, data = _run_json(["prime", "--hook-json"], timeout=20, cwd=cwd)
    if not ok or not isinstance(data, dict):
        return None
    return data


def most_recent(issues):
    """The most recently updated issue from a list, or None.

    Used when several issues are claimed at once and attribution has to pick
    one: the latest ``updated_at`` is the best available guess at what the user
    is actually working on right now.
    """
    best = None
    best_key = ""
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        key = str(issue.get("updated_at") or issue.get("started_at") or "")
        if best is None or key > best_key:
            best, best_key = issue, key
    return best


def dolt_push(cwd=None):
    rc, _out, _err = _run(["dolt", "push"], timeout=60, cwd=cwd)
    return rc == 0


def dolt_sync(cwd=None):
    rc, _out, _err = _run(["dolt", "sync"], timeout=60, cwd=cwd)
    return rc == 0
