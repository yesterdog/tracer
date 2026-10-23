# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-13: Dataset and DatasetItem (ADR 0005 Decision 6, ADR 0009 Decision 5, ADR 0016 Decisions 4
and 5, ADR 0004 Decision 6g).

Assumed public API (docs/phase1/api-surface.md section 22), ``tracer_evalschema.dataset``:
``Dataset`` (``dts``, versioned: ``name``, ``item_ids``) and ``DatasetItem`` (``dti``, a record of
fact: ``language`` (a SupportedLanguage), optional ``artifact_id`` (a recording), ``transcript_id``
and ``source_run_id``, and ``privacy``, required when the item holds a recording or a transcript).

This module defines ``test_dataset_item_no_codemix`` (SCOPE section 5), whose binding name
contains a T-DRIFT token; it must be on the ADR 0005 Decision 10 allowlist by path.

Names are SCOPE section 5 names unless the docstring says "Proposed name".
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import (
    EXCLUDED_LANGUAGE_TAGS,
    MIXED_ALIAS_TAG,
    MIXED_FIELD_NAME,
    MIXED_FIELD_PATTERN,
    judge_definition,
    make,
    property_names,
    schema_accepts,
    schema_of,
)
from ._support_b3 import (
    assert_rejected3,
    doc3,
    entity_class3,
    language_declaration,
    make3,
    new_id,
    privacy_human,
    privacy_synthetic,
    result_doc,
)


def _item(**over: Any) -> Any:
    return make3("DatasetItem", **over)


def _reject(field: str | None = None, **over: Any) -> None:
    assert_rejected3(entity_class3("DatasetItem"), doc3("DatasetItem", **over), field=field)


def test_dataset_item_no_codemix() -> None:
    """SCOPE P1-13 acceptance, ADR 0005 I19: no mixed-language field exists anywhere in the Dataset
    and DatasetItem models or their JSON Schemas, a field of that name is rejected, and
    ``DatasetItem.language`` accepts a configured language only: the mixed-language alias, an
    excluded language, a romanized tag (native script only), ``und`` and a language name are
    rejected, as is a region outside IN, US and GB."""
    item_model = entity_class3("DatasetItem")
    dataset_model = entity_class3("Dataset")
    for model in (item_model, dataset_model):
        names = (
            set(model.model_fields) | property_names(model) | property_names(model, "serialization")
        )
        assert not [n for n in names if MIXED_FIELD_PATTERN.search(n)], model.__name__
    _reject(None, **{MIXED_FIELD_NAME: ["hi", "en"]})
    for good in ("hi-IN", "hi", "en-GB", "mr-IN", "ta", "pa-Guru-IN", "ml", "gu-IN", "te", "kn"):
        assert _item(language=good).language == good
    assert _item(language="HI-in").language == "hi-IN"
    for bad in (
        MIXED_ALIAS_TAG,
        EXCLUDED_LANGUAGE_TAGS[0],
        EXCLUDED_LANGUAGE_TAGS[0] + "-IN",
        "hi-Latn",
        "und",
        "mul",
        "hindi",
        "hi_IN",
        "ta-LK",
        "en-AU",
        "",
        None,
        3,
    ):
        _reject("language", language=bad)
    doc = doc3("DatasetItem")
    assert_rejected3(
        item_model, {k: v for k, v in doc.items() if k != "language"}, field="language"
    )
    assert "language" in schema_of(item_model)["required"]
    assert schema_of(item_model)["properties"]["language"]["pattern"]


def test_human_recording_item_requires_privacy_marker() -> None:
    """ADR 0016 Decision 4, I3, SCOPE P1-13: a DatasetItem that holds a recording
    (``artifact_id``) or a transcript (``transcript_id``) carries a PrivacyMarker: omitting it, or
    giving ``null``, is rejected; a marker for human-subject data carries its consent and
    retention (P-46). The IDs are typed (``art_``, ``tsc_``, ``run_``)."""
    model = entity_class3("DatasetItem")
    payloads = [
        {"artifact_id": new_id("art"), "transcript_id": None},
        {"artifact_id": None, "transcript_id": new_id("tsc")},
    ]
    for payload in payloads:
        with_marker = _item(**payload)
        assert with_marker.privacy.human_subject is True
        assert with_marker.privacy.consent.status == "obtained"
        base = doc3("DatasetItem", **payload)
        without = {k: v for k, v in base.items() if k != "privacy"}
        assert_rejected3(model, without, field="privacy")
        assert_rejected3(model, {**without, "privacy": None}, field="privacy")
        assert_rejected3(model, {**without, "privacy": {"human_subject": True}}, field="privacy")
    both = _item(artifact_id=new_id("art"), transcript_id=new_id("tsc"))
    assert both.artifact_id is not None
    assert both.transcript_id is not None
    for bad in (new_id("tsc"), new_id("run"), "art_x", new_id("art")[4:], 7):
        _reject("artifact_id", artifact_id=bad)
    for bad in (new_id("art"), new_id("turn"), "tsc_x", 7):
        _reject("transcript_id", transcript_id=bad, artifact_id=None)
    loaded = _item(source_run_id=new_id("run"))
    assert str(loaded.source_run_id).startswith("run_")
    for bad in (new_id("snap"), new_id("run")[4:], "run_x"):
        _reject("source_run_id", source_run_id=bad)
    assert model.model_validate_json(loaded.model_dump_json()) == loaded
    assert schema_accepts(model, json.loads(loaded.model_dump_json()))


def test_dataset_item_recording_without_run_is_human_subject() -> None:
    """Proposed name (ADR 0016 Decision 5): a DatasetItem with a recording and no source run has
    no role evidence, so its derivation is ``human_subject=true``: a stored false marker is
    looser than the derivation and is rejected, also for a transcript item. With a source run the
    derivation depends on that run's evidence, which is checked where the run is known
    (``privacy.check_human_subject_marker``), so the model accepts a false marker there."""
    model = entity_class3("DatasetItem")
    synthetic = privacy_synthetic()
    payloads = [
        {"artifact_id": new_id("art"), "transcript_id": None},
        {"artifact_id": None, "transcript_id": new_id("tsc")},
    ]
    for payload in payloads:
        assert_rejected3(model, doc3("DatasetItem", privacy=synthetic, **payload), field="privacy")
        strict = _item(privacy=privacy_human(human_subject_reason="manual"), **payload)
        assert strict.privacy.human_subject is True
        with_run = _item(privacy=synthetic, source_run_id=new_id("run"), **payload)
        assert with_run.privacy.human_subject is False
    run = make3("Run", mode="simulated", caller_origin="simulated")
    item = _item(
        privacy=synthetic,
        artifact_id=None,
        transcript_id=new_id("tsc"),
        source_run_id=str(run.id),
    )
    turns = [make3("Turn", transcript_id=str(item.transcript_id), role="unknown")]
    with pytest.raises(ValueError, match=r"looser|derivation|unknown"):
        api("privacy").check_human_subject_marker(item.privacy, run, turns)
    turns = [make3("Turn", transcript_id=str(item.transcript_id), role="agent")]
    api("privacy").check_human_subject_marker(item.privacy, run, turns)


def test_dataset_item_language_is_not_a_declaration() -> None:
    """N14 (ADR 0004 Decision 6g, I32): a dataset item's ``language`` is configured intent. It is
    never copied into ``Transcript.declared_language`` and never read as an observation: the item
    has no declaration field, an untagged transcript of the item stays ``language_not_observed``
    for a language-dependent metric, and an explicit declaration is a separate act of a user
    with its own provenance (``declared_by``, ``declared_at``)."""
    results = api("results")
    item_model = entity_class3("DatasetItem")
    assert not [name for name in item_model.model_fields if "declar" in name or "detected" in name]
    transcript = make3("Transcript", source="agent_export")
    item = _item(language="mr-IN", artifact_id=None, transcript_id=str(transcript.id))
    assert item.language == "mr-IN"
    assert transcript.declared_language is None
    assert transcript.detected_language is None
    turn = make3("Turn", transcript_id=str(transcript.id))
    judge = make("MetricDefinition", **judge_definition(languages=["mr", "hi", "en"]))
    outcome = results.check_language(judge, [turn], [transcript])
    assert outcome.na_reason == "language_not_observed"
    assert outcome.language_source is None
    assert list(outcome.declared_language_transcript_ids) == []
    # only an explicit declaration changes the outcome, and then the result says so
    declared = make3("Transcript", source="agent_export", declared_language=language_declaration())
    declared_turn = make3("Turn", transcript_id=str(declared.id))
    after = results.check_language(judge, [declared_turn], [declared])
    assert (after.na_reason, after.language_source) == (None, "declared")
    assert transcript.declared_language is None
    scored = entity_class3("MetricResult").model_validate(result_doc(judge, language_source=None))
    with pytest.raises(ValueError, match="language_source"):
        results.check_result_against_definition(scored, judge)


def test_dataset_is_a_versioned_entity() -> None:
    """Proposed name (ADR 0009 Decision 5, ADR 0001 Decision 4): Dataset is a versioned entity
    (``dts``, ``version >= 1``, a verified ``content_digest``) holding a ``name`` (a label) and the
    IDs of its items (``dti_``, unique, in order); a DatasetItem is a record of fact (``dti``)
    with no version. Changing the items without a new digest is rejected."""
    base = api("base")
    dataset = entity_class3("Dataset")
    item = entity_class3("DatasetItem")
    assert issubclass(dataset, base.VersionedEntity)
    assert not issubclass(item, base.VersionedEntity)
    assert api("registry").entity_classes()["dts"] is dataset
    assert api("registry").entity_classes()["dti"] is item
    loaded = make3("Dataset")
    assert loaded.version == 1
    assert [str(i)[:4] for i in loaded.item_ids] == ["dti_", "dti_"]
    assert set(dataset.model_fields) - {
        "id",
        "schema_version",
        "created_at",
        "metadata",
        "metadata_flags",
        "version",
        "content_digest",
    } == {"name", "item_ids"}
    first = new_id("dti")
    for bad in ([first, first], [new_id("art")], [str(first)[4:]], "dti_x", None):
        assert_rejected3(dataset, doc3("Dataset", item_ids=bad), field="item_ids")
    assert make3("Dataset", item_ids=[]).item_ids == []
    ordered = make3("Dataset", item_ids=[first, new_id("dti")])
    assert str(ordered.item_ids[0]) == first
    for bad_name in ("", " x", "x ", None, 3):
        assert_rejected3(dataset, doc3("Dataset", name=bad_name), field="name")
    with pytest.raises(api("errors").DigestMismatch):
        dataset.model_validate({**loaded.model_dump(mode="json"), "item_ids": [new_id("dti")]})
    with pytest.raises(ValidationError):
        item.model_validate({**doc3("DatasetItem"), "version": 1})
