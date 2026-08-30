"""``Then`` steps about injected context — the plugin's only token spend.

These assertions are deliberately about *size and content*, not about an exact
string. The compact primer exists because ``bd prime`` costs ~1,200 tokens on
every session start, resume and compaction; a scenario that pinned the exact
wording would break on every copy edit while letting the primer quietly grow back
to four kilobytes. So the character budget is asserted, and the content is
asserted by the commands a reader must be able to find in it.
"""

from pytest_bdd import parsers, then

from world import injected_context, world  # noqa: F401


@then(parsers.parse("the injected context is under {limit:d} characters"))
def step_under_limit(world, limit):
    context = injected_context(world)
    assert context, "nothing was injected"
    assert len(context) < limit, (
        "the primer is %d characters; every one is context the user did not ask "
        "for, on every session start, resume and compaction" % len(context))


@then("the injected context names the two commands that satisfy the gate")
def step_names_gate_commands(world):
    context = injected_context(world)
    assert "bd update <id> --claim" in context, "no way to claim existing work"
    assert 'bd create "<title>"' in context, "no way to start new work"


@then("the injected context names the task in progress")
def step_names_task(world):
    assert "ab-1" in injected_context(world)


@then("the injected context names the command that closes it")
def step_names_close(world):
    assert "bd close ab-1" in injected_context(world)


@then("the injected context contains the beads workflow manual")
def step_contains_manual(world):
    assert "BEADS WORKFLOW MANUAL" in injected_context(world), (
        "full mode must pass bd's own output through verbatim")


@then("the injected context is a single line")
def step_single_line(world):
    context = injected_context(world)
    assert context, "nothing was injected"
    assert "\n" not in context, (
        "this is prepended to the user's prompt once per turn: %r" % context)


@then("the injected context declares the SessionStart event")
def step_declares_session_start(world):
    data = world["result"].json or {}
    event = (data.get("hookSpecificOutput") or {}).get("hookEventName")
    assert event == "SessionStart", (
        "PreCompact injects into the post-compaction session, which Claude Code "
        "reads as SessionStart; got %r" % event)
