"""Step definitions for `features/commit-capture.feature`.

The git stub is what makes these scenarios both offline and honest. Capture never
parses the Bash command's stdout for a sha — it asks git — so a scenario has to be
able to say "HEAD is here and these commits appeared", which is exactly what
planting canned answers per `git` subcommand expresses. Driving a real repository
would additionally test git, which is not the thing under test and would make the
suite depend on a binary's version.

Commit shas are written in the feature as short hex names (`ba5e`, `c0ffee`) and
expanded here by repetition, so a scenario reads as prose while the sha12 in the
resulting metadata key stays derivable from what the scenario said.

Timestamps are expressed as *before* or *after the claim* rather than as epochs.
That is the relation rail 2 actually checks, and an epoch literal in a feature file
would be a number a reader has to decode before the scenario means anything.
"""

import calendar
import time

from pytest_bdd import given, parsers, then, when

from conftest import post_bash_payload
from when_steps import step_run
from world import world  # noqa: F401 — fixture must be importable from here

# The instant `world.track_task` claims at. Read from there rather than restated
# loosely, because every "before"/"after" in this file is relative to it.
CLAIMED_AT = "2026-08-06T09:00:00Z"
CLAIMED_EPOCH = calendar.timegm(time.strptime(CLAIMED_AT, "%Y-%m-%dT%H:%M:%SZ"))

SEP = "\x1f"  # gitlog._SEP — the field separator asked of --pretty=format


def sha_of(name):
    """A full 40-hex sha from the short name a scenario used."""
    return (name * 40)[:40]


def _log_line(name, epoch, subject="do the work", declares=""):
    return SEP.join([sha_of(name), str(epoch), subject, declares])


def _plant_log(world, lines):
    world["harness"].set_git("log", stdout="\n".join(lines))


# ══ Given: the repository ═══════════════════════════════════════════════════

@given("the working directory is a git repository")
def step_git_repo(world):
    harness = world["harness"]
    harness.make_git_project(world["cwd"])
    # `--show-toplevel` rather than the hook's cwd is what keys the watermark, so
    # the stub has to answer it: a commit made from a subdirectory must measure
    # against the same mark as one made from the root.
    harness.set_git("rev-parse.--show-toplevel", stdout="%s\n" % world["cwd"])
    # An empty log by default. A scenario that plants no commits is asserting the
    # quiet path, and inheriting a previous scenario's commits would make that
    # assertion pass for the wrong reason.
    harness.set_git("log", stdout="")


@given(parsers.parse('HEAD is at commit "{name}"'))
def step_head_at(world, name):
    world["harness"].set_git("rev-parse.HEAD", stdout="%s\n" % sha_of(name))


@given(parsers.parse('the watermark for this repository is "{name}"'))
def step_watermark_is(world, name):
    """Merges into whatever state a preceding Given already wrote.

    Ordering matters and the scenarios respect it: `track_task` and "no task is being
    tracked" both *replace* the state file, so this step has to run after them. It
    merges rather than replaces so that it does not in turn undo the claim.
    """
    harness = world["harness"]
    state = harness.read_state(world["session"]) or {}
    state["session_id"] = world["session"]
    state["head_watermarks"] = {str(world["cwd"]): sha_of(name)}
    harness.write_state(world["session"], state)


@given("no watermark has been recorded for this repository")
def step_no_watermark(world):
    world["harness"].write_state(world["session"], {"session_id": world["session"]})


@given("the git executable is not on PATH")
def step_no_git(world):
    world["harness"].remove_git()


# ══ Given: the commits that appeared ════════════════════════════════════════
#
# These touch only the git stub, never session state, so they are order-independent
# with respect to the claim and the watermark above.

@given(parsers.parse('a commit "{name}" was made after the claim'))
def step_commit_after(world, name):
    _plant_log(world, [_log_line(name, CLAIMED_EPOCH + 600)])


@given(parsers.parse('a commit "{name}" was made before the claim'))
def step_commit_before(world, name):
    # The `git pull` case: HEAD moves, but every commit predates the claim, so
    # none of them can have been observed being made during it.
    _plant_log(world, [_log_line(name, CLAIMED_EPOCH - 86400)])


@given(parsers.parse('a commit "{name}" was made after the claim declaring "{ids}"'))
def step_commit_declaring(world, name, ids):
    # Rendered as git renders it: repeated trailer lines and one line naming three
    # tasks both arrive as a single comma-separated field.
    _plant_log(world, [_log_line(name, CLAIMED_EPOCH + 600,
                                 subject="close three", declares=ids)])


@given(parsers.parse("{count:d} commits were made after the claim"))
def step_many_commits(world, count):
    _plant_log(world, [
        _log_line("%06x" % (i + 1), CLAIMED_EPOCH + 600 + i) for i in range(count)])


# ══ When: the one shape the shared step cannot express ══════════════════════

@when(parsers.parse('the Bash watcher observes a heredoc documenting "{command}"'))
def step_watcher_heredoc(world, command):
    """A commit command quoted inside a heredoc body is data, not a boundary.

    Written as its own step because the multi-line body cannot be spelled on one
    Gherkin line. The outer command is `cat`, deliberately: if the tokeniser ever
    stopped skipping heredoc bodies, the assertion that git was never invoked
    fails, which is the failure this scenario exists to catch.
    """
    command = command.replace('\\"', '"')
    body = "cat <<'EOF' > notes.md\nTo record the work, run:\n%s\nEOF" % command
    step_run(world, "watch_bd_commands.py",
             post_bash_payload(body, session_id=world["session"], cwd=world["cwd"]))


# ══ Then: the edges ═════════════════════════════════════════════════════════

def _commit_writes(world):
    """Every ``abacus_commit_*`` pair written, as (issue_id, sha12, basis)."""
    out = []
    for call in world["harness"].bd_calls():
        parts = call.split()
        if "--set-metadata" not in parts:
            continue
        target = parts[2] if len(parts) > 2 else ""
        for i, part in enumerate(parts):
            if part != "--set-metadata" or i + 1 >= len(parts):
                continue
            key, _, value = parts[i + 1].partition("=")
            if not key.startswith("abacus_commit_"):
                continue
            out.append((target, key[len("abacus_commit_"):], value.split(":")[0]))
    return out


@then(parsers.parse('a commit edge for "{name}" is written to "{issue_id}" with basis "{basis}"'))
def step_edge_written(world, name, issue_id, basis):
    sha12 = sha_of(name)[:12]
    written = _commit_writes(world)
    assert (issue_id, sha12, basis) in written, (
        "expected %s to carry %s with basis %s; edges written were %s"
        % (issue_id, sha12, basis, written))


@then("no commit edge is written")
def step_no_edge(world):
    assert _commit_writes(world) == [], (
        "expected no commit edge, got %s" % (_commit_writes(world),))


@then(parsers.parse('the watermark for this repository is recorded as "{name}"'))
def step_watermark_recorded(world, name):
    state = world["harness"].read_state(world["session"]) or {}
    marks = state.get("head_watermarks") or {}
    assert marks.get(str(world["cwd"])) == sha_of(name), (
        "expected the watermark at %s, got %r" % (sha_of(name)[:12], marks))


@then("git is never invoked")
def step_no_git_calls(world):
    assert world["harness"].git_calls() == [], (
        "this path must not spawn git: %s" % world["harness"].git_calls())
