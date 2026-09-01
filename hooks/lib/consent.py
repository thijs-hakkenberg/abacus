#!/usr/bin/env python3
"""The acknowledgement that has to precede any unprompted action (adr/014).

abacus does four things nobody asked for in the moment it does them: it denies a
tool call, it creates a ``.beads/`` inside a repository, it writes ``abacus_*``
metadata onto an issue, and it can reach a remote as a session closes. Being
*installed* is not agreement to any of those — a plugin installed user-wide
inherits every repository the user opens, including ones they do not own.

Claude Code has no install hook, so the earliest surface available is the first
``SessionStart`` after the plugin loads, with ``UserPromptSubmit`` as the
next-prompt fallback. Neither can block, and neither should: consent obtained by
making the tool unusable is not consent. So the enforcement runs the other way
round — **until the governing settings are acknowledged, abacus performs no write
and no denial.** It observes, it says what it would do, and it does nothing.

Two things follow from that, and they are the whole of this module:

- *Which* settings need agreeing to. Only the ones that decide whether an
  unprompted action happens (``GOVERNING_KEYS``). A ccusage bump or a statusline
  toggle changes what abacus *says*, never what it *does*, and re-asking for
  those trains people to dismiss the notice unread.
- *When* consent goes stale. Widening ``auto_init.roots`` from ``~/projects`` to
  every git repository is a materially different thing to have agreed to, so the
  record carries a fingerprint of the governing settings and a change pauses
  governance until it is agreed again.

Consent gates *unprompted* action only. An explicit ``/abacus:audit fix`` or
``/abacus:task-start`` is itself the user acting, and keeps working.
"""

import hashlib
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import abacus_config  # noqa: E402
import state_store  # noqa: E402

ACK_BASENAME = "acknowledged.json"

# Every setting that decides whether abacus acts without being asked, and
# nothing else. Adding a key here invalidates every existing acknowledgement on
# every machine, which is correct when a genuinely new unprompted action lands
# and wrong for anything cosmetic.
GOVERNING_KEYS = (
    "gate.enabled",             # denies a tool call
    "gate.non_beads_project",   # denies in repositories with no workspace
    "auto_init.enabled",        # writes a .beads/ into a repository
    "auto_init.roots",          # ...and where it is allowed to
    "auto_init.stealth",        # ...and whether that write can reach a commit
    "sync_on_session_end",      # reaches a remote
)

# Schema version of the record itself. A record written by a future version
# declaring a schema this one does not know is treated as not-acknowledged: it is
# better to ask twice than to infer agreement from a document we cannot read.
SCHEMA = 1


def acknowledgement_path():
    return os.path.join(state_store.state_dir(), ACK_BASENAME)


def governing_settings(cfg=None):
    """The governing settings as a flat dotted-key dict.

    Read through ``abacus_config``'s accessors rather than off the raw dict, so
    the fingerprint covers the value the plugin will actually *act* on. An
    unreadable ``roots`` resolves to ``None`` there, and that is a distinct thing
    to have consented to — it means auto-init does nothing at all (adr/012).
    """
    cfg = cfg if cfg is not None else abacus_config.load_config()
    sync = cfg.get("sync_on_session_end")
    return {
        "gate.enabled": abacus_config.gate_enabled(cfg),
        "gate.non_beads_project": abacus_config.non_beads_mode(cfg),
        "auto_init.enabled": abacus_config.auto_init_enabled(cfg),
        "auto_init.roots": abacus_config.auto_init_roots(cfg),
        "auto_init.stealth": abacus_config.auto_init_stealth(cfg),
        "sync_on_session_end": sync if sync in ("push", "sync", "off") else "off",
    }


def _comparable(settings):
    """Settings reduced to what a *meaningful* change means.

    Only ``roots`` needs it: consent is to a set of directories, so reordering
    them grants nothing new and must not re-ask. Everything else compares as-is.
    """
    out = dict(settings)
    roots = out.get("auto_init.roots")
    if isinstance(roots, (list, tuple)):
        out["auto_init.roots"] = sorted(str(r) for r in roots)
    return out


def fingerprint(cfg=None):
    """A stable digest of the governing settings."""
    payload = json.dumps(_comparable(governing_settings(cfg)), sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_acknowledgement(path=None):
    """The stored record, or ``{}``.

    Unreadable, corrupt, not a dict, missing its fingerprint, or declaring a
    schema this version does not know all read as ``{}``. Unusually for this
    plugin the two safety directions coincide here: "no consent on file" means no
    denial *and* no write, so there is nothing to trade off.
    """
    try:
        with open(path or acknowledgement_path(), "r", encoding="utf-8") as f:
            record = json.load(f)
    except Exception:
        return {}
    if not isinstance(record, dict):
        return {}
    if not record.get("fingerprint"):
        return {}
    if record.get("schema") not in (None, SCHEMA):
        return {}
    return record


def status(cfg=None, path=None):
    """``"acknowledged"``, ``"never"``, or ``"changed"``.

    The three are distinguished because the notice has to read differently: a
    first-run explanation and a "this changed under you" are the same fact but
    not the same message, and collapsing them makes the second look like a bug.
    """
    record = load_acknowledgement(path)
    if not record:
        return "never"
    return "acknowledged" if record.get("fingerprint") == fingerprint(cfg) else "changed"


def is_acknowledged(cfg=None, path=None):
    """Whether abacus may act unprompted. The single question every hook asks."""
    return status(cfg, path) == "acknowledged"


def changed_keys(cfg=None, path=None):
    """Which governing settings differ from what was acknowledged.

    Empty when nothing was ever acknowledged — there is no diff to show against
    a record that does not exist, and inventing one would name settings the user
    never saw.
    """
    record = load_acknowledgement(path)
    if not record:
        return []
    was = record.get("settings")
    if not isinstance(was, dict):
        return list(GOVERNING_KEYS)
    was = _comparable(was)
    now = _comparable(governing_settings(cfg))
    return [k for k in GOVERNING_KEYS if was.get(k) != now.get(k)]


def acknowledge(cfg=None, path=None):
    """Record consent for the current governing settings. True on success.

    The settings are stored verbatim beside the fingerprint so that what was
    agreed to can be read back later without recomputing a hash — a record
    nobody can inspect is a checkbox, not a record.
    """
    target = path or acknowledgement_path()
    record = {
        "schema": SCHEMA,
        "fingerprint": fingerprint(cfg),
        "settings": governing_settings(cfg),
        "acknowledged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return _atomic_write(target, record)


def revoke(path=None):
    """Forget the acknowledgement, returning to the just-installed posture."""
    try:
        os.unlink(path or acknowledgement_path())
    except OSError:
        return False
    return True


def _atomic_write(target, record):
    """Same temp-file-and-replace discipline as ``state_store.save``.

    A half-written record would read as no consent, which is safe, but it would
    also silently discard an agreement the user just gave — so the write is
    atomic rather than merely tolerant of failure.
    """
    try:
        directory = os.path.dirname(target)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".acknowledged-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        return False
    return True


# ── the notice ──────────────────────────────────────────────────────────────
# Deliberately empty in the steady state. Enforcement that costs tokens on every
# prompt forever is the thing this plugin exists to avoid — the gate is
# mechanical precisely so it can be free until it fires, and a permanent banner
# would give that back.

def _render_roots(roots):
    if roots is None:
        return "nowhere — the configured roots could not be read, so auto-init does nothing"
    if not roots:
        return "any git repository"
    return ", ".join(roots)


def _what_it_would_do(settings):
    """The unprompted actions the current settings actually enable, one per line."""
    lines = []
    if settings.get("gate.enabled"):
        mode = settings.get("gate.non_beads_project")
        where = {
            "block": "in every project, including ones with no beads workspace",
            "warn": "in projects that have a beads workspace",
            "off": "in projects that have a beads workspace",
        }.get(mode, "in projects that have a beads workspace")
        lines.append("deny Edit/Write/NotebookEdit/MultiEdit when no beads task is "
                     "in progress, %s" % where)
    if settings.get("auto_init.enabled"):
        lines.append("create a .beads/ workspace in git repositories under: %s"
                     % _render_roots(settings.get("auto_init.roots")))
        if not settings.get("auto_init.stealth"):
            lines.append("...and NOT hide it from git, so it can reach a commit")
    lines.append("write abacus_* cost metadata onto the beads issue it attributes work to")
    sync = settings.get("sync_on_session_end")
    if sync in ("push", "sync"):
        lines.append("run `bd dolt %s` when a session ends, reaching your remote" % sync)
    return lines


def notice(cfg=None, path=None):
    """What to show the user, or ``""`` once acknowledged."""
    state = status(cfg, path)
    if state == "acknowledged":
        return ""

    settings = governing_settings(cfg)
    bullets = "\n".join("  · %s" % line for line in _what_it_would_do(settings))

    if state == "changed":
        changed = changed_keys(cfg, path)
        head = ("abacus: the settings that govern its behaviour changed since you "
                "agreed to them, so it is **not governing anything** until you agree "
                "again.\n\nChanged: %s" % ", ".join(changed))
    else:
        head = ("abacus is installed but **not governing anything yet**. It has not "
                "denied a tool call, created a workspace, or written to an issue, and "
                "it will not until you say so.")

    return (
        "%s\n\nOnce acknowledged, it will:\n%s\n\n"
        "Agree with  /abacus:acknowledge   ·   inspect first with  /abacus:status\n"
        "Change the settings in ~/.claude/abacus/config.json, or leave abacus "
        "unacknowledged and it stays inert."
    ) % (head, bullets)
