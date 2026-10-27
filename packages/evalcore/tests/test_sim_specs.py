# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-23 (the input files of ``tracer simulate``): evaluator, persona, test profile and target.

Contract: docs/phase4/api-surface-batch-e.md section 4. The command seals the real schema records
from these four small documents (the user never writes a content digest), so their shape is the
user's interface: strict JSON, a ``format`` member, a closed set of keys, no default for any value
(SCOPE: every value the user states, Tracer supplies none), and a refusal that is a stable code and
never a value. The module is loaded lazily, so before it exists each test fails on its own with
``ModuleNotFoundError``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from evalcore_p4e_support import (  # type: ignore[import-not-found,unused-ignore]
    LINE_0,
    LINE_1,
    dumps,
    evaluator_doc,
    mod,
    persona_doc,
    profile_spec_doc,
    target_doc,
)

CANARY = "".join(["SPEC", "CANARY", "q7"])


def spec() -> Any:
    return mod("simspec")


PARSERS = {
    "evaluator": ("parse_sim_evaluator", evaluator_doc),
    "persona": ("parse_sim_persona", persona_doc),
    "test_profile": ("parse_sim_test_profile", profile_spec_doc),
    "target": ("parse_sim_target", target_doc),
}


def refusal(kind: str, text: str) -> str:
    name, _ = PARSERS[kind]
    with pytest.raises(spec().SimSpecError) as caught:
        getattr(spec(), name)(text)
    assert str(caught.value) == caught.value.code
    return str(caught.value.code)


def test_sim_spec_files_parse_valid_documents() -> None:
    """The four documents of the base case parse to frozen values with every field as written."""
    ev = spec().parse_sim_evaluator(dumps(evaluator_doc()))
    assert ev.name == "synthetic helpline test"
    assert ev.language == "mr"
    assert ev.instructions == "Ask two short questions of the agent."
    assert ev.expected_outcome == "The agent answers both questions."
    assert [(t.index, t.text, t.language) for t in ev.scripted_turns] == [
        (0, LINE_0, "mr"),
        (1, LINE_1, "mr"),
    ]
    assert ev.max_duration_ms == 60_000
    per = spec().parse_sim_persona(dumps(persona_doc(accent="a regional accent")))
    assert (per.key, per.language, per.accent) == ("caller-a", "mr", "a regional accent")
    assert spec().parse_sim_persona(dumps(persona_doc())).accent is None
    prof = spec().parse_sim_test_profile(dumps(profile_spec_doc()))
    assert (prof.name, prof.dob, prof.account_number) == (
        "Asha Testkar",
        "2001-02-03",
        "TEST-0001",
    )
    tgt = spec().parse_sim_target(dumps(target_doc()))
    assert (tgt.name, tgt.url) == ("sample agent", "ws://127.0.0.1:8765/ws-client")
    # frozen: a parsed value cannot be edited into something the user did not write
    with pytest.raises(AttributeError):
        ev.name = "other"


def test_sim_spec_formats_are_the_documented_ones() -> None:
    """The format ids are part of the file contract (a new shape is a new ``/2``)."""
    s = spec()
    assert s.SIM_EVALUATOR_FORMAT == "tracer.sim_evaluator/1"
    assert s.SIM_PERSONA_FORMAT == "tracer.sim_persona/1"
    assert s.SIM_TEST_PROFILE_FORMAT == "tracer.sim_test_profile/1"
    assert s.SIM_TARGET_FORMAT == "tracer.sim_target/1"


@pytest.mark.parametrize("kind", sorted(PARSERS))
def test_sim_spec_refuses_what_is_not_a_document_of_its_kind(kind: str) -> None:
    """Not JSON, a duplicate key, NaN, a list, a wrong or missing format: each its own code."""
    _, make = PARSERS[kind]
    good = make()
    assert refusal(kind, "not json at all") == f"{kind}_not_json"
    assert refusal(kind, "") == f"{kind}_not_json"
    assert refusal(kind, '{"format": "x", "format": "y"}') == f"{kind}_not_json"
    assert refusal(kind, '{"max_duration_ms": NaN}') == f"{kind}_not_json"
    assert refusal(kind, "[]") == f"{kind}_shape"
    assert refusal(kind, '"text"') == f"{kind}_shape"
    assert refusal(kind, dumps({**good, "format": "tracer.sim_other/1"})) == f"{kind}_format"
    assert (
        refusal(kind, dumps({k: v for k, v in good.items() if k != "format"})) == f"{kind}_format"
    )
    assert refusal(kind, dumps({**good, "extra": 1})) == f"{kind}_unknown_key"


@pytest.mark.parametrize("kind", sorted(PARSERS))
def test_sim_spec_has_no_default_for_any_key(kind: str) -> None:
    """SCOPE: every value is the user's. Dropping any key is refused, naming the key."""
    _, make = PARSERS[kind]
    good = make()
    for key in good:
        if key == "format":
            continue
        document = {k: v for k, v in good.items() if k != key}
        assert refusal(kind, dumps(document)) == f"{kind}_missing_{key}"


INVALID = [
    ("evaluator", {"name": ""}, "evaluator_invalid_name"),
    ("evaluator", {"name": 3}, "evaluator_invalid_name"),
    ("evaluator", {"language": ""}, "evaluator_invalid_language"),
    ("evaluator", {"instructions": ""}, "evaluator_invalid_instructions"),
    ("evaluator", {"expected_outcome": None}, "evaluator_invalid_expected_outcome"),
    ("evaluator", {"scripted_turns": "x"}, "evaluator_invalid_scripted_turns"),
    (
        "evaluator",
        {"scripted_turns": [{"index": 1, "text": "a", "language": "mr"}]},
        "evaluator_invalid_scripted_turns",
    ),
    (
        "evaluator",
        {"scripted_turns": [{"index": True, "text": "a", "language": "mr"}]},
        "evaluator_invalid_scripted_turns",
    ),
    (
        "evaluator",
        {"scripted_turns": [{"index": 0, "text": "", "language": "mr"}]},
        "evaluator_invalid_scripted_turns",
    ),
    (
        "evaluator",
        {"scripted_turns": [{"index": 0, "text": "a"}]},
        "evaluator_invalid_scripted_turns",
    ),
    (
        "evaluator",
        {"scripted_turns": [{"index": 0, "text": "a", "language": "mr", "x": 1}]},
        "evaluator_invalid_scripted_turns",
    ),
    ("evaluator", {"max_duration_ms": 0}, "evaluator_invalid_max_duration_ms"),
    ("evaluator", {"max_duration_ms": 14_400_001}, "evaluator_invalid_max_duration_ms"),
    ("evaluator", {"max_duration_ms": True}, "evaluator_invalid_max_duration_ms"),
    ("evaluator", {"max_duration_ms": 1.5}, "evaluator_invalid_max_duration_ms"),
    ("persona", {"key": ""}, "persona_invalid_key"),
    ("persona", {"key": "Caller A"}, "persona_invalid_key"),
    ("persona", {"key": "a" * 65}, "persona_invalid_key"),
    ("persona", {"accent": ""}, "persona_invalid_accent"),
    ("persona", {"accent": 5}, "persona_invalid_accent"),
    ("persona", {"language": 7}, "persona_invalid_language"),
    ("test_profile", {"name": ""}, "test_profile_invalid_name"),
    ("test_profile", {"dob": None}, "test_profile_invalid_dob"),
    ("test_profile", {"account_number": ""}, "test_profile_invalid_account_number"),
    ("target", {"name": ""}, "target_invalid_name"),
    ("target", {"url": ""}, "target_invalid_url"),
    ("target", {"url": 5}, "target_invalid_url"),
]


@pytest.mark.parametrize(("kind", "change", "code"), INVALID)
def test_sim_spec_invalid_values_are_refused_with_the_key_in_the_code(
    kind: str, change: dict[str, Any], code: str
) -> None:
    """Each invalid value gets a code that names the key and nothing else."""
    _, make = PARSERS[kind]
    assert refusal(kind, dumps({**make(), **change})) == code


def test_sim_spec_boundaries_are_accepted() -> None:
    """The edges that are valid: one millisecond, four hours, an empty script, a 64-character
    key."""
    ev = spec().parse_sim_evaluator(dumps(evaluator_doc(max_duration_ms=1, scripted_turns=[])))
    assert ev.max_duration_ms == 1
    assert ev.scripted_turns == ()
    assert spec().parse_sim_evaluator(dumps(evaluator_doc(max_duration_ms=14_400_000)))
    assert spec().parse_sim_persona(dumps(persona_doc(key="a" * 64))).key == "a" * 64


def error_of(name: str, text: str) -> Any:
    """The refusal ``getattr(spec(), name)(text)`` raises, or ``None`` when the text is accepted."""
    try:
        getattr(spec(), name)(text)
    except spec().SimSpecError as error:
        return error
    return None


def test_sim_spec_refusals_never_quote_a_value() -> None:
    """A canary in every position of every document never appears in a refusal message."""
    refused = 0
    for name, make in PARSERS.values():
        for key, value in make().items():
            if key == "format":
                continue
            document = {**make(), key: CANARY if not isinstance(value, int) else -5}
            error = error_of(name, dumps(document))
            if error is not None:
                refused += 1
                assert CANARY not in str(error)
                assert CANARY not in repr(error)
                assert str(error) == error.code
    assert refused > 0  # at least one document was refused: the loop checked something
    for kind in PARSERS:
        assert CANARY not in refusal(kind, "{" + CANARY)
        assert CANARY not in refusal(kind, json.dumps({"format": CANARY}))
        assert CANARY not in refusal(kind, json.dumps({CANARY: 1}))


def test_resolve_profile_text_replaces_each_placeholder_once() -> None:
    """ADR 0006: ``{{profile.<field>}}`` only; a substituted value is never scanned again."""
    profile = spec().parse_sim_test_profile(dumps(profile_spec_doc(name="{{profile.dob}}")))
    resolve = spec().resolve_profile_text
    assert resolve("no placeholder", None) == "no placeholder"
    assert resolve("hello {{profile.name}}, {{profile.account_number}}", profile) == (
        "hello {{profile.dob}}, TEST-0001"
    )
    assert resolve("{{profile.dob}}{{profile.dob}}", profile) == "2001-02-032001-02-03"
    # look-alikes are text, not placeholders
    assert resolve("{{ profile.name }} {{profile.Name}} {profile.name}", profile) == (
        "{{ profile.name }} {{profile.Name}} {profile.name}"
    )


def test_resolve_profile_text_refuses_an_unknown_field_and_a_missing_profile() -> None:
    profile = spec().parse_sim_test_profile(dumps(profile_spec_doc()))
    with pytest.raises(spec().SimSpecError) as unknown:
        spec().resolve_profile_text("{{profile.mother_tongue}}", profile)
    assert unknown.value.code == "placeholder_unknown"
    with pytest.raises(spec().SimSpecError) as missing:
        spec().resolve_profile_text("{{profile.name}}", None)
    assert missing.value.code == "placeholder_without_profile"
    for error in (unknown.value, missing.value):
        assert "mother_tongue" not in str(error)
