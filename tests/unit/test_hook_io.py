"""Coverage: hook_io — the envelope plumbing and the fail-open guard.

No dedicated unit test file existed for this module before this one; every
hook script exercises it as a side effect of running, but nothing pinned its
behaviour directly — including ``guard()``, which is the mechanism CLAUDE.md
calls "the plugin's whole safety story" (every script wraps its body in it so
an uncaught exception fails open rather than breaking the user's tool call).
That is exactly the kind of fail-open handler CLAUDE.md says deserves a real
test rather than a ``# pragma: no cover``.

These are in-process tests against the module directly: no subprocess, so no
hermeticity concern (hook_io.py touches only sys.stdin/stdout/stderr and
os.environ, all monkeypatched below).
"""

import io
import json

import pytest


@pytest.fixture
def hook_io(lib_path):
    import hook_io as module

    return module


# ── read_payload ─────────────────────────────────────────────────────────────


def test_read_payload_parses_a_json_object(hook_io, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"a": 1})))
    assert hook_io.read_payload() == {"a": 1}


def test_read_payload_empty_stdin_is_empty_dict(hook_io, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert hook_io.read_payload() == {}


def test_read_payload_whitespace_only_stdin_is_empty_dict(hook_io, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("   \n  "))
    assert hook_io.read_payload() == {}


def test_read_payload_malformed_json_is_empty_dict(hook_io, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    assert hook_io.read_payload() == {}


def test_read_payload_non_dict_json_is_empty_dict(hook_io, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps([1, 2, 3])))
    assert hook_io.read_payload() == {}


def test_read_payload_stdin_read_raising_is_empty_dict(hook_io, monkeypatch):
    class ExplodingStdin:
        def read(self):
            raise OSError("no tty")

    monkeypatch.setattr("sys.stdin", ExplodingStdin())
    assert hook_io.read_payload() == {}


# ── session_id / payload_cwd ────────────────────────────────────────────────


def test_session_id_prefers_snake_case(hook_io):
    assert hook_io.session_id({"session_id": "s1", "sessionId": "s2"}) == "s1"


def test_session_id_falls_back_to_camel_case(hook_io):
    assert hook_io.session_id({"sessionId": "s2"}) == "s2"


def test_session_id_falls_back_to_unknown(hook_io):
    assert hook_io.session_id({}) == "unknown"


def test_payload_cwd_uses_cwd_key_when_it_is_a_real_directory(hook_io, tmp_path):
    assert hook_io.payload_cwd({"cwd": str(tmp_path)}) == str(tmp_path)


def test_payload_cwd_falls_back_to_project_dir(hook_io, tmp_path):
    assert hook_io.payload_cwd({"project_dir": str(tmp_path)}) == str(tmp_path)


def test_payload_cwd_falls_back_to_projectDir_camel_case(hook_io, tmp_path):
    assert hook_io.payload_cwd({"projectDir": str(tmp_path)}) == str(tmp_path)


def test_payload_cwd_skips_a_value_that_is_not_a_real_directory(hook_io, tmp_path):
    missing = str(tmp_path / "does-not-exist")
    assert hook_io.payload_cwd({"cwd": missing}) == __import__("os").getcwd()


def test_payload_cwd_defaults_to_getcwd_when_nothing_present(hook_io):
    import os

    assert hook_io.payload_cwd({}) == os.getcwd()


def test_payload_cwd_expands_user(hook_io, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "sub").mkdir()
    assert hook_io.payload_cwd({"cwd": "~/sub"}) == str(tmp_path / "sub")


# ── log / emit ───────────────────────────────────────────────────────────────


def test_log_writes_a_prefixed_line_to_stderr(hook_io, capsys):
    hook_io.log("hello")
    captured = capsys.readouterr()
    assert captured.err == "[abacus] hello\n"
    assert captured.out == ""


def test_log_swallows_a_write_failure(hook_io, monkeypatch):
    class ExplodingStderr:
        def write(self, _text):
            raise OSError("broken pipe")

        def flush(self):
            pass

    monkeypatch.setattr("sys.stderr", ExplodingStderr())
    hook_io.log("hello")  # must not raise


def test_emit_writes_json_to_stdout_only(hook_io, capsys):
    hook_io.emit({"a": 1})
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"a": 1}
    assert captured.err == ""


def test_emit_swallows_a_write_failure(hook_io, monkeypatch):
    class ExplodingStdout:
        def write(self, _text):
            raise OSError("broken pipe")

        def flush(self):
            pass

    monkeypatch.setattr("sys.stdout", ExplodingStdout())
    hook_io.emit({"a": 1})  # must not raise


# ── deny / additional_context ───────────────────────────────────────────────


def test_deny_emits_a_permission_decision_envelope(hook_io, capsys):
    hook_io.deny("no task in progress")
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["hookSpecificOutput"] == {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "no task in progress",
    }


def test_deny_accepts_a_different_event_name(hook_io, capsys):
    hook_io.deny("reason", event="SomeOtherEvent")
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["hookSpecificOutput"]["hookEventName"] == "SomeOtherEvent"


def test_additional_context_emits_when_text_present(hook_io, capsys):
    hook_io.additional_context("primer text")
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["hookSpecificOutput"] == {
        "hookEventName": "SessionStart",
        "additionalContext": "primer text",
    }


def test_additional_context_emits_nothing_for_empty_text(hook_io, capsys):
    hook_io.additional_context("")
    captured = capsys.readouterr()
    assert captured.out == ""


# ── guard ────────────────────────────────────────────────────────────────────


def test_guard_exits_zero_when_main_returns_nothing(hook_io):
    with pytest.raises(SystemExit) as exc:
        hook_io.guard(lambda: None)
    assert exc.value.code == 0


def test_guard_exits_with_mains_return_value(hook_io):
    with pytest.raises(SystemExit) as exc:
        hook_io.guard(lambda: 5)
    assert exc.value.code == 5


def test_guard_lets_systemexit_propagate_unconverted(hook_io):
    def main():
        raise SystemExit(3)

    with pytest.raises(SystemExit) as exc:
        hook_io.guard(main)
    assert exc.value.code == 3


def test_guard_fails_open_on_an_unexpected_exception(hook_io, monkeypatch, capsys):
    monkeypatch.delenv("ABACUS_DEBUG", raising=False)

    def main():
        raise ValueError("boom")

    with pytest.raises(SystemExit) as exc:
        hook_io.guard(main)

    assert exc.value.code == 0
    assert "internal error (failing open)" in capsys.readouterr().err


def test_guard_reraises_when_abacus_debug_is_set(hook_io, monkeypatch):
    monkeypatch.setenv("ABACUS_DEBUG", "1")

    def main():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        hook_io.guard(main)
