# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-15: the scripted brain and the replay brain (ADR 0028 rule 1 and I1; ADR 0026 rule 14 step
1; contract docs/phase4/api-surface-batch-c.md sections 6 and 7).

Scripted: the next line is the next ``scripted_turns`` entry by ``index``, and nothing else is
asked of anyone. Replay (``--caller fake:<dir>``): the line of turn N is read from
``turn-NNNN.json`` (four digits, from 0001); every file the call could need is checked, and read,
when the brain is built, so a missing file is a refusal before any key is read or any socket
opened. The fixture of the tests is the invented Marathi file ``sim_mr.json`` of P4-00f.
"""

from __future__ import annotations

import inspect
import json
import os
import socket
from pathlib import Path
from typing import Any

import pytest

import evalcore_phase4_batchc_support as sup  # type: ignore[import-not-found,unused-ignore]
from tracer_evalschema.evaluator import ScriptedTurn

CAP = 300


def brains() -> Any:
    return sup.mod("brains")


def scripted_turns(lines: list[str]) -> list[ScriptedTurn]:
    return [ScriptedTurn(index=i, text=t, language="mr") for i, t in enumerate(lines)]


def scenario_lines(number: int) -> list[str]:
    return [t["text"] for t in sup.sim_mr()["scenarios"][number]["scripted_turns"]]


def write_replay(
    directory: Path, lines: list[str], *, start: int = 1, raw: dict[int, bytes] | None = None
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for offset, line in enumerate(lines):
        name = brains().REPLAY_FILE_NAME.format(start + offset)
        (directory / name).write_text(json.dumps({"line": line}), "utf-8")
    for number, content in (raw or {}).items():
        (directory / brains().REPLAY_FILE_NAME.format(number)).write_bytes(content)
    return directory


def replay(directory: Path | str, *, max_turns: int, **kwargs: Any) -> Any:
    params: dict[str, Any] = {"language": "mr", "max_turns": max_turns, "max_line_chars": CAP}
    params.update(kwargs)
    return brains().ReplayBrain(directory, **params)


# ------------------------------------------------------------------ the protocol (rule 1)


def test_brains_have_the_shape_of_a_caller_brain_and_a_mode_of_adr_0027(tmp_path: Path) -> None:
    """Rule 1: the three brains are `CallerBrain`s and their modes are the three
    `caller_text_origin` values in order."""
    core = sup.mod("brain")
    simtypes = sup.mod("simtypes")
    one = brains().ScriptedBrain(scripted_turns([sup.LINE_A]))
    two = replay(write_replay(tmp_path / "r", [sup.LINE_A]), max_turns=1)
    three = sup.make_llm_brain(sup.FakeLlm([]))
    assert [b.mode for b in (one, three, two)] == list(simtypes.CALLER_MODES)
    for brain in (one, two, three):
        assert isinstance(brain, core.CallerBrain)
        assert core.require_caller_brain(brain) is brain
    assert set(brains().__all__) >= {
        "LlmBrain",
        "REPLAY_FILE_MAX_BYTES",
        "REPLAY_FILE_NAME",
        "ReplayBrain",
        "ReplayDirectoryError",
        "ScriptedBrain",
    }


# --------------------------------------------------------------------------- scripted brain


def test_scripted_mode_makes_no_llm_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR 0028 rule 1 and I1: a scripted call never asks a model for anything. The scripted
    brain has no engine to ask (its only parameter is the script), the LLM brain and the engine
    driver are rigged to fail the test if they are reached, and the whole fixture is played."""
    parameters = inspect.signature(brains().ScriptedBrain).parameters
    assert list(parameters) == ["scripted_turns"]
    assert parameters["scripted_turns"].default is inspect.Parameter.empty

    llm = sup.mod("llm")

    def trip(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("an LLM request was attempted in scripted mode")

    monkeypatch.setattr(llm, "complete_once", trip)
    monkeypatch.setattr(brains(), "complete_once", trip, raising=False)
    monkeypatch.setattr(brains().LlmBrain, "__init__", trip)
    monkeypatch.setattr(brains().LlmBrain, "next_line", trip)

    for number in range(3):
        lines = scenario_lines(number)
        brain = brains().ScriptedBrain(scripted_turns(lines))
        tts = sup.RecordingTts()
        decisions = sup.drive(brain, [None] * (len(lines) + 1), tts)
        assert tts.texts == lines
        assert [d.line for d in decisions[:-1]] == lines
        assert (decisions[-1].line, decisions[-1].end_reason) == (None, "script_complete")
        assert all((d.invalid_outputs, d.regenerations) == (0, 0) for d in decisions)
    assert [len(scenario_lines(n)) for n in range(3)] == [10, 10, 8]

    # the source of the class names no engine, no request and no model
    source = sup.module_source("brains")
    tree_names = sup.forbidden_in_source(
        _class_source(source, "ScriptedBrain"),
        ("complete_once", "LlmEngine", "LlmRequest", "LlmContext", "LlmBrain", "UsageSink"),
    )
    assert tree_names == []


def _class_source(source: str, name: str) -> str:
    import ast

    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"no class {name}")


def test_scripted_brain_returns_the_entry_of_the_index_and_ignores_the_agent() -> None:
    """Rule 1: scripted lines come by index, in any order, whatever the agent text; the end is
    `script_complete`."""
    lines = scenario_lines(0)
    brain = brains().ScriptedBrain(scripted_turns(lines))
    # random access: the same index gives the same decision, in any order, with any agent text
    for index in (3, 0, 9, 3, 1):
        for agent in (None, "", sup.AGENT_A, sup.HOSTILE_AGENT_TEXTS["ignore previous"]):
            decision = brain.next_line(index, agent)
            assert decision.line == lines[index]
            assert decision.end_reason is None
            assert (decision.invalid_outputs, decision.regenerations) == (0, 0)
    for index in (10, 11, 500):
        decision = brain.next_line(index, None)
        assert (decision.line, decision.end_reason) == (None, "script_complete")
    empty = brains().ScriptedBrain([])
    assert empty.next_line(0, None).end_reason == "script_complete"


def test_scripted_brain_does_not_judge_the_lines_it_is_given() -> None:
    """Scripted lines are the evaluator's own and are synthesised before the call (rule 1);
    the output validator is for model and replay lines. The TTS adapter refuses what it cannot
    speak (P4-08)."""
    english = ScriptedTurn(index=0, text="Hello, I need help with my bill.", language="mr")
    brain = brains().ScriptedBrain([english])
    assert brain.next_line(0, None).line == "Hello, I need help with my bill."


def test_scripted_brain_checks_the_script_when_it_is_built() -> None:
    """ADR 0026 rule 14 step 1: a script with gaps, duplicates or an over-long line is refused
    before the call."""
    turns = scripted_turns(["\u0915", "\u0916", "\u0917"])
    for bad in (
        [turns[1], turns[0]],
        [turns[0], turns[2]],
        [turns[0], turns[0]],
        [turns[1]],
    ):
        with pytest.raises(ValueError, match=r"\S"):
            brains().ScriptedBrain(bad)
    for not_turns in ([{"index": 0, "text": "x"}], ["x"], [None], 5, None):
        with pytest.raises(TypeError):
            brains().ScriptedBrain(not_turns)
    too_long = [ScriptedTurn(index=0, text="\u0915" * 2501, language="mr")]
    with pytest.raises(ValueError, match=r"\S"):
        brains().ScriptedBrain(too_long)
    brains().ScriptedBrain([ScriptedTurn(index=0, text="\u0915" * 2500, language="mr")])


def test_scripted_brain_keeps_its_own_copy_of_the_script() -> None:
    """The script is fixed when the brain is built: changing the list afterwards changes
    nothing."""
    turns = scripted_turns([sup.LINE_A, sup.LINE_B])
    brain = brains().ScriptedBrain(turns)
    turns.append(ScriptedTurn(index=2, text=sup.LINE_C, language="mr"))
    turns[0] = ScriptedTurn(index=0, text=sup.LINE_D, language="mr")
    assert [brain.next_line(i, None).line for i in range(2)] == [sup.LINE_A, sup.LINE_B]
    assert brain.next_line(2, None).end_reason == "script_complete"


def test_replay_brain_accepts_the_largest_max_turns_the_schema_allows(tmp_path: Path) -> None:
    """ADR 0027: ``max_turns`` is at most 2**53 - 1; a replay that ends with the sentinel in its
    first file needs no more files, whatever the cap."""
    directory = write_replay(tmp_path / "r", ["END_CALL"])
    brain = replay(directory, max_turns=2**53 - 1)
    assert brain.next_line(0, None).end_reason == "caller_end_call"
    assert sup.profile_block_problems(brain.profile_block()) == []


def test_scripted_brain_turn_index_must_be_a_non_negative_int() -> None:
    """Contract section 5: a bad turn index is a ValueError, a boolean included."""
    brain = brains().ScriptedBrain(scripted_turns([sup.LINE_A]))
    for bad in (-1, True, 0.0, "0", None):
        with pytest.raises(ValueError, match=r"\S"):
            brain.next_line(bad, None)


def test_scripted_profile_block_is_the_mode_alone_and_validates() -> None:
    """ADR 0027: the scripted member of the schema is the mode alone."""
    block = brains().ScriptedBrain(scripted_turns([sup.LINE_A])).profile_block()
    assert block == {"mode": "scripted"}
    assert sup.profile_block_problems(block) == []
    assert sup.profile_block_problems({"mode": "scripted", "model": "x"})


# ----------------------------------------------------------------------------- replay brain


def test_replay_brain_missing_file_is_a_refusal_before_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0028 rule 1, I1 and ADR 0026 rule 14 step 1: a replay directory that lacks a file the
    call could need is refused when the brain is built, with a typed error that names the file
    and the code, before any key is read and before any socket is opened. While the brain is
    built, every read of the environment is recorded and a socket cannot be created."""
    error_class = brains().ReplayDirectoryError
    assert issubclass(error_class, sup.mod("errors").EvalcoreError)
    assert issubclass(error_class, ValueError)

    last_missing = write_replay(tmp_path / "a", sup.LINES)  # files 1 to 4
    first_missing = write_replay(tmp_path / "b", sup.LINES, start=2)  # files 2 to 5
    gap = write_replay(tmp_path / "c", [sup.LINE_A, sup.LINE_B])  # files 1, 2, 4
    write_replay(gap, [sup.LINE_C], start=4)
    empty = tmp_path / "d"
    empty.mkdir()
    cases = {
        "the last file is missing": (last_missing, 5, 5),
        "the first file is missing": (first_missing, 4, 1),
        "a gap in the middle": (gap, 4, 3),
        "an empty directory": (empty, 2, 1),
    }

    def no_socket(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a socket was opened")

    spy = sup.EnvSpy(os.environ)
    with monkeypatch.context() as patch:
        patch.setattr(os, "environ", spy)
        patch.setattr(socket.socket, "__init__", no_socket)
        patch.setattr(socket, "create_connection", no_socket)
        spy.active = True
        for name, (directory, max_turns, turn) in cases.items():
            with pytest.raises(error_class) as excinfo:
                replay(directory, max_turns=max_turns)
            error = excinfo.value
            assert (error.turn, error.code) == (turn, "missing"), name
            text = str(error)
            assert text == f"ReplayDirectoryError: turn-{turn:04d}.json: missing", name
            assert tmp_path.name not in text, name
            assert str(directory) not in text, name
            assert error.__cause__ is None, name
        spy.active = False
    assert spy.reads == [], "the environment was read while the brain was built"


def test_replay_brain_reads_the_directory_once_when_it_is_built(tmp_path: Path) -> None:
    """Rule 1: nothing touches the file system after step 1, so a file cannot go missing mid-
    call."""
    directory = write_replay(tmp_path / "r", sup.LINES)
    brain = replay(directory, max_turns=4)
    for child in directory.iterdir():
        child.unlink()
    directory.rmdir()
    decisions = sup.drive(brain, [None] * 5, sup.RecordingTts())
    assert [d.line for d in decisions[:4]] == list(sup.LINES)
    assert (decisions[4].line, decisions[4].end_reason) == (None, "max_turns")


def test_replay_brain_keeps_no_path_and_its_errors_have_one_shape(tmp_path: Path) -> None:
    """The directory is an input of step 1 only: the built brain holds no path, and every error
    reads ``ReplayDirectoryError: <file or "replay directory">: <code>``."""
    directory = write_replay(tmp_path / "canaryqa-path-foxtrot", sup.LINES)
    brain = replay(directory, max_turns=4)
    assert not sup.scan_for(vars(brain), "canaryqa-path-foxtrot")
    assert not sup.scan_for(vars(brain), str(tmp_path))
    assert "canaryqa-path-foxtrot" not in repr(brain) + repr(brain.profile_block())
    with pytest.raises(brains().ReplayDirectoryError) as excinfo:
        replay(tmp_path / "nowhere", max_turns=1)
    assert str(excinfo.value) == "ReplayDirectoryError: replay directory: directory_missing"
    (directory / "turn-0002.json").write_bytes(b"{")
    with pytest.raises(brains().ReplayDirectoryError) as excinfo:
        replay(directory, max_turns=4)
    assert str(excinfo.value) == "ReplayDirectoryError: turn-0002.json: not_json"
    assert (excinfo.value.turn, excinfo.value.code) == (2, "not_json")
    for bad in ((0, "missing"), (-1, "missing"), (True, "missing"), (1, "other"), (None, "")):
        with pytest.raises(ValueError, match=r"\S"):
            brains().ReplayDirectoryError(turn=bad[0], code=bad[1])


def test_replay_brain_plays_the_fixture_scenarios_exactly(tmp_path: Path) -> None:
    """Rule 1: the three invented Marathi scenarios replay line for line and end at `max_turns`."""
    for number in range(3):
        lines = scenario_lines(number)
        directory = write_replay(tmp_path / f"s{number}", lines)
        brain = replay(directory, max_turns=len(lines))
        tts = sup.RecordingTts()
        decisions = sup.drive(brain, [None] * (len(lines) + 1), tts)
        assert tts.texts == lines
        assert (decisions[-1].line, decisions[-1].end_reason) == (None, "max_turns")
        assert all((d.invalid_outputs, d.regenerations) == (0, 0) for d in decisions)


def test_the_files_after_the_sentinel_are_not_needed_and_not_read(tmp_path: Path) -> None:
    """A replay of a call that the caller ended needs no padding up to ``max_turns``: the call
    cannot reach a turn after the sentinel (contract section 7.2)."""
    directory = write_replay(tmp_path / "r", [sup.LINE_A, "END_CALL"])
    (directory / "turn-0003.json").write_bytes(b"\xff not json at all")
    brain = replay(directory, max_turns=10)
    assert brain.next_line(0, None).line == sup.LINE_A
    ended = brain.next_line(1, None)
    assert (ended.line, ended.end_reason) == (None, "caller_end_call")
    assert (ended.invalid_outputs, ended.regenerations) == (0, 0)
    with pytest.raises(ValueError, match=r"\S"):
        brain.next_line(2, None)
    # a sentinel with white space is still the sentinel, and the first file may be it
    directory = write_replay(tmp_path / "s", ["  END_CALL \n"])
    assert replay(directory, max_turns=3).next_line(0, None).end_reason == "caller_end_call"


def test_other_names_and_files_beyond_the_cap_are_ignored(tmp_path: Path) -> None:
    """Contract section 6.1: only `turn-NNNN.json` up to `max_turns` count; look-alike names do
    not."""
    directory = write_replay(tmp_path / "r", [sup.LINE_A, sup.LINE_B])
    for name, content in (
        ("turn-1.json", b"garbage"),
        ("turn-00001.json", b"garbage"),
        ("turn-0001.json.bak", b"garbage"),
        ("notes.txt", b"garbage"),
        ("turn-0003.json", b"garbage"),
    ):
        (directory / name).write_bytes(content)
    brain = replay(directory, max_turns=2)
    assert [brain.next_line(i, None).line for i in range(2)] == [sup.LINE_A, sup.LINE_B]
    assert brain.next_line(2, None).end_reason == "max_turns"


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"", "not_json"),
        (b"\xff\xfe\x00", "not_json"),
        (b"{", "not_json"),
        (b"NaN", "not_json"),
        (b'{"line": NaN}', "not_json"),
        (b"[" * 60_000, "not_json"),
        (b"{" * 5000 + b"}" * 5000, "not_json"),
        (b"[]", "bad_shape"),
        (b'"x"', "bad_shape"),
        (b"null", "bad_shape"),
        (b"{}", "bad_shape"),
        (b'{"line": 5}', "bad_shape"),
        (b'{"line": null}', "bad_shape"),
        (b'{"line": ["x"]}', "bad_shape"),
        (b'{"text": "x"}', "bad_shape"),
        (b'{"line": "x", "extra": 1}', "bad_shape"),
        (b'{"line": "x", "line": "y"}', "bad_shape"),
        (b'{"line": "' + b"a" * 70_000 + b'"}', "too_large"),
    ],
)
def test_a_replay_file_that_does_not_parse_is_refused_before_the_call(
    tmp_path: Path, content: bytes, code: str
) -> None:
    """Rule 1: 18 shapes of bad file (not JSON, NaN, too deep, wrong shape, duplicate keys, too
    large) are refused at step 1 with a code and without quoting."""
    directory = write_replay(tmp_path / "r", [sup.LINE_A], raw={2: content})
    with pytest.raises(brains().ReplayDirectoryError) as excinfo:
        replay(directory, max_turns=3)
    assert (excinfo.value.turn, excinfo.value.code) == (2, code)
    assert "turn-0002.json" in str(excinfo.value)
    assert tmp_path.name not in str(excinfo.value)


def test_replay_file_size_limit_is_a_named_constant(tmp_path: Path) -> None:
    """Contract section 6: the limit is 65,536 bytes, inclusive."""
    limit = brains().REPLAY_FILE_MAX_BYTES
    assert limit == 65536
    prefix = b'{"line": "'
    body = b"a" * (limit - len(prefix) - 2)
    at_limit = prefix + body + b'"}'
    assert len(at_limit) == limit
    directory = write_replay(tmp_path / "ok", [], raw={1: at_limit})
    assert replay(directory, max_turns=1) is not None
    directory = write_replay(tmp_path / "big", [], raw={1: at_limit + b" "})
    with pytest.raises(brains().ReplayDirectoryError) as excinfo:
        replay(directory, max_turns=1)
    assert excinfo.value.code == "too_large"


def test_replay_refuses_links_directories_and_a_missing_directory(tmp_path: Path) -> None:
    """Contract section 6.2: links and directories in place of files are refused (as ADR 0024 A9
    does for cache files); a missing directory has its own code; a directory reached through a
    link is fine."""
    error_class = brains().ReplayDirectoryError
    real = write_replay(tmp_path / "real", [sup.LINE_A, sup.LINE_B])
    linked = write_replay(tmp_path / "linked", [sup.LINE_A])
    (linked / "turn-0002.json").symlink_to(real / "turn-0002.json")
    with pytest.raises(error_class) as excinfo:
        replay(linked, max_turns=2)
    assert (excinfo.value.turn, excinfo.value.code) == (2, "not_a_regular_file")
    folder = write_replay(tmp_path / "folder", [sup.LINE_A])
    (folder / "turn-0002.json").mkdir()
    with pytest.raises(error_class) as excinfo:
        replay(folder, max_turns=2)
    assert (excinfo.value.turn, excinfo.value.code) == (2, "not_a_regular_file")
    for missing in (tmp_path / "nowhere", tmp_path / "real" / "turn-0001.json"):
        with pytest.raises(error_class) as excinfo:
            replay(missing, max_turns=1)
        assert (excinfo.value.turn, excinfo.value.code) == (None, "directory_missing")
        assert tmp_path.name not in str(excinfo.value)
    # a directory reached through a link is fine (a user's path may be one); a string is a path
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    assert replay(alias, max_turns=2).next_line(1, None).line == sup.LINE_B
    assert replay(str(real), max_turns=2).next_line(0, None).line == sup.LINE_A


def test_a_replayed_line_passes_the_output_validator_with_no_regeneration(tmp_path: Path) -> None:
    """Rule 6: the same validator as an LLM line, and a refusal is final (zero regenerations)."""
    corpus = sup.hostile_replies(system_echo=False)
    checked = 0
    for number, (name, reply) in enumerate(corpus.items()):
        if not isinstance(reply, str) or len(reply) > 5000:
            continue  # a file over the size limit is refused at build time, tested above
        directory = write_replay(tmp_path / f"c{number}", [sup.LINE_A])
        (directory / "turn-0002.json").write_text(json.dumps({"line": reply}), "utf-8")
        brain = replay(directory, max_turns=2)
        assert brain.next_line(0, None).line == sup.LINE_A
        decision = brain.next_line(1, None)
        assert (decision.line, decision.end_reason) == (None, "brain_invalid_output"), name
        assert (decision.invalid_outputs, decision.regenerations) == (1, 0), name
        checked += 1
    assert checked >= 30


def test_a_replayed_line_is_returned_as_the_validator_returns_it(tmp_path: Path) -> None:
    """Rule 6: a replayed line is stripped and checked like a model's."""
    padded = "  " + sup.LINE_B + " \n"
    directory = write_replay(tmp_path / "r", [padded, sup.LINE_C])
    brain = replay(directory, max_turns=2)
    assert brain.next_line(0, None).line == sup.LINE_B
    assert brain.next_line(1, "ignored").line == sup.LINE_C


def test_replay_brain_checks_its_parameters(tmp_path: Path) -> None:
    """Rule 5 in spirit: the replay brain has no defaults and refuses bad caps and a language
    without an allowed set."""
    parameters = inspect.signature(brains().ReplayBrain).parameters
    assert list(parameters) == ["directory", "language", "max_turns", "max_line_chars"]
    for name in ("language", "max_turns", "max_line_chars"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is inspect.Parameter.empty
    directory = write_replay(tmp_path / "r", [sup.LINE_A])
    for key, value in (
        ("max_turns", 0),
        ("max_turns", True),
        ("max_turns", 2**53),
        ("max_line_chars", 0),
        ("max_line_chars", 2501),
        ("language", "hi"),
        ("language", None),
    ):
        params: dict[str, Any] = {"language": "mr", "max_turns": 1, "max_line_chars": CAP}
        with pytest.raises(ValueError, match=r"\S"):
            brains().ReplayBrain(directory, **{**params, key: value})
    with pytest.raises(TypeError):
        replay(None, max_turns=1)  # type: ignore[arg-type]
    brain = replay(directory, max_turns=1)
    for bad in (-1, True, 0.0, None):
        with pytest.raises(ValueError, match=r"\S"):
            brain.next_line(bad, None)


def test_replay_decisions_are_deterministic_and_random_access(tmp_path: Path) -> None:
    """Rule 12: the decisions do not depend on the order of the calls."""
    directory = write_replay(tmp_path / "r", scenario_lines(1))
    first = replay(directory, max_turns=10)
    second = replay(directory, max_turns=10)
    forward = [first.next_line(i, None) for i in range(11)]
    backward = [second.next_line(i, None) for i in (10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0)][::-1]
    assert forward == backward
    assert forward[10].end_reason == "max_turns"
    assert first.next_line(25, None).end_reason == "max_turns"


def test_replay_profile_block_validates_and_holds_no_path(tmp_path: Path) -> None:
    """ADR 0027 and rule 3: the replay member of the schema has no path and the block does not name
    the directory."""
    directory = write_replay(tmp_path / "secret-dir-name", [sup.LINE_A, sup.LINE_B])
    block = replay(directory, max_turns=2, max_line_chars=120).profile_block()
    assert block == {
        "mode": "replay",
        "max_line_chars": 120,
        "max_turns": 2,
        "end_sentinel": "END_CALL",
    }
    assert sup.profile_block_problems(block) == []
    assert "secret-dir-name" not in repr(block)
