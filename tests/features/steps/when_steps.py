"""The ``When`` steps — each one runs a real hook script as a subprocess.

Nothing is called in-process. Claude Code invokes these scripts with JSON on
stdin and reads stdout, so a test that imported and called ``main()`` would miss
an import error, a stray ``print`` corrupting the envelope, or a non-zero exit
that only appears under the real entrypoint. Driving the subprocess is the whole
point: these scenarios verify the hook as the platform actually runs it.
"""

from pytest_bdd import parsers, when

from conftest import post_bash_payload, pre_tool_payload, session_payload
from world import world  # noqa: F401 — fixture must be importable from here

_GATED_TOOL_WORDS = {"an Edit": "Edit", "a Write": "Write", "a Read": "Read",
                     "a NotebookEdit": "NotebookEdit"}


def step_run(world, script, payload, extra_args=()):
    harness = world["harness"]
    if world["raw_stdin"] is not None:
        world["result"] = harness.run_hook_raw(
            script, world["raw_stdin"], cwd=world["cwd"],
            extra_args=extra_args, **world["env"])
    else:
        payload.update(world["payload_extra"])
        world["result"] = harness.run_hook(
            script, payload, cwd=world["cwd"],
            extra_args=extra_args, **world["env"])
    return world["result"]


@when(parsers.parse("the PreToolUse gate runs for {tool_phrase}"))
def step_run_gate(world, tool_phrase):
    step_run(world, "gate_edits.py",
              pre_tool_payload(session_id=world["session"],
                          tool=_GATED_TOOL_WORDS[tool_phrase],
                          cwd=world["cwd"]))


@when(parsers.parse('the Bash watcher observes "{command}"'))
def step_run_watcher(world, command):
    # Gherkin escapes the inner quotes of a nested command; unescape so the
    # tokeniser sees exactly what the shell would have.
    command = command.replace('\\"', '"')
    step_run(world, "watch_bd_commands.py",
              post_bash_payload(command, session_id=world["session"], cwd=world["cwd"]))


@when(parsers.parse('the Bash watcher observes "{first}" and "{second}" on separate lines'))
def step_run_watcher_multiline(world, first, second):
    """Two commands separated by a newline — the commonest multi-step Bash shape.

    Spelled as two quoted strings because a Gherkin step is one line; the step
    joins them with the newline the shell would have seen.
    """
    step_run_watcher(world, first + "\n" + second)


@when("the SessionStart hook runs")
def step_run_session_start(world):
    step_run(world, "session_start.py",
              session_payload(session_id=world["session"], source="startup", cwd=world["cwd"]))


@when("the SessionStart hook runs for a resumed session")
def step_run_session_resume(world):
    step_run(world, "session_start.py",
              session_payload(session_id=world["session"], source="resume", cwd=world["cwd"]))


@when("the PreCompact hook runs")
def step_run_precompact(world):
    step_run(world, "session_start.py",
              session_payload(session_id=world["session"], source="compact",
                         event="PreCompact", cwd=world["cwd"]),
              extra_args=("--precompact",))


@when("the UserPromptSubmit hook runs")
def step_run_statusline(world):
    step_run(world, "prompt_statusline.py",
              session_payload(session_id=world["session"], source="",
                         event="UserPromptSubmit", cwd=world["cwd"]))


@when("the Stop hook runs")
def step_run_stop(world):
    step_run(world, "stop_reconcile.py",
              session_payload(session_id=world["session"], source="",
                         event="Stop", cwd=world["cwd"]))


@when("the SessionEnd hook runs")
def step_run_session_end(world):
    step_run(world, "session_end.py",
              session_payload(session_id=world["session"], source="",
                         event="SessionEnd", cwd=world["cwd"]))
