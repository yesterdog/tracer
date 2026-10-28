# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-21 (the outcome of a simulated call): ``tracer.sim_outcome/1`` (ADR 0027 rules 1 and 12;
ADR 0026 rules 9, 15 and 21).

Contract: docs/phase4/api-surface-batch-e.md section 5.3. The document says whether the call was
established, its one end reason, the cap that ended it, the SHA-256 of the authorisation reference
(never its text) and the counts; its ``outcome`` is always ``not_evaluated``. The shipped schema is
the ADR's block; the cross-field rules of the ADR are refusals with a stable code.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped,unused-ignore]

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]
from evalcore_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    AUTH_TEXT,
    counts,
    mod,
    outcome_document,
)

END_REASONS = (
    "script_complete",
    "caller_end_call",
    "agent_hangup",
    "max_duration",
    "max_turns",
    "max_silence",
    "kill_switch",
    "setup_failed",
    "transport_error",
    "brain_invalid_output",
    "spend_cap",
    "brain_failed",
)


def so() -> Any:
    return mod("simoutcome")


def code_of(document: Any, **options: Any) -> str:
    with pytest.raises(so().SimOutcomeError) as caught:
        so().validate_sim_outcome(document, **options)
    assert str(caught.value) == caught.value.code
    return str(caught.value.code)


def test_shipped_sim_outcome_schema_equals_adr0027_block() -> None:
    section = sup.heading_section(
        sup.ADR27.read_text("utf-8"), "## The `tracer.sim_outcome/1` format"
    )
    blocks = re.findall(r"```json\n(.*?)\n```", section, re.DOTALL)
    assert len(blocks) == 1
    shipped = so().SIM_OUTCOME_JSON_SCHEMA
    assert json.loads(json.dumps(shipped)) == json.loads(blocks[0])
    assert shipped["title"] == so().SIM_OUTCOME_FORMAT == "tracer.sim_outcome/1"
    assert so().SIM_OUTCOME_METADATA_KEY == "tracer.sim_outcome"
    Draft202012Validator.check_schema(dict(shipped))


def test_the_authorisation_reference_is_stored_as_its_sha256_only() -> None:
    """ADR 0026 rule 21: lower-case hex SHA-256 of the UTF-8 bytes; the text is never in the
    document."""
    digest = so().authorisation_reference_sha256(AUTH_TEXT)
    assert digest == hashlib.sha256(AUTH_TEXT.encode("utf-8")).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    document = outcome_document()
    assert document["authorisation_reference_sha256"] == digest
    assert AUTH_TEXT not in json.dumps(document)
    # a different reference gives a different digest; a non-ASCII one hashes its UTF-8 bytes
    assert so().authorisation_reference_sha256(AUTH_TEXT + " ") != digest
    assert (
        so().authorisation_reference_sha256("\u0928")
        == hashlib.sha256("\u0928".encode()).hexdigest()
    )
    with pytest.raises(TypeError):
        so().authorisation_reference_sha256(b"bytes")


def test_a_built_outcome_validates_and_is_never_evaluated() -> None:
    document = outcome_document()
    Draft202012Validator(dict(so().SIM_OUTCOME_JSON_SCHEMA)).validate(document)
    so().validate_sim_outcome(document, brain_mode="scripted")
    assert document["outcome"] == "not_evaluated"
    assert document["started_at_clock"] == "simulator_wall_clock"
    assert document["format"] == "tracer.sim_outcome/1"
    assert set(document) == set(so().SIM_OUTCOME_JSON_SCHEMA["required"]) | {"format"}
    assert set(document["counts"]) == set(counts())


@pytest.mark.parametrize("end_reason", END_REASONS)
def test_every_end_reason_is_a_valid_record(end_reason: str) -> None:
    """The twelve end reasons of ADR 0026 rule 9, each recorded as itself."""
    if end_reason == "setup_failed":
        document = outcome_document(established=False)
    elif end_reason == "spend_cap":
        document = outcome_document(end_reason=end_reason, cap_reached="tts_chars")
    elif end_reason == "brain_failed":
        document = outcome_document(end_reason=end_reason, mode="llm")
    else:
        document = outcome_document(end_reason=end_reason)
    assert document["end_reason"] == end_reason
    so().validate_sim_outcome(document)


@pytest.mark.parametrize("cap", ["call_seconds", "tts_chars", "llm_tokens", "stt_seconds"])
def test_the_cap_that_ended_the_call_is_named(cap: str) -> None:
    document = outcome_document(end_reason="spend_cap", cap_reached=cap)
    assert document["cap_reached"] == cap
    assert code_of({**document, "cap_reached": "cpu"}).startswith("schema:")


def test_a_kill_switch_before_the_connection_is_a_valid_unestablished_call() -> None:
    """ADR 0027 cross-field rule: an unestablished call ends with setup_failed or kill_switch."""
    document = so().build_sim_outcome(
        dialect="pipecat-protobuf/1",
        call_established=False,
        end_reason="kill_switch",
        cap_reached=None,
        connected_ms=None,
        authorisation_reference_sha256=so().authorisation_reference_sha256(AUTH_TEXT),
        caller_text_origin="scripted",
        counts=counts(),
    )
    so().validate_sim_outcome(document)


def base() -> dict[str, Any]:
    return dict(outcome_document())


def mutated(*path_value: Any) -> dict[str, Any]:
    document = copy.deepcopy(base())
    *path, value = path_value
    target = document
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return document


@pytest.mark.parametrize(
    ("make", "code"),
    [
        (lambda: mutated("end_reason", "setup_failed"), "setup_failed_but_established"),
        (
            lambda: {
                **base(),
                "call_established": False,
                "end_reason": "script_complete",
                "connected_ms": None,
            },
            "not_established_inconsistent",
        ),
        (
            lambda: {
                **base(),
                "call_established": False,
                "end_reason": "setup_failed",
                "connected_ms": 5,
            },
            "not_established_inconsistent",
        ),
        (lambda: mutated("connected_ms", None), "established_without_connected_ms"),
        (lambda: mutated("cap_reached", "tts_chars"), "cap_reached_inconsistent"),
        (
            lambda: {**base(), "end_reason": "spend_cap", "cap_reached": None},
            "cap_reached_inconsistent",
        ),
        (lambda: mutated("counts", "caller_turns_completed", 3), "turns_completed_above_started"),
        (lambda: mutated("counts", "barge_ins_fired", 3), "barge_ins_above_turns"),
        (lambda: mutated("counts", "brain_regenerations", 1), "regenerations_above_invalid"),
        (lambda: mutated("counts", "brain_requests", 1), "scripted_with_brain_requests"),
        (lambda: mutated("counts", "llm_requests", 2), "scripted_with_brain_requests"),
    ],
)
def test_cross_field_rules_of_the_adr_are_refusals_with_a_stable_code(
    make: Callable[[], dict[str, Any]], code: str
) -> None:
    assert code_of(make()) == code


def test_the_text_origin_must_be_the_brain_mode_of_the_profile() -> None:
    document = outcome_document(mode="replay")
    so().validate_sim_outcome(document, brain_mode="replay")
    assert code_of(document, brain_mode="scripted") == "caller_text_origin_mismatch"
    llm = outcome_document(mode="llm", brain_requests=2, llm_requests=3)
    so().validate_sim_outcome(llm, brain_mode="llm")  # an LLM call may send requests


@pytest.mark.parametrize(
    ("path_value", "label"),
    [
        (("format", "tracer.sim_outcome/2"), "format"),
        (("outcome", "passed"), "outcome is always not_evaluated"),
        (("end_reason", "finished"), "end reason outside the closed list"),
        (("started_at_clock", "agent_clock"), "clock"),
        (("connected_ms", -1), "negative"),
        (("connected_ms", True), "bool as integer"),
        (("connected_ms", 2**53), "above i-json"),
        (("authorisation_reference_sha256", "A" * 64), "upper-case digest"),
        (("authorisation_reference_sha256", "a" * 63), "short digest"),
        (("caller_text_origin", "human"), "origin"),
        (("dialect", "smallwebrtc/2"), "dialect"),
        (("counts", {}), "counts empty"),
    ],
)
def test_structural_faults_are_schema_refusals(path_value: tuple[str, Any], label: str) -> None:
    document = copy.deepcopy(base())
    document[path_value[0]] = path_value[1]
    assert not Draft202012Validator(dict(so().SIM_OUTCOME_JSON_SCHEMA)).is_valid(document), label
    assert code_of(document).startswith("schema:"), label


def test_counts_are_closed_and_bounded() -> None:
    for name in counts():
        negative = mutated("counts", name, -1)
        assert code_of(negative) == f"schema:/counts/{name}"
    extra = mutated("counts", "phone_number", 1)
    assert code_of(extra) == "schema:/counts/phone_number"
    missing = copy.deepcopy(base())
    del missing["counts"]["frames_sent"]
    assert code_of(missing) == "schema:/counts/frames_sent"
    assert code_of({}).startswith("schema:")


def test_build_validates_before_returning() -> None:
    with pytest.raises(so().SimOutcomeError) as caught:
        so().build_sim_outcome(
            dialect="pipecat-protobuf/1",
            call_established=True,
            end_reason="setup_failed",
            cap_reached=None,
            connected_ms=1,
            authorisation_reference_sha256="a" * 64,
            caller_text_origin="scripted",
            counts=counts(),
        )
    assert caught.value.code == "setup_failed_but_established"


def test_the_document_holds_no_text() -> None:
    """ADR 0027 invariant I1: only enums, integers, booleans, null and one digest."""
    document = outcome_document()
    leaves: list[Any] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        else:
            leaves.append(value)

    walk(document)
    for leaf in leaves:
        assert (
            leaf is None
            or isinstance(leaf, bool | int)
            or (isinstance(leaf, str) and re.fullmatch(r"[A-Za-z0-9._:/@+-]{1,64}", leaf))
        ), leaf
