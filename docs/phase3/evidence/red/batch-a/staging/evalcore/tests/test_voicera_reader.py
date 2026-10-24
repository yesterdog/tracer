# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-04: the shipped VoicEra export reader (contract docs/phase3/api-surface-batch-a.md section 4).

``tracer_evalcore.importers.voicera.read_voicera_export`` is the F8 mapper promoted into the
product (ADR 0023 rule 2, SCOPE Q22). The differential test loads the F8 mapper of the evalschema
tests (``packages/evalschema/tests/support/mappings/voicera_transcript.py``, unchanged) by file
path and compares the two on a corpus of inputs. All text is invented (the public twin fixture and
the strings below); non-ASCII characters are written as escapes so that this file is ASCII.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import logging
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from tracer_evalschema.ids import ExternalRef
from tracer_evalschema.privacy import ConsentRecord, PrivacyMarker, RetentionPolicy
from tracer_evalschema.transcript import LanguageDeclaration


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
MAPPINGS = ROOT / "packages" / "evalschema" / "tests" / "support" / "mappings"
TWIN = (
    ROOT
    / "packages"
    / "evalschema"
    / "tests"
    / "fixtures"
    / "synthetic"
    / "voicera-twin-2026-10-24"
    / "call_2026-10-24_11-01-07_c0ffee01.txt"
)

CREATED_AT = "2026-10-25T09:00:00.000000Z"
PRIVACY: dict[str, Any] = {"human_subject": False}
UUID = "5c0a7e1d-3b42-4f6e-9a18-2d7e4c9b0a11"
STAMP = "2026-10-24T05:31:07.412+00:00"
HEADER = f"call_id={UUID}\n---\n"
DECLARATION: dict[str, Any] = {
    "language": "mr-IN",
    "source": "user_declared",
    "declared_by": "cli",
    "declared_at": "2026-10-25T08:00:00.000000Z",
}
#: The warning codes that do not drop or alter content (contract section 4.3).
NON_REFUSING = frozenset(
    {"call_id_not_uuid", "timestamp_not_parseable", "naive_timestamp", "time_precision_reduced"}
)

# Devanagari and friends, as escapes
HELLO = "\u0928\u092e\u0938\u094d\u0915\u093e\u0930"
THANKS = "\u0927\u0928\u094d\u092f\u0935\u093e\u0926"
NUKTA_EXCLUDED = "\u0958"  # not stable under NFC (its NFC form is two code points)
ZWJ_TEXT = "\u0915\u094d\u200d\u0937"
ZWNJ_TEXT = "\u0915\u094d\u200c\u0937"


def voicera() -> Any:
    return importlib.import_module("tracer_evalcore.importers.voicera")


def f8() -> Any:
    """The F8 mapper of the evalschema tests, loaded by path (its package ``tests`` is not
    importable from this directory)."""
    name = "_tracer_f8_support"
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [str(MAPPINGS)]
        sys.modules[name] = package
    return importlib.import_module(f"{name}.voicera_transcript")


def msg(speaker: str, text: str, stamp: str = STAMP) -> str:
    return f"[{stamp}] {speaker}: {text}\n"


def read(text: Any, **over: Any) -> Any:
    kwargs: dict[str, Any] = {"created_at": CREATED_AT, "privacy": PRIVACY}
    kwargs.update(over)
    return voicera().read_voicera_export(text, **kwargs)


def corpus() -> dict[str, tuple[str, dict[str, Any]]]:
    """Inputs for the differential test: every kind of line, every warning, edge encodings."""
    many = "".join(msg("assistant" if i % 2 else "user", f"line {i}") for i in range(600))
    return {
        "twin": (TWIN.read_bytes().decode("utf-8"), {}),
        "minimal": (HEADER + msg("assistant", "hi"), {}),
        "header_only": (HEADER, {}),
        "two_roles": (HEADER + msg("assistant", "a") + msg("user", "b"), {}),
        "crlf": ((HEADER + msg("assistant", "a") + msg("user", "b")).replace("\n", "\r\n"), {}),
        "bom": ("\ufeff" + HEADER + msg("assistant", "a"), {}),
        "blank_lines": (
            HEADER + "\n" + msg("user", "a") + "\n\n" + msg("assistant", "b") + " \n",
            {},
        ),
        "unknown_speaker": (HEADER + msg("system", "x") + msg("assistant", "y"), {}),
        "only_unknown_speakers": (HEADER + msg("system", "x"), {}),
        "unparsed_line": (HEADER + msg("assistant", "a") + "garbage line\n" + msg("user", "b"), {}),
        "bare_speaker_line": (HEADER + "assistant: no stamp\n" + msg("user", "b"), {}),
        "no_header": (msg("assistant", "a") + msg("user", "b"), {}),
        "no_delimiter": (f"call_id={UUID}\n" + msg("assistant", "a"), {}),
        "no_delimiter_garbage": (f"call_id={UUID}\nnot a delimiter\n" + msg("assistant", "a"), {}),
        "naive_stamp": (HEADER + msg("assistant", "a", "2026-10-24T05:31:07.412"), {}),
        "bad_stamp": (HEADER + msg("assistant", "a", "yesterday at five"), {}),
        "long_fraction": (HEADER + msg("assistant", "a", "2026-10-24T05:31:07.1234567+00:00"), {}),
        "offset_stamp": (HEADER + msg("assistant", "a", "2026-10-24T11:01:07.412+05:30"), {}),
        "z_stamp": (HEADER + msg("assistant", "a", "2026-10-24T05:31:07Z"), {}),
        "call_id_not_uuid": ("call_id=abc-123\n---\n" + msg("assistant", "a"), {}),
        "empty_call_id": ("call_id=\n---\n" + msg("assistant", "a"), {}),
        "header_leading_space": (f" call_id={UUID}\n---\n" + msg("assistant", "a"), {}),
        "header_trailing_space": (f"call_id={UUID} \n---\n" + msg("assistant", "a"), {}),
        "empty_text": (HEADER + f"[{STAMP}] assistant:\n", {}),
        "spaces_and_tabs": (HEADER + msg("assistant", "  lead  and trail  \t"), {}),
        "lone_surrogate": (HEADER + msg("assistant", "a\ud800b"), {}),
        "empty": ("", {}),
        "whitespace_only": ("\n  \n\t\n", {}),
        "nukta_and_joiners": (
            HEADER + msg("user", f"{NUKTA_EXCLUDED} {ZWJ_TEXT} {ZWNJ_TEXT} {HELLO}"),
            {},
        ),
        "bidi_in_text": (HEADER + msg("assistant", "abc\u202edef\u2066x\u2069"), {}),
        "long_line": (HEADER + msg("assistant", "x" * 200_000), {}),
        "uppercase_speaker": (HEADER + msg("Assistant", "a"), {}),
        "colon_and_brackets": (HEADER + msg("assistant", "a: b [c] d: e"), {}),
        "repeated_bad_line": (HEADER + "bad\nbad\n" + msg("user", "a"), {}),
        "mixed_scripts": (HEADER + msg("user", f"{HELLO} library card {THANKS} 123"), {}),
        "many_turns": (HEADER + many, {}),
        "declared": (HEADER + msg("assistant", "a"), {"declared_language": DECLARATION}),
        "declared_empty_input": ("", {"declared_language": DECLARATION}),
    }


def dump(model: Any, skip: set[str]) -> dict[str, Any]:
    data: dict[str, Any] = model.model_dump(mode="json")
    return {k: v for k, v in data.items() if k not in skip}


def warning_tuples(items: Any) -> list[tuple[str, str | None, str | None, str]]:
    return [(w.code, w.path, w.raw, w.message) for w in items]


# ------------------------------------------------------------------------------ the SCOPE tests


@pytest.mark.parametrize("case", sorted(corpus()))
def test_voicera_reader_equals_f8_mapper_on_public_cases(case: str) -> None:
    text, extra = corpus()[case]
    expected = f8().map_f8(text, created_at=CREATED_AT, privacy=PRIVACY, **extra)
    got = read(text, accept_warnings=True, **extra)

    # records: the transcript and the turns, without their identities
    if not expected.transcripts:
        assert got.transcript is None
        assert got.turns == ()
    else:
        assert got.transcript is not None
        assert dump(got.transcript, {"id"}) == dump(expected.transcripts[0], {"id"})
        assert all(t.transcript_id == got.transcript.id for t in got.turns)
    assert [dump(t, {"id", "transcript_id"}) for t in got.turns] == [
        dump(t, {"id", "transcript_id"}) for t in expected.turns
    ]
    assert [t.seq for t in got.turns] == list(range(len(expected.turns)))
    assert len({t.id for t in got.turns}) == len(got.turns)
    # warnings and external references, in the same order, with the same words
    assert warning_tuples(got.warnings) == warning_tuples(expected.warnings)
    assert tuple(got.external_refs) == tuple(expected.external_refs)
    assert isinstance(got.warnings, tuple)
    assert isinstance(got.turns, tuple)

    # without accept_warnings the same input is refused exactly when a warning drops content
    refusing = [w for w in expected.warnings if w.code not in NON_REFUSING]
    if refusing:
        with pytest.raises(voicera().VoicEraRefusedError):
            read(text, **extra)
    else:
        plain = read(text, **extra)
        assert warning_tuples(plain.warnings) == warning_tuples(expected.warnings)
        assert plain.transcript is not None
        assert dump(plain.transcript, {"id"}) == dump(expected.transcripts[0], {"id"})


def test_voicera_reader_refuses_dropped_lines_by_default() -> None:
    mod = voicera()
    leak = "LEAKSENTINEL"
    dropping: dict[str, tuple[str, str, str]] = {
        "unknown_speaker": (
            HEADER + msg("system", leak) + msg("assistant", "y"),
            "unknown_enum_value",
            "f8:3/speaker",
        ),
        "unparsed_line": (
            HEADER + msg("assistant", "a") + f"{leak} garbage\n",
            "unparsed_line",
            "f8:4",
        ),
        "empty": ("", "empty_input", ""),
        "whitespace_only": ("\n \n", "empty_input", ""),
        "no_header": (msg("assistant", leak), "header_not_recognized", "f8:1"),
        "no_delimiter": (
            f"call_id={UUID}\n" + msg("assistant", leak),
            "delimiter_not_found",
            "f8:2",
        ),
        "lone_surrogate": (
            HEADER + msg("assistant", "a\ud800b"),
            "text_not_valid_unicode",
            "f8:3/text",
        ),
    }
    for name, (text, code, pointer) in dropping.items():
        with pytest.raises(mod.VoicEraRefusedError) as caught:
            read(text)
        refusal = caught.value
        assert any(w.code == code for w in refusal.warnings), name
        message = str(refusal)
        assert code in message, name
        assert pointer in message, name
        assert leak not in message, f"{name}: the refusal message quotes a dropped line"
        assert leak not in repr(refusal)
        # the same input is read when the caller says so, and every warning is returned
        accepted = read(text, accept_warnings=True)
        assert any(w.code == code for w in accepted.warnings), name
        assert [w.code for w in refusal.warnings] == [w.code for w in accepted.warnings]
    assert issubclass(
        mod.VoicEraRefusedError, importlib.import_module("tracer_evalcore.errors").InputRefusedError
    )
    assert issubclass(mod.VoicEraRefusedError, ValueError)

    # warnings that drop nothing are returned, not refused
    for text, code in (
        ("call_id=abc-123\n---\n" + msg("assistant", "a"), "call_id_not_uuid"),
        (HEADER + msg("assistant", "a", "2026-10-24T05:31:07.412"), "naive_timestamp"),
        (HEADER + msg("assistant", "a", "tomorrow"), "timestamp_not_parseable"),
        (
            HEADER + msg("assistant", "a", "2026-10-24T05:31:07.1234567+00:00"),
            "time_precision_reduced",
        ),
    ):
        done = read(text)
        assert [w.code for w in done.warnings] == [code]
        assert len(done.turns) == 1
    assert mod.NON_REFUSING_WARNING_CODES == NON_REFUSING
    assert isinstance(mod.NON_REFUSING_WARNING_CODES, frozenset)

    # one dropping warning among harmless ones still refuses, and the refusal carries every
    # warning of the read, harmless ones included
    with pytest.raises(mod.VoicEraRefusedError) as mixed:
        read("call_id=abc-123\n---\n" + msg("assistant", "a") + "junk\n")
    assert [w.code for w in mixed.value.warnings] == ["call_id_not_uuid", "unparsed_line"]

    # a long list of warnings gives a bounded message
    many = HEADER + "".join(f"junk {i}\n" for i in range(500))
    with pytest.raises(mod.VoicEraRefusedError) as caught:
        read(many, accept_warnings=False)
    assert len(str(caught.value)) < 5000
    assert len(caught.value.warnings) == 500

    # accept_warnings is a keyword-only bool with the default False; nothing else is taken for yes
    params = inspect.signature(mod.read_voicera_export).parameters
    assert params["accept_warnings"].default is False
    assert params["accept_warnings"].kind is inspect.Parameter.KEYWORD_ONLY
    for wrong in ("yes", 1, None):
        with pytest.raises(TypeError):
            read(HEADER + msg("assistant", "a"), accept_warnings=wrong)


def test_voicera_reader_keeps_text_verbatim_and_seq_order() -> None:
    lines: list[tuple[str, str, str]] = [
        ("assistant", "  two  leading and trailing  ", "2026-10-24T05:31:09.000+00:00"),
        ("user", "tab\tinside", "2026-10-24T05:31:08.000+00:00"),  # earlier stamp, later line
        ("system", "not mapped", "2026-10-24T05:31:07.000+00:00"),
        ("user", f"{NUKTA_EXCLUDED} stays as written", "2026-10-24T05:31:07.000+00:00"),
        ("assistant", f"{ZWJ_TEXT} | {ZWNJ_TEXT}", "2026-10-24T05:31:07.000+00:00"),
        ("user", f"Latin words inside {HELLO} text 123 \u0967\u0968\u0969", STAMP),
        ("assistant", "<script>alert(1)</script> & \"quotes\" 'single'", STAMP),
        ("user", "emoji \U0001f600 astral and \u0964 \u0965 danda", STAMP),
        ("assistant", "", STAMP),
        ("user", "y" * 10_000, STAMP),
    ]
    text = HEADER + "".join(msg(s, t, ts) for s, t, ts in lines)
    result = read(text, accept_warnings=True)
    expected = [(s, t) for s, t, _ in lines if s in {"assistant", "user"}]
    assert [t.text for t in result.turns] == [t for _, t in expected]
    assert [t.seq for t in result.turns] == list(range(len(expected)))
    assert [t.role for t in result.turns] == [
        "agent" if s == "assistant" else "caller" for s, _ in expected
    ]
    # nothing is normalised: the excluded character keeps its code point
    assert result.turns[2].text.startswith(NUKTA_EXCLUDED)
    assert "\u0915\u093c" not in result.turns[2].text
    assert "\u200d" in result.turns[3].text
    assert "\u200c" in result.turns[3].text
    # the order is the order of the lines, never the order of the stamps
    stamps = [t.wall_time.at for t in result.turns if t.wall_time is not None]
    assert stamps != sorted(stamps)
    # a large export is read whole
    big = HEADER + "".join(msg("user" if i % 2 else "assistant", f"t{i}") for i in range(5000))
    assert [t.seq for t in read(big).turns] == list(range(5000))


def test_voicera_reader_maps_assistant_agent_user_caller() -> None:
    text = HEADER + msg("assistant", "a1") + msg("user", "u1") + msg("assistant", "a2")
    result = read(text)
    assert [(t.role, t.text) for t in result.turns] == [
        ("agent", "a1"),
        ("caller", "u1"),
        ("agent", "a2"),
    ]
    assert {t.source for t in result.turns} == {"agent_log"}
    assert {t.timing_source for t in result.turns} == {"none"}
    assert result.transcript is not None
    assert result.transcript.source == "agent_export"
    assert result.transcript.provider_call_ref is not None
    assert (
        result.transcript.provider_call_ref.system,
        result.transcript.provider_call_ref.kind,
        result.transcript.provider_call_ref.value,
    ) == ("voicera", "call_id", UUID)
    assert result.external_refs == (ExternalRef(system="voicera", kind="call_id", value=UUID),)
    # only the two labels map; a role is never guessed from anything else
    other = read(
        HEADER + msg("Assistant", "x") + msg("agent", "y") + msg("caller", "z"),
        accept_warnings=True,
    )
    assert other.turns == ()
    assert [w.code for w in other.warnings] == ["unknown_enum_value"] * 3
    # a user-only export has no agent turn and the reader says so by what it returns
    only_user = read(HEADER + msg("user", "u"))
    assert [t.role for t in only_user.turns] == ["caller"]
    # a PrivacyMarker instance works as well as a mapping
    marker = PrivacyMarker(human_subject=False)
    assert read(text, privacy=marker).transcript is not None
    assert read(text, privacy=marker).transcript.privacy == marker
    human = PrivacyMarker(
        human_subject=True,
        consent=ConsentRecord(status="unknown"),
        retention=RetentionPolicy(delete_on_expiry=False),
    )
    assert read(text, privacy=human).transcript.privacy == human


def test_voicera_reader_never_sets_language_or_timing() -> None:
    texts = [
        TWIN.read_bytes().decode("utf-8"),
        HEADER
        + msg("user", f"{HELLO} mr-IN language: en-US lang=hi-IN")
        + msg("assistant", "Hello, how can I help? 12:30 pm for 3 minutes")
        + msg("user", "\u0905\u0906\u0907 abc"),
    ]
    for text in texts:
        result = read(text)
        transcript = result.transcript
        assert transcript is not None
        for name in (
            "provider",
            "model",
            "detected_language",
            "detected_language_raw",
            "language_confidence",
            "declared_language",
            "timeline_origin",
        ):
            assert getattr(transcript, name) is None, name
        assert result.turns
        for turn in result.turns:
            for name in (
                "start_ms",
                "end_ms",
                "segments",
                "language",
                "language_raw",
                "interrupted",
                "exchange_index",
            ):
                assert getattr(turn, name) is None, name
            assert turn.timing_source == "none"
            assert turn.wall_time is None or turn.wall_time.meaning == "unknown"


def test_voicera_reader_declared_language_only_from_argument() -> None:
    text = HEADER + msg("user", f"{HELLO} (mr-IN)") + msg("assistant", "language: en-US")
    none = read(text)
    assert none.transcript is not None
    assert none.transcript.declared_language is None
    assert all(t.language is None for t in none.turns)

    given = read(text, declared_language=DECLARATION)
    assert given.transcript is not None
    assert given.transcript.declared_language == LanguageDeclaration(**DECLARATION)
    assert given.transcript.detected_language is None
    assert all(t.language is None and t.language_raw is None for t in given.turns)
    again = read(text, declared_language=LanguageDeclaration(**DECLARATION))
    assert again.transcript is not None
    assert again.transcript.declared_language == given.transcript.declared_language
    assert read(text, declared_language=None).transcript.declared_language is None

    # an argument the schema refuses stops the call whatever the export holds, and it is the
    # schema's own error (a ValueError), not a refusal of the export
    refused = voicera().VoicEraRefusedError
    attempts: list[tuple[str, dict[str, Any]]] = []
    for bad in (
        {**DECLARATION, "language": "xx-XX"},
        {**DECLARATION, "source": "detected"},
        {**DECLARATION, "declared_at": "tomorrow"},
        {"language": "mr-IN"},
    ):
        attempts += [(content, {"declared_language": bad}) for content in (text, "", "junk")]
    attempts += [
        ("", {"privacy": bad}) for bad in (None, {"human_subject": "yes"}, {"human_subject": True})
    ]
    attempts.append((text, {"created_at": "not a time"}))
    for content, over in attempts:
        with pytest.raises(ValueError) as caught:  # noqa: PT011 - the model's ValidationError
            read(content, **over)
        assert not isinstance(caught.value, refused), over

    # the keywords
    params = inspect.signature(voicera().read_voicera_export).parameters
    assert list(params) == ["text", "created_at", "privacy", "declared_language", "accept_warnings"]
    assert params["text"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("created_at", "privacy", "declared_language", "accept_warnings"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
    assert params["declared_language"].default is None
    assert params["created_at"].default is inspect.Parameter.empty
    assert params["privacy"].default is inspect.Parameter.empty
    # the export is decoded text, never bytes or a path
    for wrong in (b"call_id=x", Path("x.txt"), None, 12):
        with pytest.raises(TypeError):
            read(wrong)


BIDI_AND_CONTROLS = [
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
    "\u2066",
    "\u2067",
    "\u2068",
    "\u2069",
    "\u200e",
    "\u200f",
    "\u061c",
    "\x00",
    "\x07",
    "\x1b",
    "\x7f",
    "\x85",
]


@pytest.mark.parametrize("char", BIDI_AND_CONTROLS, ids=lambda c: f"U+{ord(c):04X}")
def test_voicera_reader_rejects_bidi_in_call_id_safely(
    char: str, caplog: pytest.LogCaptureFixture
) -> None:
    mod = voicera()
    identifier = f"SECRETID{char}7391"
    text = f"call_id={identifier}\n---\n" + msg("assistant", "hello")
    caplog.set_level(logging.DEBUG)
    for accept in (False, True):  # a safety refusal: accept_warnings cannot switch it off
        with pytest.raises(mod.VoicEraRefusedError) as caught:
            read(text, accept_warnings=accept)
        refusal = caught.value
        shown = [str(refusal), repr(refusal), *map(str, refusal.args)]
        for item in shown:
            assert char not in item, "the control character is echoed"
            assert "SECRETID" not in item, "the identifier is echoed"
            assert "7391" not in item, "the identifier is echoed"
        assert "f8:1/call_id" in str(refusal)
        assert "call_id_unsafe_characters" in str(refusal)
        assert all("SECRETID" not in (w.raw or "") for w in refusal.warnings)
    assert all(
        char not in r.getMessage() and "SECRETID" not in r.getMessage() for r in caplog.records
    )

    # the same identifier without the control character is an ordinary non-UUID call id
    plain = read("call_id=SECRETID7391\n---\n" + msg("assistant", "hello"))
    assert [w.code for w in plain.warnings] == ["call_id_not_uuid"]
    assert plain.external_refs == (
        ExternalRef(system="voicera", kind="call_id", value="SECRETID7391"),
    )


def test_voicera_reader_result_is_a_frozen_value() -> None:
    mod = voicera()
    result = read(HEADER + msg("assistant", "a"))
    assert isinstance(result, mod.VoicEraRead)
    assert dataclasses.is_dataclass(type(result))
    assert [f.name for f in dataclasses.fields(mod.VoicEraRead)] == [
        "transcript",
        "turns",
        "warnings",
        "external_refs",
    ]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.turns = ()
    # the same input read twice differs only in the identities
    again = read(HEADER + msg("assistant", "a"))
    assert result.transcript is not None
    assert again.transcript is not None
    assert result.transcript.id != again.transcript.id
    assert dump(result.transcript, {"id"}) == dump(again.transcript, {"id"})
