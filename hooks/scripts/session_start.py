#!/usr/bin/env python3
"""SessionStart / PreCompact — prime the agent and baseline the session.

This is the only hook in the plugin that spends tokens, which makes it the only
place where "enforce tracking without spending tokens on enforcement" can be
violated. Measured on bd 1.1.2, ``bd prime --hook-json`` emits 4,854 characters
(~1,200 tokens) of workflow manual into *every* session. The gate does not need
any of it — enforcement is mechanical, and an agent that has not read a word of
beads documentation still cannot edit without a claimed task.

So the default is a compact primer: what the gate does, and the two commands that
satisfy it. That is the whole actionable content of those 1,200 tokens, at under
a tenth of the size (adr/009). ``prime.mode: "full"`` passes bd's own manual
through verbatim for users who want it, and ``"off"`` emits nothing.

The other job is the cost baseline, and the rule there is that **priming must
never reset attribution**. This hook fires on resume and after compaction as well
as at startup; ccusage totals are cumulative per session id, so an existing
baseline still diffs correctly and replacing it would silently discard everything
the current task had spent. A baseline is only ever created, never overwritten.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import beads  # noqa: E402
import ccusage  # noqa: E402
import consent  # noqa: E402
import hook_io  # noqa: E402
import state_store  # noqa: E402
import abacus_config  # noqa: E402
import abacus_time  # noqa: E402

COMPACT_PRIMER = """abacus: this project tracks work as beads tasks. Edit/Write are blocked unless a task is in progress.
  claim existing:  bd ready --json   then  bd update <id> --claim --json
  start new:       bd create "<title>" --silent   then  bd update <id> --claim --json
Cost, tokens and duration are attributed to whichever task is claimed."""

ACTIVE_TEMPLATE = """abacus: task {issue_id} is in progress{title}; edits are attributed to it.
Close it with `bd close {issue_id}` when the work is done."""


def _beads_plugin_installed():
    """True if a beads-branded plugin is enabled in the user's settings.

    beads ships its own SessionStart primer. Emitting ours alongside it would put
    two overlapping instruction sets in the same context, so we defer entirely
    rather than try to merge them.
    """
    import json

    for name in ("settings.json", "settings.local.json"):
        path = os.path.join(os.path.expanduser("~"), ".claude", name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        enabled = (data or {}).get("enabledPlugins") or {}
        for key, value in enabled.items():
            if value and str(key).split("@", 1)[0] == "beads":
                return True
    return False


def _primer(cfg, cwd, event):
    """The context to inject, or "" for silence."""
    prime_cfg = cfg.get("prime") or {}
    mode = str(prime_cfg.get("mode") or "compact").lower()
    if mode == "off" or prime_cfg.get("enabled") is False:
        return ""
    if _beads_plugin_installed():
        return ""

    if mode == "full":
        data = beads.prime(cwd=cwd) or {}
        context = ((data.get("hookSpecificOutput") or {}).get("additionalContext") or "")
        if context:
            return context
        # bd could not be primed; the compact primer is still better than nothing.

    status = beads.in_progress(cwd=cwd)
    if status["available"] and status["issues"]:
        issue = beads.most_recent(status["issues"]) or {}
        title = issue.get("title") or ""
        return ACTIVE_TEMPLATE.format(
            issue_id=issue.get("id", "?"),
            title=' ("%s")' % title if title else "",
        )
    return COMPACT_PRIMER


def _within(path, root):
    """True if `path` is `root` or beneath it.

    Compares realpaths with a trailing separator: plain string containment reads
    ``/a/projects-old`` as inside ``/a/projects``, which would silently widen the
    configured scope by one careless sibling directory.
    """
    try:
        root = os.path.realpath(os.path.expanduser(str(root)))
    except Exception:
        return False
    if not root or root == os.sep:
        return False
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _eligible_for_auto_init(cwd, cfg):
    """Whether a workspace may be created in `cwd`. Every step here says no."""
    try:
        path = os.path.realpath(cwd)
    except Exception:
        return False
    if not os.path.isdir(path):
        return False
    # A git root is the only project boundary this accepts: it is an explicit
    # statement that these files are one unit of work, and --stealth needs
    # .git/info/exclude to keep the workspace uncommittable. Test for existence
    # rather than isdir — inside a worktree or submodule, .git is a file.
    if not os.path.exists(os.path.join(path, ".git")):
        return False
    # $HOME and / are never a project. A dotfiles repo at $HOME would otherwise
    # get a workspace that every session anywhere beneath it walks up and finds,
    # gating unrelated work under one task list.
    for forbidden in ("~", os.sep):
        try:
            if path == os.path.realpath(os.path.expanduser(forbidden)):
                return False
        except Exception:
            return False
    roots = abacus_config.auto_init_roots(cfg)
    if roots is None:
        return False
    if not roots:
        return True  # an explicit empty list means every git repository
    return any(_within(path, root) for root in roots)


def _auto_init(cwd, cfg):
    """Give `cwd` a beads workspace if configuration and the rails both allow it.

    Returns True only when bd can read the workspace back, so the caller can
    treat it exactly as it would a workspace that was already there.

    Costs about 3s (measured, bd 1.1.2, fresh git repo) against SessionStart's
    20s budget, and only ever in a project that has no workspace — so once per
    project, not once per session.
    """
    if not abacus_config.auto_init_enabled(cfg):
        return False
    if not _eligible_for_auto_init(cwd, cfg):
        return False
    created = beads.init(cwd, stealth=abacus_config.auto_init_stealth(cfg))
    hook_io.log("auto-init %s: %s" % ("ok" if created else "failed", cwd))
    return created


def _baseline(session, cwd, cfg, source):
    """Create the session's state and baseline. Never overwrites an existing one."""
    state = state_store.load(session)
    changes = {"session_id": session}
    if not state.get("started_at"):
        changes["started_at"] = abacus_time.now_iso()

    already_tracking = bool(state.get("current_task")) and isinstance(state.get("snapshot"), dict)
    if not already_tracking:
        status = beads.in_progress(cwd=cwd)
        issue = beads.most_recent(status["issues"]) if status["available"] else None
        if issue:
            # A task claimed before this session opened (another terminal, an
            # earlier session) still needs a baseline, or its cost would only
            # begin counting at the first edit this session happens to make.
            changes.update({
                "current_task": str(issue.get("id") or ""),
                "current_title": issue.get("title", ""),
                "claimed_at": abacus_time.now_iso(),
                "snapshot": ccusage.snapshot(session, cfg),
                "snapshot_source": "session-start-adopt",
            })
    state_store.update(session, changes)


def main():
    payload = hook_io.read_payload()
    precompact = "--precompact" in sys.argv[1:]
    event = "PreCompact" if precompact else "SessionStart"

    if abacus_config.is_disabled():
        return 0

    cwd = hook_io.payload_cwd(payload)
    cfg = abacus_config.load_config()

    # Claude Code has no plugin-install hook, so this is the earliest surface
    # after an install — and the last one before auto_init would write to a
    # repository. Until the settings are acknowledged, say what abacus *would* do
    # and do none of it (adr/014). The primer is skipped deliberately: it
    # describes enforcement, and describing enforcement that is paused would
    # spend tokens on a false statement.
    if not consent.is_acknowledged(cfg):
        session = hook_io.session_id(payload)
        # Recorded so the UserPromptSubmit surface stays quiet: one ask per
        # session, whichever hook gets there first.
        state_store.update(session, {"consent_asked_at": abacus_time.now_iso()})
        hook_io.additional_context(consent.notice(cfg), event="SessionStart")
        return 0

    if not beads.has_workspace(cwd):
        # PreCompact is the middle of a session, not the start of a project, so
        # it never creates anything.
        if precompact or not _auto_init(cwd, cfg):
            # Nothing to enforce here, so nothing to say. Spending tokens
            # describing a tracker this repo does not use is pure waste.
            return 0

    session = hook_io.session_id(payload)

    if not precompact:
        _baseline(session, cwd, cfg, str(payload.get("source") or "startup"))
        # Nothing else ever cleans these up; without this the state directory
        # grows by one file per session forever.
        state_store.prune(int(cfg.get("state_max_age_days") or 14))

    # PreCompact declares SessionStart: the context is injected into the
    # post-compaction session, which is what Claude Code reads it as.
    hook_io.additional_context(_primer(cfg, cwd, event), event="SessionStart")
    return 0


if __name__ == "__main__":
    hook_io.guard(main)
