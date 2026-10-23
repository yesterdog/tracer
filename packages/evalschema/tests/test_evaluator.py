# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-09: Evaluator, ScriptedTurn, ExpectedToolCall, bindings and placeholders (ADR 0006, 0010).

Assumed public API (docs/phase1/api-surface.md section 19), ``tracer_evalschema.evaluator``:
``Evaluator`` (versioned; ``name``, ``language``, ``instructions``, ``expected_outcome``,
``persona`` ref, optional ``test_profile`` (a TestProfileRef), ``metric_bindings`` refs,
``scripted_turns``, ``expected_tool_calls``, ``max_duration_ms``), ``ScriptedTurn{index, text,
language}`` and ``ExpectedToolCall{name, match, order_index}``; ``tracer_evalschema.metrics.
check_bindings_unique(bindings)``. Profile placeholders are ``{{profile.<field>}}`` with the
fields of ``profile.profile_placeholder_fields()``.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import (
    EXCLUDED_LANGUAGE_TAGS,
    MIXED_ALIAS_TAG,
    assert_rejected,
    assert_rejected_name,
    build,
    cls,
    collect_enums,
    contains_key,
    entity_class,
    fields_of,
    jsonschema_module,
    make,
    mixed_alias_message,
    new_id,
    raw_document,
    ref,
    schema_accepts,
    schema_of,
)
from ._support_b3 import (
    QUOTE_MARK,
    assert_input_hidden,
    marked_id,
    marked_key,
    marked_set,
    marked_tag,
)


def _turn(index: int, text: str = "नमस्ते", language: Any = "hi-IN") -> dict[str, Any]:
    return {"index": index, "text": text, "language": language}


def _evaluator(**over: Any) -> Any:
    return make("Evaluator", **over)


def _assert_typed_name_hidden(error: ValidationError, name: str) -> None:
    """Owner decision (placeholder message): inside a model that hides its input the unknown
    profile placeholder error gives the field path and the rule only, never the typed field name.
    The name is absent from ``str``, ``repr``, ``errors(include_input=False)``,
    ``json(include_input=False)`` (pydantic always puts the input in the plain forms, ADR 0007
    Decision 8 (d)) and every error context. A short name that is also a word of the rule text is
    checked in the forms in which a message would quote it."""
    rule = "unknown profile placeholder field"
    texts = [
        str(error),
        repr(error),
        repr(error.errors(include_input=False)),
        error.json(include_input=False),
        repr([e.get("ctx") for e in error.errors()]),
    ]
    fields = api("profile").profile_placeholder_fields()
    known_text = (
        " ".join(
            (
                rule,
                "a placeholder names a TestProfile field",
                *fields,
                "validation error for value_error",
            ),
        )
        + " profile_placeholder https://errors.pydantic.dev"
    )
    forms = [name] if name not in known_text else []
    forms += [f"profile.{name}", f"'{name}'", f'"{name}"', "{" + name + "}"]
    for text in texts:
        for form in forms:
            assert form not in text, f"the typed name {name!r} is shown as {form!r}: {text}"
    assert any(rule in str(e["msg"]) for e in error.errors()), error.errors()


# ------------------------------------------------------------------------ max duration


def _max_duration() -> int:
    node = schema_of(entity_class("Evaluator"))["properties"]["max_duration_ms"]
    maximum = node.get("maximum")
    assert isinstance(maximum, int), "the schema must state an upper bound"
    return maximum


def test_evaluator_max_duration_bounds() -> None:
    """SCOPE P1-09 / ADR 0010 Decision 8: ``max_duration_ms`` is required and bounded: a positive
    integer number of milliseconds up to a stated maximum (the schema's ``maximum``). The bound
    itself is the architect's to choose; it must exceed one minute."""
    maximum = _max_duration()
    assert maximum > 60_000
    for good in (1, 300_000, maximum):
        assert _evaluator(max_duration_ms=good).max_duration_ms == good
    for bad in (0, -1, maximum + 1, 1.5, 1.0, "300000", True, None):
        assert_rejected_name("Evaluator", max_duration_ms=bad, field="max_duration_ms")
    doc = {k: v for k, v in raw_document("Evaluator").items() if k != "max_duration_ms"}
    with pytest.raises(ValidationError):
        entity_class("Evaluator").model_validate(
            {**doc, "content_digest": build("Evaluator")["content_digest"]}
        )
    model = entity_class("Evaluator")
    assert schema_accepts(model, build("Evaluator", max_duration_ms=maximum))
    for bad_value in (0, maximum + 1):
        assert not schema_accepts(model, raw_document("Evaluator", max_duration_ms=bad_value))
    node = schema_of(model)["properties"]["max_duration_ms"]
    assert node["type"] == "integer"


# ----------------------------------------------------------------------- ScriptedTurn


def test_scripted_turn_index_contiguous() -> None:
    """ADR 0010 Decision 8: scripted turn indexes run 0, 1, 2, ... without gaps or repeats."""
    assert _evaluator(scripted_turns=[]).scripted_turns == []
    assert [t.index for t in _evaluator(scripted_turns=[_turn(0)]).scripted_turns] == [0]
    three = _evaluator(scripted_turns=[_turn(0), _turn(1), _turn(2)])
    assert [t.index for t in three.scripted_turns] == [0, 1, 2]
    for indexes in ([1], [1, 2], [0, 2], [0, 0, 1], [0, 1, 1], [0, 1, 3], [-1, 0], [2, 3, 4]):
        assert_rejected_name(
            "Evaluator",
            scripted_turns=[_turn(i) for i in indexes],
            field="scripted_turns",
        )
    turn = cls("evaluator", "ScriptedTurn")
    assert turn.model_validate(_turn(0)).index == 0
    for bad in (-1, 1.5, "0", True, None):
        with pytest.raises(ValidationError):
            turn.model_validate({**_turn(0), "index": bad})
    for field in ("index", "text", "language"):
        with pytest.raises(ValidationError):
            turn.model_validate({k: v for k, v in _turn(0).items() if k != field})
    with pytest.raises(ValidationError):
        turn.model_validate({**_turn(0), "extra": 1})
    assert not {"id", "schema_version", "created_at", "metadata"} & fields_of(turn)


def test_scripted_turn_language_supported() -> None:
    """ADR 0005 Decision 6: a scripted turn's (and the evaluator's) language is a
    ``SupportedLanguage``: native script, regions IN/US/GB, canonical case; excluded, romanized
    and mixed tags are rejected."""
    turn = cls("evaluator", "ScriptedTurn")
    for tag, canonical in (
        ("hi-IN", "hi-IN"),
        ("HI-in", "hi-IN"),
        ("ml", "ml"),
        ("pa-Guru-IN", "pa-Guru-IN"),
        ("en-GB", "en-GB"),
    ):
        assert turn.model_validate(_turn(0, language=tag)).language == canonical
        assert _evaluator(language=tag).language == canonical
    rejected = [
        "hi-Latn",
        MIXED_ALIAS_TAG,
        *EXCLUDED_LANGUAGE_TAGS,
        "pa-" + "Arab",
        "ta-LK",
        "en-AU",
        "hindi",
        "hi_IN",
        "und",
        "",
        None,
    ]
    for bad_tag in rejected:
        with pytest.raises(ValidationError):
            turn.model_validate(_turn(0, language=bad_tag))
        assert_rejected_name("Evaluator", language=bad_tag, field="language")
        assert_rejected_name("Evaluator", scripted_turns=[_turn(0, language=bad_tag)])
    assert mixed_alias_message() in str(assert_rejected_name("Evaluator", language=MIXED_ALIAS_TAG))
    mixed_script = "नमस्ते hello"
    assert turn.model_validate(_turn(0, text=mixed_script)).text == mixed_script


# ------------------------------------------------------------------------ placeholders


def test_scripted_turn_placeholder_stored_unresolved() -> None:
    """P-16: profile values appear in scripted turns and instructions only as
    ``{{profile.<field>}}`` placeholders; they are stored exactly as written, never resolved,
    also when the evaluator references a profile; other ``{{...}}`` text is ordinary text."""
    text = "मेरा नाम {{profile.name}} है और खाता {{profile.account_number}} है"
    turn = cls("evaluator", "ScriptedTurn").model_validate(_turn(0, text=text))
    assert turn.text == text
    assert turn.model_dump(mode="json")["text"] == text
    profile = build("TestProfile")
    instructions = "Say you are {{profile.name}}, born {{profile.dob}}."
    evaluator = _evaluator(
        instructions=instructions,
        scripted_turns=[
            _turn(0, text=text),
            _turn(1, text="Order {{order_number}} {{ }}", language="en-IN"),
        ],
        test_profile={
            "id": profile["id"],
            "version": profile["version"],
            "content_digest": profile["content_digest"],
        },
    )
    assert evaluator.instructions == instructions
    assert evaluator.scripted_turns[0].text == text
    assert evaluator.scripted_turns[1].text == "Order {{order_number}} {{ }}"
    dumped = evaluator.model_dump_json()
    for secret in ("Asha Verma", "1990-04-12", "4400123412"):
        assert secret not in dumped
    assert "{{profile.name}}" in dumped
    again = entity_class("Evaluator").model_validate_json(dumped)
    assert again.instructions == instructions
    assert again.content_digest == evaluator.content_digest


def test_profile_placeholder_unknown_field_rejected() -> None:
    """P-16 (accepted by the user in revision 6): a placeholder that names no TestProfile field
    is rejected, in scripted turns and in instructions. Owner decision (placeholder message): the
    message gives the rule ("unknown profile placeholder field") and the known fields, never the
    typed name."""
    turn = cls("evaluator", "ScriptedTurn")
    fields = api("profile").profile_placeholder_fields()
    for field in fields:
        assert turn.model_validate(_turn(0, text="x {{profile." + field + "}} y")).text
    for unknown in ("shoe_size", "nam", "dob_year", "password", "id", "version"):
        assert unknown not in fields
        text = "Hello {{profile." + unknown + "}}"
        with pytest.raises(ValidationError, match="unknown profile placeholder field") as caught:
            turn.model_validate(_turn(0, text=text))
        _assert_typed_name_hidden(caught.value, unknown)
        _assert_typed_name_hidden(
            assert_rejected_name("Evaluator", instructions=text, field="instructions"), unknown
        )
        mixed = "{{profile.name}} then {{profile." + unknown + "}}"
        _assert_typed_name_hidden(
            assert_rejected_name("Evaluator", scripted_turns=[_turn(0, text=mixed)]), unknown
        )
    for ordinary in (
        "{{customer}}",
        "{{ profile }}",
        "{profile.shoe_size}",
        "profile.shoe_size",
        "{{}}",
    ):
        assert turn.model_validate(_turn(0, text=ordinary)).text == ordinary


# --------------------------------------------------------------------- ExpectedToolCall


def _match_values() -> list[Any]:
    call = cls("evaluator", "ExpectedToolCall")
    schema = schema_of(call)
    return collect_enums(schema, schema["properties"]["match"])


MATCH_MEMBERS = ("name_only", "arguments_exact", "arguments_subset")
ARGUMENTS = {"order_id": "A-1", "amount": 12.5, "note": None, "items": [1, {"sku": "x"}]}


def _call(match: str = "name_only", **over: Any) -> dict[str, Any]:
    """An ``ExpectedToolCall`` document with the arguments its ``match`` needs (revision 8:
    ``arguments_exact`` and ``arguments_subset`` require ``arguments``; ``name_only`` forbids
    them)."""
    doc: dict[str, Any] = {"name": "refund_order", "match": match, "order_index": 0}
    if match != "name_only":
        doc["arguments"] = {"order_id": "A-1"}
    return {**doc, **over}


def test_expected_tool_call_match_enum() -> None:
    """ADR 0010 Decision 8: ``ExpectedToolCall.match`` is a closed enum (a string ``enum`` in
    the JSON Schema, never ``const``); ``name`` is an open machine key; ``order_index`` is a
    non-negative integer. Revision 8 fixes the members to ``name_only``, ``arguments_exact`` and
    ``arguments_subset``; the last two require ``arguments``, so the per-member loop and the
    evaluator embedding supply them."""
    call = cls("evaluator", "ExpectedToolCall")
    members = _match_values()
    assert len(set(members)) == len(members)
    assert set(members) == set(MATCH_MEMBERS)
    assert all(
        isinstance(m, str) and m == m.lower() and m.replace("_", "").isalnum() for m in members
    )
    assert not contains_key(schema_of(call), "const")
    good = _call("name_only")
    loaded = call.model_validate(good)
    assert (loaded.name, loaded.match, loaded.order_index) == ("refund_order", "name_only", 0)
    for member in members:
        assert call.model_validate(_call(member)).match == member
    for bad in ("unknown_mode", "NAME_ONLY", "name_only ", "", None, 1, ["x"], "exact", "subset"):
        with pytest.raises(ValidationError):
            call.model_validate({**good, "match": bad})
    for bad_index in (-1, 1.5, "0", True, None):
        with pytest.raises(ValidationError):
            call.model_validate({**good, "order_index": bad_index})
    for field in ("name", "match"):
        with pytest.raises(ValidationError):
            call.model_validate({k: v for k, v in good.items() if k != field})
    with pytest.raises(ValidationError):
        call.model_validate({**good, "api_key": "x"})
    assert not {"id", "schema_version", "created_at", "metadata"} & fields_of(call)
    evaluator = _evaluator(
        expected_tool_calls=[good, _call(members[-1], name="send_sms", order_index=1)]
    )
    assert [c.name for c in evaluator.expected_tool_calls] == ["refund_order", "send_sms"]
    assert_rejected_name("Evaluator", expected_tool_calls=[{**good, "match": "unknown_mode"}])


# ----------------------------------------------------------------------------- bindings


def test_bindings_unique() -> None:
    """ADR 0010 Decision 4 / SCOPE P1-09: an evaluator lists each binding once, and two bindings of
    one evaluator never name the same metric definition (any version, enabled or not)."""
    first, second = ref("mbd"), ref("mbd")
    assert len(_evaluator(metric_bindings=[first, second]).metric_bindings) == 2
    assert_rejected_name("Evaluator", metric_bindings=[first, first], field="metric_bindings")
    assert_rejected_name("Evaluator", metric_bindings=[first, {**first, "version": 2}])
    for bad in (ref("per"), {"id": first["id"]}, {**first, "version": 0}, new_id("mbd"), None):
        assert_rejected_name("Evaluator", metric_bindings=[bad], field="metric_bindings")
    metrics = api("metrics")
    evaluator_id = new_id("evl")
    definition = ref("met")
    one = make("MetricBinding", evaluator_id=evaluator_id, metric=definition)
    other = make("MetricBinding", evaluator_id=evaluator_id, metric=ref("met"))
    metrics.check_bindings_unique([one, other])
    metrics.check_bindings_unique([])
    elsewhere = make("MetricBinding", evaluator_id=new_id("evl"), metric=definition)
    metrics.check_bindings_unique([one, elsewhere])
    newer = make("MetricBinding", evaluator_id=evaluator_id, metric={**definition, "version": 2})
    disabled = make("MetricBinding", evaluator_id=evaluator_id, metric=definition, enabled=False)
    for clash in (newer, disabled, one):
        with pytest.raises(ValueError, match=definition["id"]):
            metrics.check_bindings_unique([one, clash])


# ------------------------------------------------------------------- Evaluator itself


def test_evaluator_expected_outcome_required() -> None:
    """Proposed name (ADR 0013 Decision 2: the expected outcome is an ordinary required boolean
    check with ``equals_true``; how the text and that check are tied together is an open question,
    api-surface section 19). The text itself is required and stored verbatim."""
    assert _evaluator().expected_outcome.startswith("The agent starts a refund")
    text = "एजेंट ने रिफंड शुरू किया \u200d और संदर्भ संख्या दी"
    assert _evaluator(expected_outcome=text).expected_outcome == text
    for bad in ("", None, 5):
        assert_rejected_name("Evaluator", expected_outcome=bad, field="expected_outcome")
    model = entity_class("Evaluator")
    assert "expected_outcome" in schema_of(model)["required"]
    for field in ("name", "instructions", "language", "persona", "metric_bindings"):
        assert field in schema_of(model)["required"], field


def test_evaluator_references_are_versioned_and_typed() -> None:
    """Proposed name (ADR 0009 Decision 4): the persona is referenced as ``{id, version}`` of the
    right kind; the optional test profile is a ``TestProfileRef`` holding no profile data."""
    persona = _evaluator().persona
    assert str(persona.id).startswith("per_")
    assert persona.version == 1
    for bad in (
        ref("evl"),
        {"id": ref("per")["id"]},
        {**ref("per"), "version": 0},
        new_id("per"),
        None,
    ):
        assert_rejected_name("Evaluator", persona=bad, field="persona")
    profile = {"id": new_id("tpf"), "version": 2}
    assert _evaluator(test_profile=profile).test_profile.version == 2
    assert _evaluator(test_profile=None).test_profile is None
    for bad in (
        {**profile, "name": "Asha Verma"},
        {"id": new_id("per"), "version": 2},
        {"id": profile["id"]},
        {"id": profile["id"], "version": 0},
    ):
        assert_rejected_name("Evaluator", test_profile=bad, field="test_profile")
    # no embedded profile data and no PII marker anywhere in the evaluator schema
    model = entity_class("Evaluator")
    assert not contains_key(schema_of(model), "x-pii")
    assert not {"persona_id", "test_profile_id"} & fields_of(model)
    assert schema_accepts(model, build("Evaluator", test_profile=profile))


def test_evaluator_text_fields_are_verbatim() -> None:
    """Proposed name. Free text (instructions, expected outcome, scripted turn text) is stored
    code point for code point; ``name`` is a label."""
    text = "क्\u200dष और क्\u200cष, ണ്\u200d, mixed: abc 123\nline two"
    loaded = _evaluator(instructions=text, scripted_turns=[_turn(0, text=text)])
    assert loaded.instructions == text
    assert loaded.scripted_turns[0].text == text
    for bad in ("", None, 3):
        assert_rejected_name("Evaluator", instructions=bad, field="instructions")
    for bad_name in ("", " lead", "tail ", "a\u200bb", "a\u202eb", "a\nb", None):
        assert_rejected_name("Evaluator", name=bad_name, field="name")
    assert _evaluator(name="ണ്\u200d refund").name.endswith("refund")


# ================================================================================ revision 8
# Pins api-surface 19.4 / 19.4.1 (revision 8) and the owner's answer to E7.


def _profile_ref() -> dict[str, Any]:
    return {"id": new_id("tpf"), "version": 1}


def test_evaluator_max_duration_schema_is_exact() -> None:
    """Resolution 6 (ADR 0002 Decision 2): the property schema is exactly ``{"type": "integer",
    "minimum": 0, "exclusiveMinimum": 0, "maximum": 14400000}`` (1 ms to 4 hours) in both schema
    modes. The ``Ms`` base (``type`` integer, ``minimum`` 0) stays unchanged and the field only adds
    tightening keywords, so the batch 1 walk ``test_ms_field_names_are_integers_schema`` holds
    (it runs over this field too). Draft 2020-12 semantics are checked with ``jsonschema``."""
    model = entity_class("Evaluator")
    exact = {"type": "integer", "minimum": 0, "exclusiveMinimum": 0, "maximum": 14_400_000}
    jsonschema = jsonschema_module()
    for mode in ("validation", "serialization"):
        node = schema_of(model, mode)["properties"]["max_duration_ms"]
        validation_keywords = {k: v for k, v in node.items() if k not in {"title", "description"}}
        assert validation_keywords == exact, mode
        jsonschema.Draft202012Validator.check_schema(node)
        validator = jsonschema.Draft202012Validator(node)
        for good in (1, 60_000, 14_400_000):
            assert validator.is_valid(good), good
        for bad in (0, -1, 14_400_001, 1.5, "1", True, None):
            assert not validator.is_valid(bad), bad
        assert node["type"] == "integer"
        assert node["minimum"] == 0
    assert _evaluator(max_duration_ms=14_400_000).max_duration_ms == 14_400_000
    assert_rejected_name("Evaluator", max_duration_ms=14_400_001, field="max_duration_ms")
    assert_rejected_name("Evaluator", max_duration_ms=0, field="max_duration_ms")


def test_evaluator_requires_at_least_one_binding() -> None:
    """Section 19.4.1: ``metric_bindings`` is non-empty, because every evaluator has an
    expected-outcome check (resolution 7), and the schema states ``minItems``. Proposed name."""
    assert len(_evaluator(metric_bindings=[ref("mbd")]).metric_bindings) == 1
    assert_rejected_name("Evaluator", metric_bindings=[], field="metric_bindings")
    model = entity_class("Evaluator")
    assert not schema_accepts(model, raw_document("Evaluator", metric_bindings=[]))
    assert schema_of(model)["properties"]["metric_bindings"]["minItems"] >= 1


# ------------------------------------------------------------------- expected tool calls


def test_expected_tool_call_arguments_rules() -> None:
    """Resolution 5 (ADR 0010 Decision 8): ``ExpectedToolCall{name, match, arguments?,
    order_index}``. ``name_only`` forbids ``arguments``; ``arguments_exact`` requires them (``{}``
    means "called with no arguments"); ``arguments_subset`` requires a non-empty object. The
    arguments are I-JSON values stored unchanged, and the JSON Schema states the same conditions.
    Proposed name."""
    call = cls("evaluator", "ExpectedToolCall")
    assert fields_of(call) == {"name", "match", "arguments", "order_index"}
    assert call.model_validate(_call("name_only")).arguments is None
    assert call.model_validate(_call("name_only", arguments=None)).arguments is None
    for forbidden in ({}, {"a": 1}, ARGUMENTS):
        with pytest.raises(ValidationError):
            call.model_validate(_call("name_only", arguments=forbidden))
        assert not schema_accepts(call, _call("name_only", arguments=forbidden))
    exact, subset = "arguments_exact", "arguments_subset"
    assert call.model_validate(_call(exact, arguments={})).arguments == {}
    assert schema_accepts(call, _call(exact, arguments={}))
    for match in (exact, subset):
        loaded = call.model_validate(_call(match, arguments=ARGUMENTS))
        assert loaded.arguments == ARGUMENTS
        assert loaded.model_dump(mode="json")["arguments"] == ARGUMENTS
        assert call.model_validate_json(loaded.model_dump_json()) == loaded
        assert schema_accepts(call, _call(match, arguments=ARGUMENTS))
        for missing in (None, "absent"):
            doc = _call(match)
            if missing is None:
                doc["arguments"] = None
            else:
                del doc["arguments"]
            with pytest.raises(ValidationError):
                call.model_validate(doc)
            assert not schema_accepts(call, doc)
        for not_an_object in ([], [1], "x", 5, True):
            with pytest.raises(ValidationError):
                call.model_validate(_call(match, arguments=not_an_object))
            assert not schema_accepts(call, _call(match, arguments=not_an_object))
    with pytest.raises(ValidationError):
        call.model_validate(_call(subset, arguments={}))
    assert not schema_accepts(call, _call(subset, arguments={}))
    errors = api("errors")
    for bad_value in ({1, 2}, b"bytes", float("nan"), float("inf")):
        with pytest.raises((ValidationError, errors.NonIJsonValueError)):
            call.model_validate(_call(exact, arguments={"x": bad_value}))
    # embedded in an evaluator, in the same documents
    ok = [_call(exact, arguments={}), _call(subset, arguments=ARGUMENTS, order_index=0)]
    assert len(_evaluator(expected_tool_calls=ok).expected_tool_calls) == 2
    assert_rejected_name("Evaluator", expected_tool_calls=[_call(subset, arguments={})])
    assert_rejected_name("Evaluator", expected_tool_calls=[_call("name_only", arguments={})])


def test_expected_tool_call_order_ranks_contiguous() -> None:
    """Resolution 5 (ADR 0010 Decision 8): ``order_index`` is a rank. Calls of rank r must all be
    observed before any call of rank r + 1, calls of equal rank in any order, and the ranks used by
    an evaluator form 0..R without gaps, so "unordered" is "all 0". Several calls may share a rank
    and a name (a tool called twice). Proposed name."""

    def calls(*ranks: int) -> list[dict[str, Any]]:
        return [_call(order_index=rank, name=f"tool_{i}") for i, rank in enumerate(ranks)]

    for good in ((), (0,), (0, 0, 0), (0, 1, 2), (0, 0, 1, 2, 2), (0, 1, 1)):
        assert len(_evaluator(expected_tool_calls=calls(*good)).expected_tool_calls) == len(good)
    twice = [_call(order_index=0), _call(order_index=0)]
    assert len(_evaluator(expected_tool_calls=twice).expected_tool_calls) == 2
    for bad in ((1,), (0, 2), (1, 2), (0, 0, 2), (2, 3), (0, 1, 3), (0, 2, 2)):
        assert_rejected_name(
            "Evaluator", expected_tool_calls=calls(*bad), field="expected_tool_calls"
        )


# ------------------------------------------------------------------------ placeholders


MALFORMED_PLACEHOLDERS = (
    "{{ profile.name }}",
    "{{ profile.name}}",
    "{{profile.name }}",
    "{{Profile.name}}",
    "{{PROFILE.name}}",
    "{{profile.Name}}",
    "{{profile.}}",
    "{{profile.name}",
    "{{profile.name",
    "{{profile.name.first}}",
    "{{profile .name}}",
    "{{profile. name}}",
    "{{profile.1name}}",
    "{{profile.na-me}}",
    "{{ Profile . name }}",
)
ORDINARY_BRACES = (
    "{{customer}}",
    "{{ profile }}",
    "{{profile}}",
    "{{}}",
    "{{ }}",
    "{profile.name}",
    "profile.name",
    "{{profiles.name}}",
    "{{myprofile.name}}",
    chr(0xFF5B) * 2 + "profile.name" + chr(0xFF5D) * 2,  # full-width braces
    "{{profile" + chr(0xFF0E) + "name}}",  # full-width full stop
)


def _text_places() -> list[tuple[str, Any]]:
    """The four places a profile placeholder may stand (E7), as builders of an Evaluator document
    from one text: instructions, expected outcome, scripted turn text, a string argument."""
    return [
        ("instructions", lambda text: {"instructions": text}),
        ("expected_outcome", lambda text: {"expected_outcome": text}),
        ("scripted_turns", lambda text: {"scripted_turns": [_turn(0, text=text)]}),
        (
            "expected_tool_calls",
            lambda text: {
                "expected_tool_calls": [_call("arguments_exact", arguments={"value": text})]
            },
        ),
    ]


def test_malformed_profile_placeholder_rejected() -> None:
    """Resolution 10 (ADR 0006 Decision 9a): a placeholder is exactly ``{{profile.`` + FIELD +
    ``}}`` with FIELD matching ``[a-z][a-z0-9_]*`` and no white space inside. Text that looks like
    a placeholder but is not well formed (``{{ profile.name }}``, ``{{Profile.name}}``,
    ``{{profile.}}``, ``{{profile.name}``, ``{{profile.name.first}}``) is rejected, because it would
    reach the simulated caller as literal braces. Everything else is ordinary text, and no Unicode
    normalization is applied to find placeholders. Applies to scripted turns, instructions, the
    expected outcome and string arguments (E7). Proposed name."""
    turn = cls("evaluator", "ScriptedTurn")
    call = cls("evaluator", "ExpectedToolCall")
    for text in MALFORMED_PLACEHOLDERS:
        wrapped = f"Say {text} now"
        with pytest.raises(ValidationError):
            turn.model_validate(_turn(0, text=wrapped))
        with pytest.raises(ValidationError):
            call.model_validate(_call("arguments_exact", arguments={"value": wrapped}))
        for field, build_over in _text_places():
            (
                assert_rejected_name(
                    "Evaluator", test_profile=_profile_ref(), **build_over(wrapped)
                ),
                (field, text),
            )
    ok = "Say {{profile.name}} then {{profile.account_number}}{{profile.dob}}"
    for text in ORDINARY_BRACES:
        assert turn.model_validate(_turn(0, text=text)).text == text
        assert call.model_validate(_call("arguments_exact", arguments={"value": text}))
        for _field, build_over in _text_places():
            assert _evaluator(**build_over(f"{text} {ok}"), test_profile=_profile_ref())
    # an unknown FIELD is still a rejection, with the rule and without the typed name (P-16 as
    # changed by the owner decision on the placeholder message)
    with pytest.raises(ValidationError, match="unknown profile placeholder field") as caught:
        turn.model_validate(_turn(0, text="{{profile.shoe_size}}"))
    _assert_typed_name_hidden(caught.value, "shoe_size")


def test_profile_placeholder_requires_test_profile() -> None:
    """Resolution 10, N8 (ADR 0006 Decision 9a, ADR 0010 Decision 8): an Evaluator with any profile
    placeholder, in instructions, the expected outcome, a scripted turn or a string argument at any
    depth, must reference a ``test_profile``, because nothing else could fill it. Evaluators
    without a placeholder need none, and other ``{{...}}`` text is ordinary. Proposed name."""
    placeholder = "Say {{profile.name}} to the agent"
    nested = {"customer": {"ids": ["x", "{{profile.account_number}}"]}, "n": 3}
    places = [*_text_places(), ("nested argument", None)]
    for field, build_over in places:
        if build_over is None:
            over = {
                "expected_tool_calls": [_call("arguments_subset", arguments=nested)],
            }
        else:
            over = build_over(placeholder)
        for missing in ({}, {"test_profile": None}):
            assert_rejected(entity_class("Evaluator"), raw_document("Evaluator", **over, **missing))
        loaded = _evaluator(**over, test_profile=_profile_ref())
        assert loaded.test_profile is not None, field
    plain = _evaluator(
        instructions="Ask for {{order_number}} politely.",
        scripted_turns=[_turn(0, text="{{ }} and {{customer}}")],
    )
    assert plain.test_profile is None
    assert _evaluator(test_profile=_profile_ref()).test_profile is not None


def test_profile_placeholders_in_outcome_and_tool_arguments() -> None:
    """E7 (accepted by the owner; widens P-16): profile placeholders are also allowed in
    ``expected_outcome`` and in the string values of ``ExpectedToolCall.arguments`` (at any depth),
    so that an account number need not be written literally into an evaluator that is copied into
    every snapshot. They are stored unresolved and obey the same grammar and field check as in
    instructions. Only string values can hold a placeholder: numbers cannot. Proposed name."""
    call = cls("evaluator", "ExpectedToolCall")
    arguments = {
        "account_number": "{{profile.account_number}}",
        "customer": {"name": "{{profile.name}}", "tags": ["a", "{{profile.dob}}"]},
        "limit": 3,
    }
    for match in ("arguments_exact", "arguments_subset"):
        loaded = call.model_validate(_call(match, arguments=arguments))
        assert loaded.arguments == arguments
        assert loaded.model_dump(mode="json")["arguments"] == arguments
    outcome = "The agent greets {{profile.name}} and confirms {{profile.account_number}}."
    evaluator = _evaluator(
        expected_outcome=outcome,
        expected_tool_calls=[_call("arguments_exact", arguments=arguments)],
        test_profile=_profile_ref(),
    )
    assert evaluator.expected_outcome == outcome
    assert evaluator.expected_tool_calls[0].arguments == arguments
    dumped = evaluator.model_dump_json()
    for secret in ("Asha Verma", "1990-04-12", "4400123412"):
        assert secret not in dumped
    again = entity_class("Evaluator").model_validate_json(dumped)
    assert again.expected_outcome == outcome
    assert again.content_digest == evaluator.content_digest
    for unknown in ("shoe_size", "nam", "password", "id"):
        text = "{{profile." + unknown + "}}"
        with pytest.raises(ValidationError, match="unknown profile placeholder field") as one:
            call.model_validate(_call("arguments_exact", arguments={"x": text}))
        _assert_typed_name_hidden(one.value, unknown)
        with pytest.raises(ValidationError, match="unknown profile placeholder field") as two:
            call.model_validate(_call("arguments_exact", arguments={"x": {"y": [text]}}))
        _assert_typed_name_hidden(two.value, unknown)
        _assert_typed_name_hidden(
            assert_rejected_name(
                "Evaluator", expected_outcome="Greets " + text, test_profile=_profile_ref()
            ),
            unknown,
        )
    # dictionary keys are names, not text that is filled in
    assert call.model_validate(_call("arguments_exact", arguments={"{{customer}}": 1}))


def test_evaluator_references_reject_text_forms() -> None:
    """ADR 0009 Decision 4 (I9a, revision 8): every reference of an evaluator is a JSON object
    ``{id, version, content_digest?}``; the persona, each binding and the test profile are rejected
    as a bare string, an ``id@version`` text or a bare ID. Proposed name."""
    persona, binding, profile = new_id("per"), new_id("mbd"), new_id("tpf")
    for text in (persona, f"{persona}@1", "persona@1"):
        assert_rejected_name("Evaluator", persona=text, field="persona")
    for text in (binding, f"{binding}@1", "goal_completion@1"):
        assert_rejected_name("Evaluator", metric_bindings=[text], field="metric_bindings")
    for text in (profile, f"{profile}@1", "profile@1"):
        assert_rejected_name("Evaluator", test_profile=text, field="test_profile")


# ------------------------------------------------------------------------------ no-echo (N41)


def test_definition_models_errors_do_not_echo_input() -> None:
    """N41 (owner, revision 13; ADR 0007 Decision 8 (b); api-surface 22.9 item 17):
    ``ScriptedTurn``, ``ExpectedToolCall``, ``Persona``, ``MetricDefinition``, ``MetricBinding``
    and ``Rubric`` hide their input in validation errors also when they are validated on their
    own (inside an Evaluator or a snapshot the outermost model governs). A rejected document that
    holds a marker string does not show it in ``str``, ``repr``, ``errors(include_input=False)``
    or ``json(include_input=False)``, through ``model_validate`` and ``model_validate_json``, and
    the error is still located at the field. Seed (a), for all six: an unknown field whose value
    is the marker (``extra_forbidden``). Seed (b): a free-text field holding the marker next to
    something the model rejects: the text of a scripted turn and a string argument of an expected
    tool call (each with the malformed placeholder ``{{profile.nam``) and ``Persona.accent`` (with
    U+202E). Proposed name (SCOPE section 5, P1-09; also P1-07, P1-08 and the Rubric of P1-22)."""
    mark = QUOTE_MARK
    models = {
        "ScriptedTurn": cls("evaluator", "ScriptedTurn"),
        "ExpectedToolCall": cls("evaluator", "ExpectedToolCall"),
        "Persona": entity_class("Persona"),
        "MetricDefinition": entity_class("MetricDefinition"),
        "MetricBinding": entity_class("MetricBinding"),
        "Rubric": entity_class("Rubric"),
    }
    for name, model in models.items():
        assert model.model_config.get("hide_input_in_errors") is True, name
    documents: dict[str, dict[str, Any]] = {
        "ScriptedTurn": _turn(0),
        "ExpectedToolCall": _call("name_only"),
        "Persona": raw_document("Persona"),
        "MetricDefinition": raw_document("MetricDefinition"),
        "MetricBinding": raw_document("MetricBinding"),
        "Rubric": raw_document("Rubric"),
    }
    for name, model in models.items():
        errors = assert_input_hidden(
            model, {**documents[name], "unexpected_note": mark}, mark, field="unexpected_note"
        )
        for error in errors:
            assert any(e["type"] == "extra_forbidden" for e in error.errors()), name
    text = f"{mark} said this {{{{profile.nam"
    assert_input_hidden(models["ScriptedTurn"], _turn(0, text=text), mark, field="text")
    assert_input_hidden(
        models["ExpectedToolCall"],
        _call("arguments_exact", arguments={"value": text}),
        mark,
        field="arguments",
    )
    assert_input_hidden(
        models["Persona"],
        raw_document("Persona", accent=mark + chr(0x202E)),
        mark,
        field="accent",
    )


def test_evaluator_errors_do_not_quote_rejected_values() -> None:
    """N42 (owner, option A; ADR 0007 Decision 8 (c) extended; api-surface 22.12): no shared
    validator quotes a rejected value in any field of a model that hides its input; an error
    gives the field location and the rule only. A rejected language
    tag, typed ID inside a reference, closed-set member (``match``), MachineKey (a tool name)
    and typed ID on ``Evaluator``, ``ScriptedTurn`` and ``ExpectedToolCall`` do not appear in
    ``str``, ``repr``, ``errors(include_input=False)`` or ``json(include_input=False)``, through
    ``model_validate`` and ``model_validate_json``, and the error is still located at the field.
    Proposed name (SCOPE section 5, P1-09)."""
    evaluator = entity_class("Evaluator")
    turn = cls("evaluator", "ScriptedTurn")
    call = cls("evaluator", "ExpectedToolCall")
    cases: list[tuple[str, Any, dict[str, Any], str]] = [
        ("BCP-47", evaluator, raw_document("Evaluator", language=marked_tag()), "language"),
        ("typed ID", evaluator, raw_document("Evaluator", id=marked_id("evl")), "id"),
        (
            "typed ID",
            evaluator,
            raw_document("Evaluator", persona={"id": marked_id("per"), "version": 1}),
            "persona",
        ),
        (
            "typed ID",
            evaluator,
            raw_document("Evaluator", metric_bindings=[{"id": marked_id("mbd"), "version": 1}]),
            "metric_bindings",
        ),
        (
            "BCP-47",
            evaluator,
            raw_document("Evaluator", scripted_turns=[_turn(0, language=marked_tag())]),
            "language",
        ),
        ("BCP-47", turn, _turn(0, language=marked_tag()), "language"),
        ("ClosedSet", call, {**_call("name_only"), "match": marked_set()}, "match"),
        ("MachineKey", call, _call("name_only", name=marked_key()), "name"),
    ]
    for label, model, doc, field in cases:
        assert_input_hidden(model, doc, QUOTE_MARK, field=field, label=label)
