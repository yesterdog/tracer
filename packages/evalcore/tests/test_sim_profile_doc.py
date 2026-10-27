# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-21 (the stored configuration of a simulated run): ``tracer.sim_profile/1`` and the input file
``tracer.sim_profile_file/1`` (ADR 0027 rules 1 and 13; ADR 0026 rules 10, 11, 15, 18).

Contract: docs/phase4/api-surface-batch-e.md section 5. Three things are pinned:

* the file the user writes has no default for any value (the turn-taking values, the engines, the
  voice of every persona, the agreement parameters and the minimum interval are all required);
* the stored document is exactly the ADR's JSON Schema (the shipped schema equals the ADR block, and
  the hand validator agrees with ``jsonschema`` on every structural fault) plus the cross-field
  rules the schema cannot state, each with its stable code;
* the document holds only machine keys: the voice of the run's persona alone, the caps as given,
  and the connection labels (a variable NAME, never a token).
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped,unused-ignore]

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]
from evalcore_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    CONNECTION_NONE,
    CONNECTION_TWILIO,
    NO_CAPS,
    dumps,
    llm_caller,
    mod,
    profile_file_doc,
    replay_caller,
    stored_profile,
)

CANARY = "".join(["PROFILE", "CANARY", "w3"])


def sp() -> Any:
    return mod("simprofile")


def code_of(document: Any, **options: Any) -> str:
    with pytest.raises(sp().SimProfileError) as caught:
        sp().validate_sim_profile(document, **options)
    assert str(caught.value) == caught.value.code
    return str(caught.value.code)


def file_code(text: str) -> str:
    with pytest.raises(sp().SimProfileError) as caught:
        sp().parse_sim_profile_file(text)
    assert str(caught.value) == caught.value.code
    return str(caught.value.code)


def adr_schema() -> dict[str, Any]:
    section = sup.heading_section(
        sup.ADR27.read_text("utf-8"), "## The `tracer.sim_profile/1` format"
    )
    blocks = re.findall(r"```json\n(.*?)\n```", section, re.DOTALL)
    assert len(blocks) == 1
    parsed: dict[str, Any] = json.loads(blocks[0])
    return parsed


def test_shipped_sim_profile_schema_equals_adr0027_block() -> None:
    """ADR 0027: ``tracer-evalcore`` ships the same schema as the ADR's block; the documents test
    compares them. This is that comparison for ``tracer.sim_profile/1`` (shipped by P4-21)."""
    shipped = sp().SIM_PROFILE_JSON_SCHEMA
    assert json.loads(json.dumps(shipped)) == adr_schema()
    assert shipped["title"] == sp().SIM_PROFILE_FORMAT == "tracer.sim_profile/1"
    assert sp().SIM_PROFILE_METADATA_KEY == "tracer.sim_profile"
    assert sp().SIM_PROFILE_FILE_FORMAT == "tracer.sim_profile_file/1"
    Draft202012Validator.check_schema(dict(shipped))


# ------------------------------------------------------------------------------- the input file


def test_sim_profile_file_parses_every_value_as_written() -> None:
    parsed = sp().parse_sim_profile_file(dumps(profile_file_doc()))
    assert parsed.dialect == "pipecat-protobuf/1"
    assert dict(parsed.audio) == {
        "sample_rate_hz": 16000,
        "channels": 1,
        "frame_ms": 20,
        "wire_codec": "pcm_s16le",
    }
    assert dict(parsed.turn_taking) == {
        "caller_speaks_first": True,
        "wait_for_agent_silence_ms": 300,
        "max_silence_ms": 3000,
    }
    assert parsed.barge_ins == ()
    assert dict(parsed.caller) == {"mode": "scripted"}
    assert set(parsed.voices) == {"caller-a"}
    assert dict(parsed.stt)["max_chunk_ms"] == 25_000
    assert (parsed.flag_min_confidence, parsed.normaliser) == (0.5, "mr-Deva/1")
    assert parsed.min_call_interval_ms == 0


@pytest.mark.parametrize(
    "key",
    [
        "dialect",
        "audio",
        "turn_taking",
        "barge_ins",
        "caller",
        "voices",
        "stt",
        "agreement",
        "min_call_interval_ms",
    ],
)
def test_sim_profile_file_has_no_default_for_any_section(key: str) -> None:
    """ADR 0026 rule 10 and ADR 0027 rule 13: every value is required in the file, no default."""
    document = profile_file_doc()
    del document[key]
    assert file_code(dumps(document)) == f"sim_profile_file_missing_{key}"


@pytest.mark.parametrize(
    "key", ["wait_for_agent_silence_ms", "max_silence_ms", "caller_speaks_first"]
)
def test_sim_profile_file_turn_taking_values_have_no_default(key: str) -> None:
    """The three turn-taking values are the owner's (O4): dropping one is refused."""
    document = profile_file_doc()
    del document["turn_taking"][key]
    assert file_code(dumps(document)) == "sim_profile_file_invalid_turn_taking"


def test_sim_profile_file_refuses_what_is_not_a_profile_file() -> None:
    good = profile_file_doc()
    assert file_code("{") == "sim_profile_file_not_json"
    assert file_code('{"format": 1, "format": 2}') == "sim_profile_file_not_json"
    assert file_code("[]") == "sim_profile_file_shape"
    assert file_code(dumps({**good, "format": "tracer.sim_profile/1"})) == "sim_profile_file_format"
    assert file_code(dumps({**good, "extra": 1})) == "sim_profile_file_unknown_key"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"dialect": "other/1"}, "sim_profile_file_invalid_dialect"),
        (
            {
                "audio": {
                    "sample_rate_hz": 100,
                    "channels": 1,
                    "frame_ms": 20,
                    "wire_codec": "pcm_s16le",
                }
            },
            "sim_profile_file_invalid_audio",
        ),
        ({"barge_ins": [{"turn_index": 0}]}, "sim_profile_file_invalid_barge_ins"),
        ({"caller": {"mode": "robot"}}, "sim_profile_file_invalid_caller"),
        ({"stt": {"provider": "fake", "model": "x"}}, "sim_profile_file_invalid_stt"),
        ({"voices": {}}, "sim_profile_file_invalid_voices"),
        ({"voices": {"caller-a": {"provider": "fake"}}}, "sim_profile_file_invalid_voices"),
        ({"agreement": {"flag_min_confidence": 0.5}}, "sim_profile_file_invalid_agreement"),
        (
            {"agreement": {"flag_min_confidence": "0.5", "normaliser": "mr-Deva/1"}},
            "sim_profile_file_invalid_agreement",
        ),
        (
            {"agreement": {"flag_min_confidence": 0.5, "normaliser": "xx-Latn/1"}},
            "sim_profile_file_invalid_agreement",
        ),
        ({"min_call_interval_ms": -1}, "sim_profile_file_invalid_min_call_interval_ms"),
        ({"min_call_interval_ms": True}, "sim_profile_file_invalid_min_call_interval_ms"),
    ],
)
def test_sim_profile_file_invalid_sections_are_refused(change: dict[str, Any], code: str) -> None:
    assert file_code(dumps({**profile_file_doc(), **change})) == code


def test_sim_profile_file_refusals_never_quote_a_value() -> None:
    document = profile_file_doc()
    document["voices"]["caller-a"]["speaker"] = CANARY + " bad speaker"
    document["agreement"]["normaliser"] = CANARY
    for text in (dumps(document), "{" + CANARY, dumps({**profile_file_doc(), CANARY: 1})):
        with pytest.raises(sp().SimProfileError) as caught:
            sp().parse_sim_profile_file(text)
        assert CANARY not in str(caught.value)
        assert CANARY not in repr(caught.value)


# ----------------------------------------------------------------------------- the stored document


def test_build_sim_profile_stores_the_voice_of_the_run_persona_only() -> None:
    """ADR 0027 rule 13: the input file may map several personas; only the entry used is stored."""
    document = profile_file_doc()
    other = dict(document["voices"]["caller-a"], speaker="other-voice")
    document["voices"]["caller-b"] = other
    built = stored_profile(document, persona_key="caller-b")
    assert built["voice"]["speaker"] == "other-voice"
    assert "caller-a" not in json.dumps(built)
    assert "caller-b" not in json.dumps(built)
    assert "voices" not in built
    assert "agreement" not in built


def test_build_sim_profile_has_the_adr_members_and_the_caps_as_given() -> None:
    built = stored_profile(
        caps={"call_seconds": 90, "tts_chars": 400, "llm_tokens": None, "stt_seconds": 60}
    )
    assert set(built) == set(sp().SIM_PROFILE_JSON_SCHEMA["required"]) | {"format"}
    assert built["format"] == "tracer.sim_profile/1"
    assert built["limits"] == {
        "max_call_seconds": 90,
        "max_tts_chars": 400,
        "max_llm_tokens": None,
        "max_stt_seconds": 60,
        "min_call_interval_ms": 0,
    }
    assert built["connection"] == CONNECTION_NONE
    assert built["barge_ins"] == []


def test_build_sim_profile_refuses_a_persona_without_a_voice_and_bad_caps() -> None:
    parsed = sp().parse_sim_profile_file(dumps(profile_file_doc()))
    with pytest.raises(sp().SimProfileError) as no_voice:
        sp().build_sim_profile(
            parsed, persona_key="nobody", connection=CONNECTION_NONE, caps=NO_CAPS
        )
    assert no_voice.value.code == "voice_missing_for_persona"
    assert "nobody" not in str(no_voice.value)
    with pytest.raises(sp().SimProfileError) as bad_caps:
        sp().build_sim_profile(
            parsed, persona_key="caller-a", connection=CONNECTION_NONE, caps={"call_seconds": 1}
        )
    assert bad_caps.value.code == "caps_invalid"


@pytest.mark.parametrize("dialect", ["pipecat-protobuf/1", "twilio-media-stream/1"])
def test_a_valid_stored_profile_validates_against_the_schema_and_the_hand_validator(
    dialect: str,
) -> None:
    """Both runnable dialects: ``jsonschema`` and the hand-written validator accept the document."""
    built = stored_profile(profile_file_doc(dialect))
    Draft202012Validator(dict(sp().SIM_PROFILE_JSON_SCHEMA)).validate(built)
    sp().validate_sim_profile(built)
    assert built["dialect"] == dialect


def test_the_stored_profile_holds_no_text_only_machine_values() -> None:
    """ADR 0027 invariant I1: a variable NAME, an origin, keys, integers and booleans."""
    built = stored_profile(
        connection={
            "auth_token_env": "AGENT_TOKEN",
            "origin": "https://agent.example",
            "bot_holds_telephony_credentials": None,
        }
    )
    strings: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(built)
    assert all(re.fullmatch(r"[A-Za-z0-9._:/@+-]{1,64}", s) for s in strings), strings
    assert built["connection"]["auth_token_env"] == "AGENT_TOKEN"  # noqa: S105 - a NAME


def test_validate_agrees_with_jsonschema_on_every_structural_fault() -> None:
    """The hand validator and ``jsonschema`` refuse the same structural faults, and the refusal is
    ``schema:<pointer>`` with property names only."""
    base = stored_profile()
    validator = Draft202012Validator(dict(sp().SIM_PROFILE_JSON_SCHEMA))
    faults: list[tuple[str, Any]] = []

    def mutate(path: list[Any], value: Any, label: str) -> None:
        document = copy.deepcopy(base)
        target = document
        for step in path[:-1]:
            target = target[step]
        if value is KeyError:
            del target[path[-1]]
        else:
            target[path[-1]] = value
        faults.append((label, document))

    mutate(["format"], "tracer.sim_profile/2", "format")
    mutate(["dialect"], "pipecat/1", "dialect")
    mutate(["connection", "origin"], "https://Upper.example", "origin upper case")
    mutate(["connection", "auth_token_env"], "lower", "env name")
    mutate(["audio", "channels"], 2, "channels")
    mutate(["audio", "frame_ms"], 10, "frame_ms")
    mutate(["audio", "sample_rate_hz"], 7999, "rate low")
    mutate(["turn_taking", "max_silence_ms"], 0, "max_silence zero")
    mutate(["turn_taking", "caller_speaks_first"], 1, "bool as int")
    mutate(["turn_taking", "wait_for_agent_silence_ms"], True, "int as bool")
    mutate(["turn_taking", "wait_for_agent_silence_ms"], 2**53, "above i-json")
    mutate(["voice", "pace"], "1.50", "pace not canonical")
    mutate(["voice", "language_code"], "hi-IN", "language")
    mutate(["voice", "codec"], "wav", "codec")
    mutate(["limits", "max_call_seconds"], 14401, "call seconds above four hours")
    mutate(["limits", "min_call_interval_ms"], None, "interval null")
    mutate(["limits"], KeyError, "limits missing")
    mutate(["stt", "max_chunk_ms"], 0, "chunk")
    mutate(["caller", "mode"], "robot", "caller mode")
    mutate(["barge_ins"], [{"turn_index": 0}], "barge missing")
    mutate(["extra"], 1, "extra key")
    assert len(faults) >= 20
    for label, document in faults:
        assert not validator.is_valid(document), label
        assert code_of(document).startswith("schema:"), label
    # and a pointer shows only property names
    broken = copy.deepcopy(base)
    broken["turn_taking"]["max_silence_ms"] = 0
    assert code_of(broken) == "schema:/turn_taking/max_silence_ms"


def test_the_empty_document_and_non_objects_are_refused() -> None:
    assert code_of({}).startswith("schema:")
    assert code_of([]).startswith("schema:")
    assert code_of(None).startswith("schema:")


# ----------------------------------------------------------------------- cross-field rules (ADR
# 0027)


def altered(base: dict[str, Any], *path_value: Any) -> dict[str, Any]:
    document = copy.deepcopy(base)
    *path, value = path_value
    target = document
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return document


def test_the_dialect_fixes_the_wire_codec_and_the_twilio_rate() -> None:
    proto = stored_profile()
    twilio = stored_profile(profile_file_doc("twilio-media-stream/1"))
    assert code_of(altered(proto, "audio", "wire_codec", "mulaw")) == "dialect_audio_mismatch"
    assert (
        code_of(altered(proto, "audio", "wire_codec", "webrtc_negotiated"))
        == "dialect_audio_mismatch"
    )
    assert code_of(altered(twilio, "audio", "wire_codec", "pcm_s16le")) == "dialect_audio_mismatch"
    assert code_of(altered(twilio, "audio", "sample_rate_hz", 16000)) == "dialect_audio_mismatch"
    webrtc = altered(proto, "dialect", "smallwebrtc/1")
    assert code_of(webrtc) == "dialect_audio_mismatch"
    webrtc["audio"]["wire_codec"] = "webrtc_negotiated"
    webrtc["connection"]["bot_holds_telephony_credentials"] = None
    sp().validate_sim_profile(webrtc)  # the codec the dialect names is valid


def test_the_voice_matches_the_wire() -> None:
    proto = stored_profile()
    twilio = stored_profile(profile_file_doc("twilio-media-stream/1"))
    assert code_of(altered(proto, "voice", "sample_rate_hz", 24000)) == "voice_audio_mismatch"
    assert code_of(altered(proto, "voice", "codec", "mulaw")) == "voice_audio_mismatch"
    assert code_of(altered(twilio, "voice", "codec", "linear16")) == "voice_audio_mismatch"
    assert code_of(altered(twilio, "voice", "sample_rate_hz", 16000)) == "voice_audio_mismatch"


def test_max_silence_is_at_least_the_wait() -> None:
    proto = stored_profile()
    assert code_of(altered(proto, "turn_taking", "max_silence_ms", 299)) == "max_silence_below_wait"
    sp().validate_sim_profile(
        altered(proto, "turn_taking", "max_silence_ms", 300)
    )  # equal is valid


def test_barge_ins_are_sorted_unique_and_name_scripted_turns() -> None:
    proto = stored_profile()

    def with_barge(indexes: list[int], caller_first: bool = False) -> dict[str, Any]:
        document = copy.deepcopy(proto)
        document["barge_ins"] = [{"turn_index": i, "barge_in_after_ms": 100} for i in indexes]
        document["turn_taking"]["caller_speaks_first"] = caller_first
        return dict(document)

    sp().validate_sim_profile(with_barge([1]), scripted_turn_count=2)
    sp().validate_sim_profile(with_barge([0, 1]), scripted_turn_count=2)
    assert code_of(with_barge([1, 0])) == "barge_ins_not_sorted_unique"
    assert code_of(with_barge([1, 1])) == "barge_ins_not_sorted_unique"
    assert code_of(with_barge([2]), scripted_turn_count=2) == "barge_in_turn_unknown"
    # without the evaluator's turn count the turn rule is not checked (the caller states it)
    sp().validate_sim_profile(with_barge([5]))
    # a barge-in has no onset to follow on the first line of a caller that speaks first
    assert code_of(with_barge([0], caller_first=True)) == "barge_in_on_first_line"


def test_a_real_engine_needs_its_cap_and_a_fake_one_does_not() -> None:
    """ADR 0026 rule 15 and ADR 0027: a cap is not null when its engine is real."""
    fake = stored_profile()
    sp().validate_sim_profile(fake)  # all fake: null caps are valid
    sarvam_tts = stored_profile(profile_file_doc(tts="sarvam"), caps={**NO_CAPS})
    # the builder does not judge caps; the validator does
    assert code_of(sarvam_tts) == "cap_missing_max_call_seconds"
    with_call = altered(sarvam_tts, "limits", "max_call_seconds", 60)
    assert code_of(with_call) == "cap_missing_max_tts_chars"
    sp().validate_sim_profile(altered(with_call, "limits", "max_tts_chars", 100))
    sarvam_stt = stored_profile(profile_file_doc(stt="sarvam"))
    assert (
        code_of(altered(sarvam_stt, "limits", "max_call_seconds", 60))
        == "cap_missing_max_stt_seconds"
    )
    llm = stored_profile(profile_file_doc(caller=llm_caller()))
    assert code_of(altered(llm, "limits", "max_call_seconds", 60)) == "cap_missing_max_llm_tokens"
    # and the command may validate without the caps rule, at its own step
    sp().validate_sim_profile(sarvam_tts, check_caps=False)


def test_the_prompt_template_hash_is_the_hash_of_the_named_template() -> None:
    """ADR 0028 rule 2 and 12: the hash recorded in the profile is the shipped template's."""
    llm = stored_profile(
        profile_file_doc(caller=llm_caller()),
        caps={**NO_CAPS, "call_seconds": 60, "llm_tokens": 999},
    )
    sp().validate_sim_profile(llm)
    bad = copy.deepcopy(llm)
    bad["caller"]["prompt"]["template_sha256"] = "0" * 64
    assert code_of(bad) == "prompt_hash_mismatch"
    unknown = copy.deepcopy(llm)
    unknown["caller"]["prompt"]["template_id"] = "tracer.caller_prompt/99"
    assert code_of(unknown) == "prompt_template_unknown"


def test_the_replay_and_scripted_callers_validate() -> None:
    sp().validate_sim_profile(stored_profile(profile_file_doc(caller=replay_caller())))
    sp().validate_sim_profile(stored_profile(profile_file_doc(caller={"mode": "scripted"})))


def test_the_connection_labels_follow_the_dialect() -> None:
    """ADR 0027: ``bot_holds_telephony_credentials`` is a boolean iff the dialect is the Twilio
    one, and in Phase 4 it is false (rule 6 refuses true before the call)."""
    twilio = stored_profile(profile_file_doc("twilio-media-stream/1"))
    assert twilio["connection"] == CONNECTION_TWILIO
    assert (
        code_of(altered(twilio, "connection", "bot_holds_telephony_credentials", None))
        == "connection_inconsistent"
    )
    assert (
        code_of(altered(twilio, "connection", "bot_holds_telephony_credentials", True))
        == "connection_inconsistent"
    )
    proto = stored_profile()
    assert (
        code_of(altered(proto, "connection", "bot_holds_telephony_credentials", False))
        == "connection_inconsistent"
    )


def test_validation_refusals_never_quote_a_value() -> None:
    """Whatever the fault, the message is the lower-case code."""
    document = stored_profile()
    document["voice"]["speaker"] = CANARY
    document["turn_taking"]["max_silence_ms"] = 1
    document["connection"]["auth_token_env"] = "NAME_" + "X" * 70
    for found in (
        code_of(document),
        code_of(altered(stored_profile(), "audio", "wire_codec", "mulaw")),
    ):
        assert CANARY not in found
        assert re.fullmatch(r"[a-z][a-z0-9_:/]*", found), found
