# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Datasets and their items (ADR 0005 Decision 6, ADR 0009 Decision 5, ADR 0016 Decisions 4, 5
and 9; P1-13).

- ``Dataset`` (``dts``, versioned): a ``name`` and the ordered, unique ``item_ids`` of its items.
  Membership is inside the version's digest, so adding or removing an item is a new version (N25).
- ``DatasetItem`` (``dti``, a record of fact): its fields and privacy rules are stated in its
  class docstring, which is also its JSON Schema ``description`` (finding A29).
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar

from pydantic import AfterValidator, ConfigDict, Field, model_validator

from tracer_evalschema._hiding import HidingModel, values_hidden
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.base import EntityBase, VersionedEntity
from tracer_evalschema.ids import ArtifactId, DatasetItemId, RunId, TranscriptId
from tracer_evalschema.language import SupportedLanguage
from tracer_evalschema.privacy import PrivacyMarker
from tracer_evalschema.text import HumanLabel

__all__ = ["Dataset", "DatasetItem"]


def _unique_items(ids: list[DatasetItemId]) -> list[DatasetItemId]:
    seen: set[DatasetItemId] = set()
    for position, item in enumerate(ids):
        if item in seen:
            if values_hidden():
                raise ValueError(f"item_ids lists one item twice (again at position {position})")
            raise ValueError(f"item_ids lists {item} twice")
        seen.add(item)
    return ids


class Dataset(HidingModel, VersionedEntity):
    """A versioned set of dataset items (prefix ``dts``; ADR 0009 Decision 5, N25)."""

    id_prefix: ClassVar[str] = "dts"

    name: HumanLabel
    item_ids: Annotated[
        list[DatasetItemId],
        AfterValidator(_unique_items),
        Field(json_schema_extra={"uniqueItems": True}),
    ]


def _item_privacy_conditions(schema: dict[str, Any]) -> None:
    """The privacy rules of a dataset item as ``if``/``then`` keyed on the presence of a payload
    (a non-null ``artifact_id`` or ``transcript_id``): the item carries a privacy marker, and
    without a ``source_run_id`` the marker is human-subject (ADR 0016 Decision 5)."""
    rules: list[dict[str, Any]] = []
    for payload in ("artifact_id", "transcript_id"):
        rules.append(
            {
                "if": {"properties": {payload: {"type": "string"}}, "required": [payload]},
                "then": {"required": ["privacy"], "properties": {"privacy": {"type": "object"}}},
            }
        )
        rules.append(
            {
                "if": {
                    "properties": {payload: {"type": "string"}, "source_run_id": {"type": "null"}},
                    "required": [payload],
                },
                "then": {
                    "properties": {"privacy": {"properties": {"human_subject": {"enum": [True]}}}}
                },
            }
        )
    schema["allOf"] = rules


class DatasetItem(EntityBase):
    """One item of a dataset (prefix ``dti``; a record of fact). It names no dataset: a Dataset
    version lists its items (N25).

    ``language`` is the configured language of the item (intent, never an observation or a
    declaration). ``artifact_id`` (a recording) and ``transcript_id`` are optional.
    ``source_run_id`` is provenance only, never run membership (O7). A ``privacy`` marker is
    required when the item holds a recording or a transcript; with such a payload and no source
    run there is no role evidence, so the item is treated as human-subject and a
    ``human_subject=false`` marker is rejected (ADR 0016 Decision 5). No mixed-language field
    exists (ADR 0005).
    """

    model_config = ConfigDict(json_schema_extra=_item_privacy_conditions)

    id_prefix: ClassVar[str] = "dti"

    language: SupportedLanguage
    artifact_id: ArtifactId | None = None
    transcript_id: TranscriptId | None = None
    source_run_id: RunId | None = None
    privacy: PrivacyMarker | None = None

    @model_validator(mode="after")
    def _privacy_rules(self) -> DatasetItem:
        payload = self.artifact_id is not None or self.transcript_id is not None
        problem = None
        if payload and self.privacy is None:
            problem = "an item that holds a recording or a transcript carries a privacy marker"
        elif (
            payload
            and self.source_run_id is None
            and self.privacy is not None
            and not self.privacy.human_subject
        ):
            problem = (
                "without a source run there is no role evidence, so the payload derives "
                "human_subject=true; a false marker is looser than the derivation (ADR 0016 "
                "Decision 5)"
            )
        if problem is not None:
            raise_problems(type(self).__name__, [Problem(("privacy",), "privacy", problem)])
        return self
