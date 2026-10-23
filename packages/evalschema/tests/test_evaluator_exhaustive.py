# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch 17 (finding F5-2, owner decision): ``Evaluator.expected_tool_calls_exhaustive``.

``Evaluator.expected_tool_calls == []`` means "no expectation" (the field defaults to an empty
list), so Pipecat's ``calls: []`` ("the bot must call nothing") cannot be said with the list alone.
The decision adds ``expected_tool_calls_exhaustive: bool | None = None``:

* ``None``: not stated (nothing is said about calls other than the listed ones);
* ``True``: a tool call other than the expected ones is a failure; with an empty list it means
  "must call nothing";
* ``False``: stated, and not exhaustive (extra calls are allowed).

``[]`` with ``None`` or ``False`` is valid and means no expectation. The field is part of the
Evaluator content, so it is part of the content digest, and the exported schemas regenerate.

Existing tests and data that change when the field lands (named spec changes; none is edited
here): the golden fixtures (valid and maximal ``Evaluator``, and every document that embeds an
Evaluator: ``ConfigSnapshot``, ``RunBundle``, ``ExportBundle``) regenerate with
``scripts/build_golden_fixtures.py --write``; the exported schema files and ``schema.lock``
regenerate with the schema export ``--write``; the tests that pin the Evaluator property list or
the golden documents (``test_batch2_entities.py``, ``test_golden_fixtures.py``,
``test_schema_lock.py``, ``test_schema_export.py``) and ``scripts/property_coverage.py`` follow the
regenerated data. The last section pins the F5 mapping (docs/phase1/api-surface-mappings-2.md
section 3.3).
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import (
    assert_rejected,
    assert_rejected_name,
    build,
    entity_class,
    make,
    raw_document,
    schema_of,
    seal,
)
from ._support_b3 import (
    QUOTE_MARK,
    assert_input_hidden,
    entity_class3,
    snapshot_doc,
    snapshot_parts,
)
from ._support_b8 import rendered_schema, schema_validator
from ._support_b11 import EVALS, declarations, load_scenario_yaml, run_f5

FIELD = "expected_tool_calls_exhaustive"
RETRIEVED = "2026-10-23"
VALUES: tuple[bool | None, ...] = (None, True, False)
NOT_BOOLEAN: list[Any] = [
    "true",
    "false",
    "True",
    "yes",
    "",
    0,
    1,
    2,
    -1,
    1.0,
    0.0,
    [],
    [True],
    {},
    {"value": True},
]


def _call(name: str = "refund_order") -> dict[str, Any]:
    return {"name": name, "match": "name_only", "order_index": 0}


def _evaluator(**over: Any) -> Any:
    return make("Evaluator", **over)


def _known_field_control() -> None:
    """Positive control for every rejection test: the field exists and takes ``True``. Without it
    a rejection could be the unknown-field error (``extra_forbidden``) and the test would pass
    before the field is built, for the wrong reason."""
    assert FIELD in entity_class("Evaluator").model_fields
    assert getattr(_evaluator(**{FIELD: True}), FIELD) is True


def _not_unknown_field(error: ValidationError) -> None:
    """The error is about the value of the field, not about the field being unknown."""
    located = [e for e in error.errors() if FIELD in [str(p) for p in e["loc"]]]
    assert located, error.errors(include_input=False)
    assert all(e["type"] != "extra_forbidden" for e in located), located


def _types(node: dict[str, Any]) -> set[str]:
    """The JSON types a property schema allows (a plain ``type`` or an ``anyOf`` of them)."""
    found = {node["type"]} if isinstance(node.get("type"), str) else set()
    for branch in node.get("anyOf", []):
        found |= _types(branch)
    return found


def _evaluator_node(schema: dict[str, Any]) -> dict[str, Any]:
    """The Evaluator object schema of an exported file (the root, or ``$defs/Evaluator``)."""
    if schema.get("title") == "Evaluator" or "persona" in schema.get("properties", {}):
        return schema
    node: dict[str, Any] = schema["$defs"]["Evaluator"]
    return node


# ------------------------------------------------------------------------ field and default


def test_exhaustive_flag_is_a_field_that_defaults_to_not_stated() -> None:
    """The model has the field; a document that omits it validates and gets ``None``; the JSON
    dump always carries the key (a present ``null``), like the other optional Evaluator fields."""
    model = entity_class("Evaluator")
    assert FIELD in model.model_fields
    assert model.model_fields[FIELD].is_required() is False
    assert model.model_fields[FIELD].default is None
    evaluator = _evaluator()
    assert getattr(evaluator, FIELD) is None
    dumped = evaluator.model_dump(mode="json", by_alias=True)
    assert FIELD in dumped
    assert dumped[FIELD] is None
    # a sealed document without the key is the same evaluator: absent means the default
    sealed = build("Evaluator")
    stripped = {k: v for k, v in sealed.items() if k != FIELD}
    again = model.model_validate(stripped)
    assert getattr(again, FIELD) is None


def test_exhaustive_flag_is_in_the_schemas_as_boolean_or_null_and_not_required() -> None:
    """The validation-mode, serialization-mode and exported schemas all state the property as
    ``boolean`` or ``null``, with default ``null``, and it is not in ``required`` (so the schema
    accepts a document without it, as the model does)."""
    model = entity_class("Evaluator")
    exported = _evaluator_node(rendered_schema("Evaluator"))
    nodes = {
        "validation": schema_of(model, "validation"),
        "serialization": schema_of(model, "serialization"),
        "exported": exported,
    }
    for label, schema in nodes.items():
        properties = schema["properties"]
        assert FIELD in properties, label
        assert _types(properties[FIELD]) == {"boolean", "null"}, (label, properties[FIELD])
        assert properties[FIELD].get("default", "absent") is None, label
        assert FIELD not in schema.get("required", []), label


@pytest.mark.parametrize("entity", ["ConfigSnapshot", "RunBundle", "ExportBundle"])
def test_exhaustive_flag_is_in_every_schema_that_embeds_an_evaluator(entity: str) -> None:
    """Snapshot embedding: the exported files of the documents that embed an Evaluator describe
    the new property in their ``Evaluator`` definition, optional and boolean or null."""
    schema = rendered_schema(entity)
    node = schema["$defs"]["Evaluator"]
    assert _types(node["properties"][FIELD]) == {"boolean", "null"}
    assert FIELD not in node.get("required", [])


# --------------------------------------------------------------------------- values


@pytest.mark.parametrize("value", VALUES)
def test_exhaustive_flag_accepts_true_false_and_not_stated(value: bool | None) -> None:
    """Model and exported schema accept ``True``, ``False`` and ``None``; the value is kept
    exactly (``False`` is not turned into ``None``, ``True`` is a bool), through the Python and
    the JSON paths."""
    evaluator = _evaluator(**{FIELD: value})
    assert getattr(evaluator, FIELD) is value
    document = build("Evaluator", **{FIELD: value})
    assert document[FIELD] is value
    assert schema_validator(_evaluator_node(rendered_schema("Evaluator"))).is_valid(document)
    loaded = entity_class("Evaluator").model_validate_json(json.dumps(document))
    assert getattr(loaded, FIELD) is value


@pytest.mark.parametrize("bad", NOT_BOOLEAN, ids=[repr(b) for b in NOT_BOOLEAN])
def test_exhaustive_flag_rejects_every_value_that_is_not_a_boolean(bad: Any) -> None:
    """Strings, integers (also 0 and 1), floats, lists and objects are rejected by the model,
    located at the field, and by the exported schema (a JSON ``0`` or ``1`` is not a boolean)."""
    _known_field_control()
    _not_unknown_field(assert_rejected_name("Evaluator", field=FIELD, **{FIELD: bad}))
    validator = schema_validator(_evaluator_node(rendered_schema("Evaluator")))
    assert validator.is_valid({**build("Evaluator"), FIELD: True})
    assert not validator.is_valid({**build("Evaluator"), FIELD: bad})


def test_exhaustive_flag_rejects_a_boolean_in_json_text_form() -> None:
    """The JSON path is as strict as the Python path: ``"true"`` and ``1`` in JSON text."""
    _known_field_control()
    model = entity_class("Evaluator")
    for text in ('"true"', "1", "0", "[]", "{}"):
        document = json.loads(json.dumps(build("Evaluator")))
        raw = json.dumps({**document, FIELD: "x"}).replace('"x"', text)
        with pytest.raises(ValidationError) as caught:
            model.model_validate_json(raw)
        _not_unknown_field(caught.value)


# ----------------------------------------------------------- list and flag combinations


def test_exhaustive_true_with_an_empty_list_is_valid_and_means_call_nothing() -> None:
    """``expected_tool_calls == []`` with ``True``: the bot must call nothing. The list stays
    empty (no call is invented) and the flag is what carries the statement."""
    evaluator = _evaluator(expected_tool_calls=[], **{FIELD: True})
    assert evaluator.expected_tool_calls == []
    assert getattr(evaluator, FIELD) is True
    dumped = evaluator.model_dump(mode="json", by_alias=True)
    assert dumped["expected_tool_calls"] == []
    assert dumped[FIELD] is True
    document = build("Evaluator", expected_tool_calls=[], **{FIELD: True})
    assert schema_validator(_evaluator_node(rendered_schema("Evaluator"))).is_valid(document)


def test_exhaustive_true_with_listed_calls_is_valid() -> None:
    """``True`` with a non-empty list: the listed calls are expected and no other call is allowed.
    Both the list and the flag survive."""
    evaluator = _evaluator(
        expected_tool_calls=[_call("refund_order"), {**_call("send_sms"), "order_index": 1}],
        **{FIELD: True},
    )
    assert [c.name for c in evaluator.expected_tool_calls] == ["refund_order", "send_sms"]
    assert getattr(evaluator, FIELD) is True


@pytest.mark.parametrize("value", [None, False])
def test_exhaustive_false_or_not_stated_with_an_empty_list_means_no_expectation(
    value: bool | None,
) -> None:
    """``[]`` with ``False`` or ``None`` is valid (no expectation about tool calls), and the two
    stay distinguishable from ``True`` in the dump (the model does not normalise)."""
    evaluator = _evaluator(expected_tool_calls=[], **{FIELD: value})
    assert evaluator.expected_tool_calls == []
    assert getattr(evaluator, FIELD) is value
    assert evaluator.model_dump(mode="json")[FIELD] is value


def test_exhaustive_false_with_listed_calls_is_valid() -> None:
    evaluator = _evaluator(expected_tool_calls=[_call()], **{FIELD: False})
    assert len(evaluator.expected_tool_calls) == 1
    assert getattr(evaluator, FIELD) is False


# --------------------------------------------------------------------------- digest


def test_exhaustive_flag_is_part_of_the_content_digest() -> None:
    """Changing the flag changes ``content_digest``: ``None``, ``True`` and ``False`` give three
    different digests over otherwise identical content (so a verdict that depends on the flag
    cannot be replayed against an evaluator that says something else). Each sealed document
    verifies on load."""
    base = raw_document("Evaluator", expected_tool_calls=[])
    model = entity_class("Evaluator")
    digests: dict[bool | None, str] = {}
    for value in VALUES:
        sealed = seal(model, {**base, FIELD: value})
        assert sealed[FIELD] is value
        loaded = model.model_validate(sealed)  # the digest is verified here
        digests[value] = loaded.content_digest.value
    assert len(set(digests.values())) == 3, digests


def test_exhaustive_flag_cannot_be_changed_after_sealing() -> None:
    """A sealed Evaluator whose flag is flipped, set or removed without resealing fails digest
    verification with ``DigestMismatch``. The sealed documents themselves load first (so the
    mismatch is about the flag and not about the field being unknown)."""
    errors = api("errors")
    model = entity_class("Evaluator")
    base = raw_document("Evaluator", expected_tool_calls=[])
    for sealed_value, other in ((None, True), (None, False), (True, False), (False, True)):
        sealed = seal(model, {**base, FIELD: sealed_value})
        model.model_validate(sealed)
        with pytest.raises(errors.DigestMismatch):
            model.model_validate({**sealed, FIELD: other})
    sealed_true = seal(model, {**base, FIELD: True})
    model.model_validate(sealed_true)
    without = {k: v for k, v in sealed_true.items() if k != FIELD}
    with pytest.raises(errors.DigestMismatch):
        model.model_validate(without)


def test_exhaustive_flag_survives_a_json_round_trip_with_the_same_digest() -> None:
    """``model_dump_json`` then ``model_validate_json`` gives an equal Evaluator with the same
    value and the same digest, for every value (the digest verifies on the second load)."""
    model = entity_class("Evaluator")
    for value in VALUES:
        evaluator = _evaluator(expected_tool_calls=[], **{FIELD: value})
        text = evaluator.model_dump_json(by_alias=True)
        assert json.loads(text)[FIELD] is value
        again = model.model_validate_json(text)
        assert again == evaluator
        assert getattr(again, FIELD) is value
        assert again.content_digest == evaluator.content_digest
        assert json.loads(again.model_dump_json(by_alias=True)) == json.loads(text)


# ------------------------------------------------------------------- inputs stay hidden


def test_exhaustive_flag_errors_do_not_echo_input() -> None:
    """Hiding of input is unchanged (ADR 0007 Decision 8): a rejected flag value that is a marker
    string, and an unknown field next to a valid flag, show the marker in none of ``str``,
    ``repr``, ``errors(include_input=False)`` or ``json(include_input=False)``, through
    ``model_validate`` and ``model_validate_json``, and the error is located at the field."""
    _known_field_control()
    model = entity_class("Evaluator")
    assert model.model_config.get("hide_input_in_errors") is True
    for bad in (QUOTE_MARK, [QUOTE_MARK]):
        _not_unknown_field(assert_rejected(model, raw_document("Evaluator", **{FIELD: bad})))
    assert_input_hidden(
        model,
        raw_document("Evaluator", **{FIELD: QUOTE_MARK}),
        QUOTE_MARK,
        field=FIELD,
        label="marker as flag",
    )
    assert_input_hidden(
        model,
        raw_document("Evaluator", **{FIELD: [QUOTE_MARK]}),
        QUOTE_MARK,
        field=FIELD,
        label="marker in a list",
    )
    assert_input_hidden(
        model,
        raw_document("Evaluator", **{FIELD: True, "unexpected_note": QUOTE_MARK}),
        QUOTE_MARK,
        field="unexpected_note",
        label="unknown field beside a valid flag",
    )


def test_exhaustive_flag_does_not_change_the_profile_placeholder_rules() -> None:
    """The unknown-placeholder rule still gives the field path and the rule only, with the flag
    set (nothing about the new field leaks into, or silences, the existing checks)."""
    _known_field_control()
    assert_rejected_name(
        "Evaluator",
        instructions="Say {{profile.nam",
        field="instructions",
        **{FIELD: True},
    )


# --------------------------------------------------------------------- snapshot embedding


def test_snapshot_embeds_an_evaluator_with_the_flag() -> None:
    """A ConfigSnapshot whose evaluator says ``True`` (empty list) validates, round-trips through
    JSON with the flag kept, and its digest verifies."""
    snapshot = entity_class3("ConfigSnapshot")
    parts = snapshot_parts(evaluator_over={"expected_tool_calls": [], FIELD: True})
    doc = snapshot_doc(parts)
    assert doc["evaluator"][FIELD] is True
    loaded = snapshot.model_validate(doc)
    assert getattr(loaded.evaluator, FIELD) is True
    again = snapshot.model_validate_json(loaded.model_dump_json(by_alias=True))
    assert getattr(again.evaluator, FIELD) is True
    assert again.content_digest == loaded.content_digest


def test_snapshot_digest_pins_the_flag_of_the_embedded_evaluator() -> None:
    """An embedded evaluator replaced by another valid evaluator that differs only in the flag is
    a different record (its own digest differs), so the snapshot's digest no longer matches:
    ``DigestMismatch``. The snapshot of the original loads."""
    errors = api("errors")
    snapshot = entity_class3("ConfigSnapshot")
    parts = snapshot_parts(evaluator_over={"expected_tool_calls": [], FIELD: None})
    doc = snapshot_doc(parts)
    snapshot.model_validate(doc)
    original = parts["evaluator"]
    other = make(
        "Evaluator",
        id=str(original.id),
        persona={"id": str(parts["persona"].id), "version": 1},
        metric_bindings=[{"id": str(b.id), "version": 1} for b in parts["bindings"]],
        instructions=original.instructions,
        expected_outcome=original.expected_outcome,
        name=original.name,
        expected_tool_calls=[],
        **{FIELD: True},
    )
    assert other.content_digest != original.content_digest
    with pytest.raises(errors.DigestMismatch):
        snapshot.model_validate({**doc, "evaluator": other.model_dump(mode="json", by_alias=True)})
    forged = copy.deepcopy(doc)
    forged["evaluator"][FIELD] = True
    with pytest.raises((errors.DigestMismatch, ValidationError)):
        snapshot.model_validate(forged)


def test_reference_digest_verification_still_works_with_the_flag() -> None:
    """The digest of a ``test_profile`` reference stays outside the evaluator's digest domain
    (N20): the same evaluator content with a plain digest, another plain digest or none on its
    profile reference has one evaluator digest, with the flag set too; and the snapshot digest
    check still applies to an evaluator that carries the flag."""
    model = entity_class("Evaluator")
    profile = build("TestProfile")
    base = raw_document(
        "Evaluator",
        expected_tool_calls=[],
        instructions="Greet {{profile.name}} and ask for a refund.",
        **{FIELD: True},
    )
    reference = {"id": profile["id"], "version": profile["version"]}
    with_digest = seal(
        model, {**base, "test_profile": {**reference, "content_digest": profile["content_digest"]}}
    )
    without_digest = seal(model, {**base, "test_profile": reference})
    assert with_digest["content_digest"] == without_digest["content_digest"]
    assert (
        model.model_validate(with_digest).content_digest
        == model.model_validate(without_digest).content_digest
    )
    parts = snapshot_parts(
        with_profile=True, evaluator_over={"expected_tool_calls": [], FIELD: True}
    )
    assert getattr(parts["evaluator"], FIELD) is True
    entity_class3("ConfigSnapshot").model_validate(snapshot_doc(parts))


# ===================================================================== the F5 mapping
# docs/phase1/api-surface-mappings-2.md section 3.3 and 3.4. Pipecat's own text (pipecat-ai
# 1.13.0, evals/simulation.py, module documentation of ``metrics``): "``function_calls`` takes a
# ``calls:`` list ... Every listed call must have happened and any call not listed fails it, so
# ``calls: []`` says the bot must call nothing, the check for a caller who should be turned down."
# The driver agrees (``simulation_driver._calls_outcome``: passed only when nothing is missing and
# nothing is unlisted). So a ``function_calls`` metric is exhaustive whatever its list.

SOURCE_CALLS = "/document/scenarios/0/metrics/0/calls"


def _sim(*metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "f",
        "judge": {"eval": {"service": "ollama", "model": "m1"}},
        "scenarios": [
            {
                "name": "s",
                "persona": "Pat, a caller.",
                "goal": "Ask one thing.",
                "success": "the bot answered",
                "metrics": list(metrics),
            }
        ],
    }


def _evaluators(result: Any) -> list[Any]:
    return [r for r in result.records if type(r).__name__ == "Evaluator"]


def _flag_entry(result: Any, evaluator: Any) -> Any:
    entries = [e for e in result.trace if e.record == str(evaluator.id) and e.field == "/" + FIELD]
    assert len(entries) == 1, [(e.record, e.field) for e in result.trace if FIELD in e.field]
    return entries[0]


@pytest.mark.parametrize(
    "calls",
    [[], ["book_table"], ["a", {"name": "b", "args": {"k": "v"}}]],
    ids=["empty", "one", "two"],
)
def test_f5_function_calls_metric_maps_to_an_exhaustive_expectation(calls: list[Any]) -> None:
    """Pipecat's ``function_calls`` fails on any call that is not listed, so every such metric,
    with an empty list or a listed one, gives ``expected_tool_calls_exhaustive = True``. The list
    keeps the listed calls (an empty list stays empty: no call is made up). The trace entry of the
    flag points at the ``calls`` key (not at the metric or the scenario), carries the derivation
    ``documented_measure_table`` and a complete citation of Pipecat's text (url, retrieval date,
    and the statement that a call not listed fails the check)."""
    result = run_f5(
        _sim({"name": "none", "measure": "function_calls", "calls": calls}), declarations()
    )
    (evaluator,) = _evaluators(result)
    assert getattr(evaluator, FIELD) is True
    assert len(evaluator.expected_tool_calls) == len(calls)
    entry = _flag_entry(result, evaluator)
    assert entry.sources == (SOURCE_CALLS,)
    assert entry.derivation == "documented_measure_table"
    assert entry.citation is not None
    assert entry.citation.url is not None
    assert entry.citation.url.startswith("https://")
    assert entry.citation.url.endswith("simulation.py")
    assert entry.citation.retrieved_at == RETRIEVED
    statement = entry.citation.statement.lower()
    assert "not listed" in statement
    assert "1.13.0" in statement
    assert [w for w in result.warnings if w.code == "empty_call_list"] == []
    assert [g for g in result.gaps if g.classification == "blocking"] == []


def test_f5_scenarios_without_a_function_calls_metric_do_not_state_the_flag() -> None:
    """A simulated scenario with no ``function_calls`` metric and every scripted scenario say
    nothing about extra calls: the flag stays ``None`` (not stated), never ``False`` and never
    ``True``, and no trace entry exists for it."""
    plain = run_f5(_sim({"name": "t", "measure": "turns", "max_value": 3}), declarations())
    (evaluator,) = _evaluators(plain)
    assert getattr(evaluator, FIELD) is None
    assert not [e for e in plain.trace if e.field == "/" + FIELD]
    scripted = run_f5(
        load_scenario_yaml(EVALS / "scripted_en.yaml"),
        declarations(
            instructions="Say the lines of the script in order.",
            expected_outcome="The assistant answers every line of the script.",
            max_duration_ms=600_000,
        ),
    )
    assert _evaluators(scripted)
    for ev in _evaluators(scripted):
        assert getattr(ev, FIELD) is None, ev.name
    assert not [e for e in scripted.trace if e.field == "/" + FIELD]


def test_f5_fixture_flags_follow_the_function_calls_metrics() -> None:
    """In ``simulated_en.yaml`` only the scenario that has a ``function_calls`` metric
    (``book_table/available``) is exhaustive; the others stay ``None``."""
    result = run_f5(load_scenario_yaml(EVALS / "simulated_en.yaml"), declarations())
    flags = {e.name: getattr(e, FIELD) for e in _evaluators(result)}
    assert flags["book_table/available"] is True
    others = {name: flag for name, flag in flags.items() if name != "book_table/available"}
    assert others
    assert all(flag is None for flag in others.values()), others


def test_f5_mapped_evaluators_validate_with_the_flag() -> None:
    """The evaluator built for ``calls: []`` is a valid, sealed Evaluator: it validates from its
    own JSON dump, with the flag and an empty list, and the digest verifies."""
    result = run_f5(
        _sim({"name": "none", "measure": "function_calls", "calls": []}), declarations()
    )
    (evaluator,) = _evaluators(result)
    again = entity_class("Evaluator").model_validate(evaluator.model_dump(mode="json"))
    assert again == evaluator
    assert again.expected_tool_calls == []
    assert getattr(again, FIELD) is True
