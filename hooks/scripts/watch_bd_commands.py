#!/usr/bin/env python3
"""PostToolUse Bash watcher — the attribution engine.

The gate decides whether work may proceed; this decides what that work cost. It
watches every Bash command for the two events that bound a unit of work:

    bd update <id> --claim   ->  take a ccusage baseline
    bd close <id>            ->  read ccusage again, diff, write the delta as
                                 abacus_* metadata onto the issue

Cost is a **difference between two readings of a cumulative session total**, never
a sum of per-message figures (adr/003). ccusage already walks the subagent
transcripts and deduplicates on ``(message.id, requestId)``, so the session total
includes fan-out work that a hook doing its own accounting would miss.

Three decisions are worth knowing before editing this file.

**The command is tokenised, not regex-matched.** ``shlex`` with
``punctuation_chars=True`` splits ``cd x && bd update t-1 --claim`` into segments
at ``&&``/``;``/``|`` and — crucially — leaves ``echo "bd update x --claim"`` as a
single quoted token, so it is *not* seen as a claim. A regex over the raw string
false-positives there and would silently mis-attribute an entire task. This is
still an approximation of a shell, not a shell: it does not expand variables,
follow ``bash -c``, or resolve aliases. Whatever it misses, the gate's lazy
snapshot and the Stop/SessionEnd repair passes cover.

**An unreadable cost is omitted, not zeroed.** If ccusage fails, the metadata
carries ``abacus_cost_basis=unavailable`` and no dollar figure at all. A ``$0.00``
against a task that took an hour is a wrong answer wearing the costume of a
measurement; an absent key prompts a question instead of a false conclusion.

**Attribution only ever moves to the task that owns it.** Closing an issue this
session never claimed writes no cost figure — the spend accrued under whatever
*is* claimed, and charging it to an unrelated issue because it happened to be
closed here would be worse than recording nothing.

Never blocks, never emits a permission decision, always exits 0.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import attribution  # noqa: E402
import beads  # noqa: E402
import ccusage  # noqa: E402
import consent  # noqa: E402
import hook_io  # noqa: E402
import state_store  # noqa: E402
import abacus_config  # noqa: E402
import abacus_time  # noqa: E402

# Subcommands that change nothing and so are not task boundaries. Listed as the
# things we *skip* rather than the things we act on, so an unfamiliar subcommand
# falls through to the (harmless) no-match path rather than being acted on.
READ_ONLY_SUBCOMMANDS = frozenset((
    "list", "show", "ready", "prime", "blocked", "stats", "search", "export",
    "dep", "deps", "diag", "version", "help", "config", "quickstart",
))

# Newline is deliberately absent: `whitespace_split` makes shlex classify it as
# whitespace, so listing it here never had any effect. Line separation is handled
# before tokenising, by _logical_lines.
_SEGMENT_SEPARATORS = frozenset(("&&", "||", ";", "|", "&"))

# `<<EOF`, `<<-EOF`, `<<'EOF'`, `<<"EOF"`. Requires a quote or an identifier
# after the operator so `<<<"herestring"` and `2>&1` do not match.
_HEREDOC_RE = re.compile(r"<<-?[ \t]*(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z_][A-Za-z0-9_]*))")


# ── command parsing ─────────────────────────────────────────────────────────

def _tokenise(text):
    """shlex tokens for one line. Raises ValueError on an unterminated quote.

    Whitespace-only tokens are dropped: shlex renders an escaped newline (a ``\\``
    line continuation) as a token containing just that newline, and a token that
    is only whitespace is never an argument. Left in, it becomes the first
    positional — so ``bd update \\<newline> <id> --claim`` would claim a task
    literally named "\\n", and the close that follows would match nothing.
    """
    import shlex

    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return [token for token in lexer if token.strip()]


def _logical_lines(command):
    """Split `command` into lines, one shell command per item where possible.

    A newline separates two commands exactly as ``;`` does, and ``cd <dir>`` on
    one line with the real command on the next is the commonest shape a
    multi-step Bash call takes — so the lines must come apart. But three shapes
    make a bare ``command.split("\\n")`` actively wrong:

    - a trailing ``\\`` continues the command onto the following line;
    - a quoted argument (a long ``--reason``) may itself contain newlines, and
      cutting mid-quote leaves a fragment shlex refuses — losing a real close;
    - a heredoc body is *data*. A runbook that documents ``bd close <id>`` must
      not be read as closing it, since a hallucinated boundary writes one task's
      cost onto another task's issue.
    """
    lines = command.split("\n")
    out, buffer, index = [], [], 0
    while index < len(lines):
        buffer.append(lines[index])
        index += 1
        candidate = "\n".join(buffer)
        if candidate.rstrip().endswith("\\"):
            continue  # explicit line continuation
        if index < len(lines) and not _is_tokenisable(candidate):
            continue  # an open quote — pull the next line in and retry
        buffer = []
        out.append(candidate)
        index = _skip_heredoc_bodies(candidate, lines, index)
    if buffer:
        out.append("\n".join(buffer))
    return out


def _is_tokenisable(text):
    try:
        _tokenise(text)
        return True
    except ValueError:
        return False


def _skip_heredoc_bodies(line, lines, index):
    """Return the index of the first line after any heredocs opened on `line`.

    An unterminated heredoc (no delimiter line before EOF) consumes the rest,
    which is what the shell itself would do with the redirection.
    """
    for match in _HEREDOC_RE.finditer(line):
        delimiter = match.group(1) or match.group(2) or match.group(3)
        while index < len(lines):
            reached = lines[index].strip() == delimiter
            index += 1
            if reached:
                break
    return index


def _segments(command):
    """Split a shell command line into simple-command token lists.

    A line that cannot be tokenised (e.g. an unterminated quote) contributes no
    segments, which correctly means "no bd invocation detected" — guessing at a
    malformed command's intent is how a watcher invents task boundaries that
    never happened.
    """
    segments = []
    for line in _logical_lines(command):
        try:
            tokens = _tokenise(line)
        except ValueError:
            continue
        current = []
        for token in tokens:
            if token in _SEGMENT_SEPARATORS:
                if current:
                    segments.append(current)
                current = []
            else:
                current.append(token)
        if current:
            segments.append(current)
    return segments


def _is_bd(token):
    """True if `token` invokes bd — bare, or by an absolute/relative path.

    Compares the basename so ``/opt/homebrew/bin/bd`` matches, while ``bdiff``
    and ``sbd`` do not.
    """
    return os.path.basename(token) == "bd"


def _strip_env_prefix(tokens):
    """Drop leading ``VAR=value`` assignments.

    ``CLAUDE_SESSION_ID=abc bd close x`` and ``BEADS_DIR=/other bd close x`` are
    both ordinary ways to invoke bd, and a watcher that only looks at token 0
    would see the assignment and conclude no bd ran — losing the attribution
    write for a task that really did close.
    """
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if "=" in token and not token.startswith("-") and token.split("=", 1)[0].isidentifier():
            index += 1
            continue
        break
    return tokens[index:]


def _flag_value(tokens, *names):
    """The value of the first of `names` present, supporting ``--flag=value``."""
    for i, token in enumerate(tokens):
        for name in names:
            if token == name and i + 1 < len(tokens):
                return tokens[i + 1]
            if token.startswith(name + "="):
                return token.split("=", 1)[1]
    return None


def _first_positional(tokens):
    """The first non-flag argument after the subcommand, or None.

    bd issue ids are opaque strings (``bd-a1b2``, ``bd-probe-boa``, a custom
    prefix), so the id is identified by *position* rather than by shape. Flags
    that take a value are skipped so ``--reason done bd-a1b2`` does not read
    "done" as the id.
    """
    valued_flags = ("--reason", "-r", "--reason-file", "--session", "--db",
                    "--actor", "--status", "-s", "--assignee", "-a")
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in valued_flags:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        return token
    return None


def _all_positionals(tokens):
    """Every non-flag argument after the subcommand (bd close accepts many)."""
    out = []
    remaining = list(tokens)
    while remaining:
        found = _first_positional(remaining)
        if found is None:
            break
        out.append(found)
        remaining = remaining[remaining.index(found) + 1:]
    return out


def parse_events(command):
    """Task boundaries in `command`, in execution order.

    Each is ``("claim", id_or_None)`` or ``("close", [ids])``. Order matters:
    ``bd update x --claim && bd close x`` must take the baseline before diffing
    against it, or the close sees no baseline at all.

    Note that ``bd update --status closed`` is a close and ``--status
    in_progress`` is a claim. Both were verified against bd 1.1.2 to have the
    same effect as the dedicated subcommands, and agents do use both spellings —
    watching only ``bd close`` loses those tasks' cost silently.
    """
    events = []
    for raw_tokens in _segments(command):
        tokens = _strip_env_prefix(raw_tokens)
        if len(tokens) < 2 or not _is_bd(tokens[0]):
            continue
        sub, rest = tokens[1], tokens[2:]
        if sub in READ_ONLY_SUBCOMMANDS:
            continue
        if sub in ("close", "done"):
            events.append(("close", _all_positionals(rest)))
        elif sub == "update":
            status = (_flag_value(rest, "--status", "-s") or "").lower()
            if status == "closed":
                events.append(("close", _all_positionals(rest)))
            elif "--claim" in rest or status == "in_progress":
                events.append(("claim", _first_positional(rest)))
    return events


# ── boundary handling ───────────────────────────────────────────────────────

def _resolve_claim_id(issue_id, cwd):
    """The id a claim refers to, asking bd when the command did not name one.

    ``bd update --claim`` with no id targets bd's last-touched issue. Rather
    than track that ourselves, ask what is in progress — after the claim has
    already run, that is the answer.
    """
    if issue_id:
        return issue_id
    status = beads.in_progress(cwd=cwd)
    if not status["available"]:
        return None
    issue = beads.most_recent(status["issues"])
    return str(issue.get("id")) if issue else None


def _handle_claim(session, issue_id, cwd, cfg):
    """Start attributing to `issue_id`, finalising whatever preceded it."""
    issue_id = _resolve_claim_id(issue_id, cwd)
    if not issue_id:
        return

    state = state_store.load(session)
    current = state.get("current_task")
    if current == issue_id and isinstance(state.get("snapshot"), dict):
        # bd's --claim is idempotent and agents do re-run it. Re-snapshotting
        # would move the baseline forward and silently discard the cost accrued
        # so far, so a repeat claim is a no-op.
        return

    if current:
        # Switching tasks without closing the first. Write out what it used
        # before the baseline moves, marked partial — the task is not finished,
        # only interrupted, and a later close accumulates onto this.
        attribution.finalise(session, current, cfg, partial=True, cwd=cwd)

    issue = beads.show(issue_id, cwd=cwd) or {}
    state_store.update(session, {
        "session_id": session,
        "current_task": issue_id,
        # The id came from a command the user actually ran, so it stands even if
        # the title lookup failed; only the statusline label is lost.
        "current_title": issue.get("title", ""),
        "claimed_at": abacus_time.now_iso(),
        "snapshot": ccusage.snapshot(session, cfg),
        "snapshot_source": "watch-claim",
    })


def _handle_close(session, issue_ids, cwd, cfg):
    """Attribute a close, but only for the task this session was tracking."""
    state = state_store.load(session)
    current = state.get("current_task")

    if not issue_ids:
        # `bd close` with no id closes bd's last-touched issue. In practice that
        # is the one this session claimed, and it is the only one we could
        # honestly attribute to anyway.
        issue_ids = [current] if current else []

    if current and current in issue_ids:
        attribution.finalise(session, current, cfg, partial=False, cwd=cwd)
        attribution.clear_current(session)
        return

    # Closing something else. The accrued spend belongs to `current`, which is
    # still open, so nothing is written and tracking continues untouched.
    for issue_id in issue_ids:
        hook_io.log("closed %s, which this session was not tracking; "
                    "no cost attributed" % issue_id)


def main():
    payload = hook_io.read_payload()
    if str(payload.get("tool_name") or "") != "Bash":
        return 0

    command = str((payload.get("tool_input") or {}).get("command") or "")
    # Cheap prefilter before anything else: most Bash calls in a session are not
    # bd, and they must cost a string scan rather than a subprocess.
    if "bd" not in command:
        return 0

    if abacus_config.is_disabled():
        return 0

    cfg = abacus_config.load_config()
    # Metadata on an issue is a write to the user's store of record, so it waits
    # for consent like everything else (adr/014). Nothing is buffered for later:
    # the boundaries missed while unacknowledged are simply not attributed, and
    # the audit pass reports them as gaps rather than inventing a figure.
    if not consent.is_acknowledged(cfg):
        return 0

    events = parse_events(command)
    if not events:
        return 0

    session = hook_io.session_id(payload)
    cwd = hook_io.payload_cwd(payload)

    for kind, target in events:
        if kind == "claim":
            _handle_claim(session, target, cwd, cfg)
        else:
            _handle_close(session, target, cwd, cfg)
    return 0


if __name__ == "__main__":
    hook_io.guard(main)
