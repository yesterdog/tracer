# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The configuration snapshot of a run and the pricing catalog reference (ADR 0006 Decisions 1,
2, 5, 6 and 9, ADR 0009 Decisions 4 and 5, ADR 0013 Decision 2; P1-12).

- ``ConfigSnapshot`` (``snap``): a digested record of fact (a required ``content_digest``, no
  ``version``) that embeds the evaluator, persona, metric definitions, bindings and rubric a run
  used, and references the pricing catalog. It holds no personal data: the test profile is
  referenced by the evaluator (``TestProfileRef``), never embedded. It hides its input in
  validation errors (O4) and no message inside it quotes a rejected value (N42).
- ``snapshot_digest_domain(doc)`` and ``compute_snapshot_digest(doc)``: the digest, defined on the
  JSON document alone (a verifier in another language needs no model).
- ``PricingCatalogRef{id, version, content_digest}`` and ``check_pricing_catalog_ref``.
- ``check_internal_snapshot(snapshot)``: an internal snapshot records the plain profile digest.
- ``lint_profile_values_in_free_text(snapshot, profile)``: where a literal profile value stands in
  the snapshot's free text (P-16), by path, never by value.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Annotated, Any, ClassVar

from pydantic import Field, model_validator

from tracer_evalschema import base as _base
from tracer_evalschema._hiding import HidingModel
from tracer_evalschema._validation import Problem, raise_problems
from tracer_evalschema.base import EntityBase, VersionedRef
from tracer_evalschema.digest import (
    PLAIN_SCHEME,
    ContentDigest,
    compute_digest,
    digest_domain,
)
from tracer_evalschema.errors import RefDigestMismatchError
from tracer_evalschema.evaluator import Evaluator
from tracer_evalschema.events import IngestWarning
from tracer_evalschema.ids import PricingCatalogId
from tracer_evalschema.metrics import (
    EqualsTrueRule,
    MetricBinding,
    MetricDefinition,
    PerConversationAggregation,
)
from tracer_evalschema.persona import Persona
from tracer_evalschema.profile import TestProfile, find_literal_profile_values
from tracer_evalschema.rubric import Rubric, validate_rubric

__all__ = [
    "EXPECTED_OUTCOME_VARIABLE",
    "ConfigSnapshot",
    "PricingCatalogRef",
    "check_internal_snapshot",
    "check_pricing_catalog_ref",
    "compute_snapshot_digest",
    "lint_profile_values_in_free_text",
    "snapshot_digest_domain",
]

EXPECTED_OUTCOME_VARIABLE = "expected_outcome"
"""The reserved prompt variable through which an evaluator's expected outcome reaches its check."""

_EMBEDDED_ONE = ("evaluator", "persona", "rubric")
_EMBEDDED_MANY = ("metric_definitions", "metric_bindings")


class PricingCatalogRef(VersionedRef):
    """``{id, version, content_digest}`` of a pricing catalog (ADR 0006 I16, N26): the digest is
    required, and ``check_pricing_catalog_ref`` verifies it against the catalog of the bundle."""

    id: PricingCatalogId
    content_digest: ContentDigest


# ----------------------------------------------------------------------------- the digest


def snapshot_digest_domain(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The digest domain of a ConfigSnapshot JSON document (ADR 0006 Decision 5).

    The generic domain (no top-level ``content_digest`` and ``schema_version``, no ``null``
    member at any depth) minus, at these paths only: the ``schema_version`` of ``evaluator``,
    ``persona``, ``rubric`` and of each ``metric_definitions[]`` and ``metric_bindings[]``
    entry, and ``evaluator.test_profile.content_digest`` (P-17). Embedded ``content_digest``
    values stay; a ``schema_version`` key inside user ``metadata`` stays. The input is not
    changed.
    """
    body: dict[str, Any] = copy.deepcopy(dict(doc))
    for key in _EMBEDDED_ONE:
        embedded = body.get(key)
        if isinstance(embedded, dict):
            embedded.pop("schema_version", None)
    for key in _EMBEDDED_MANY:
        entries = body.get(key)
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    entry.pop("schema_version", None)
    evaluator = body.get("evaluator")
    if isinstance(evaluator, dict) and isinstance(evaluator.get("test_profile"), dict):
        evaluator["test_profile"].pop("content_digest", None)
    return digest_domain(body)


def compute_snapshot_digest(doc: Mapping[str, Any]) -> ContentDigest:
    """The plain ``sha256-jcs/v1`` digest of ``snapshot_digest_domain(doc)``."""
    return compute_digest(snapshot_digest_domain(doc))


# ------------------------------------------------------------------------------- the entity


def _check_reference_digest(
    reference: ContentDigest | None, actual: ContentDigest, what: str
) -> None:
    """E5: a reference may omit its digest; a present one is verified against the embedded
    record. Equal digests match; two digests of one scheme and ``key_ref`` that differ raise
    ``RefDigestMismatchError``; any other pair cannot be compared here and is never accepted."""
    if reference is None or reference == actual:
        return
    if reference.scheme == actual.scheme and reference.key_ref == actual.key_ref:
        raise RefDigestMismatchError(
            f"the reference to {what} carries a content_digest that does not match the embedded "
            "record"
        )
    raise ValueError(f"the content_digest on the reference to {what} cannot be verified here")


def _is_expected_outcome_check(definition: MetricDefinition) -> bool:
    """ADR 0013 Decision 2, api-surface 19.4 item 7: an ``llm_judge``, boolean,
    ``per_conversation``, ``equals_true`` definition that lists ``expected_outcome`` in its prompt
    variables."""
    return (
        definition.kind == "llm_judge"
        and definition.value_type == "boolean"
        and isinstance(definition.aggregation, PerConversationAggregation)
        and isinstance(definition.pass_rule, EqualsTrueRule)
        and EXPECTED_OUTCOME_VARIABLE in (definition.prompt_variables or [])
    )


class ConfigSnapshot(HidingModel, EntityBase):
    """The configuration a run used, frozen (prefix ``snap``; ADR 0006 Decision 1).

    Checked on load, after the fields: the digest equals ``compute_snapshot_digest`` of the
    document (``DigestMismatch``, ``DigestKeyUnavailable`` and ``UnknownDigestScheme`` unwrapped);
    the evaluator's persona and binding references, and each binding's definition reference,
    resolve to an embedded record of that ``(id, version)``, and a digest on such a reference is
    verified (``RefDigestMismatchError``, E5); every binding belongs to the evaluator; the rubric
    passes ``validate_rubric``; the evaluator has at least one expected-outcome check, and every
    such check is a required rubric item.
    """

    id_prefix: ClassVar[str] = "snap"

    content_digest: ContentDigest
    evaluator: Evaluator
    persona: Persona
    metric_definitions: Annotated[list[MetricDefinition], Field(min_length=1)]
    metric_bindings: Annotated[list[MetricBinding], Field(min_length=1)]
    rubric: Rubric
    pricing_catalog: PricingCatalogRef | None = None

    @model_validator(mode="after")
    def _verify(self) -> ConfigSnapshot:
        # The entry point shared with VersionedEntity (base.verify_entity_digest).
        _base.verify_entity_digest(
            snapshot_digest_domain(self.model_dump(mode="json", by_alias=True)),
            self.content_digest,
        )
        self._check_references()
        try:
            validate_rubric(
                self.rubric, bindings=self.metric_bindings, definitions=self.metric_definitions
            )
        except ValueError as exc:
            raise_problems(type(self).__name__, [Problem(("rubric",), "rubric", str(exc))])
        self._check_expected_outcome()
        return self

    def _check_references(self) -> None:
        evaluator = self.evaluator
        persona_ref = evaluator.persona
        if (persona_ref.id, persona_ref.version) != (self.persona.id, self.persona.version):
            raise_problems(
                type(self).__name__,
                [
                    Problem(
                        ("evaluator", "persona"),
                        "snapshot_reference",
                        "the evaluator's persona reference does not resolve to the embedded "
                        "persona",
                    )
                ],
            )
        _check_reference_digest(persona_ref.content_digest, self.persona.content_digest, "persona")
        bindings = {(b.id, b.version): b for b in self.metric_bindings}
        for position, reference in enumerate(evaluator.metric_bindings):
            binding = bindings.get((reference.id, reference.version))
            if binding is None:
                raise_problems(
                    type(self).__name__,
                    [
                        Problem(
                            ("evaluator", "metric_bindings", position),
                            "snapshot_reference",
                            f"binding {reference.id} version {reference.version} of the evaluator "
                            "is not embedded in the snapshot",
                        )
                    ],
                )
                return
            _check_reference_digest(
                reference.content_digest, binding.content_digest, f"binding {reference.id}"
            )
        definitions = {(d.id, d.version): d for d in self.metric_definitions}
        for position, binding in enumerate(self.metric_bindings):
            if binding.evaluator_id != evaluator.id:
                raise_problems(
                    type(self).__name__,
                    [
                        Problem(
                            ("metric_bindings", position, "evaluator_id"),
                            "snapshot_reference",
                            f"binding {binding.id} belongs to another evaluator",
                        )
                    ],
                )
            metric = binding.metric
            definition = definitions.get((metric.id, metric.version))
            if definition is None:
                raise_problems(
                    type(self).__name__,
                    [
                        Problem(
                            ("metric_bindings", position, "metric"),
                            "snapshot_reference",
                            f"definition {metric.id} version {metric.version} of binding "
                            f"{binding.id} is not embedded in the snapshot",
                        )
                    ],
                )
                return
            _check_reference_digest(
                metric.content_digest, definition.content_digest, f"definition {metric.id}"
            )

    def _check_expected_outcome(self) -> None:
        definitions = {(d.id, d.version): d for d in self.metric_definitions}
        referenced = {(r.id, r.version) for r in self.evaluator.metric_bindings}
        required: dict[Any, bool] = {item.binding.id: item.required for item in self.rubric.items}
        checks = [
            binding
            for binding in self.metric_bindings
            if (binding.id, binding.version) in referenced
            and binding.enabled
            and _is_expected_outcome_check(definitions[(binding.metric.id, binding.metric.version)])
        ]
        problem = None
        if not checks:
            problem = (
                "the evaluator needs an expected-outcome check: an enabled binding of an "
                "llm_judge, boolean, per_conversation, equals_true definition that uses the prompt "
                "variable "
                f"{EXPECTED_OUTCOME_VARIABLE}"
            )
        elif not all(required.get(binding.id) is True for binding in checks):
            problem = "every expected-outcome check is a required rubric item"
        if problem is not None:
            raise_problems(type(self).__name__, [Problem(("rubric",), "expected_outcome", problem)])


# ------------------------------------------------------------------------- checks and lint


def check_internal_snapshot(snapshot: ConfigSnapshot) -> None:
    """An internal snapshot records the plain (``sha256-jcs/v1``) digest of its test profile
    (ADR 0006 Decision 6): ``ValueError`` for an absent or keyed digest. A snapshot without a test
    profile has nothing to record."""
    reference = snapshot.evaluator.test_profile
    if reference is None:
        return
    digest = reference.content_digest
    if digest is None or digest.scheme != PLAIN_SCHEME:
        raise ValueError(
            f"an internal snapshot records the plain ({PLAIN_SCHEME}) digest of test profile "
            f"{reference.id}; it is absent or keyed here"
        )


def check_pricing_catalog_ref(ref: PricingCatalogRef, catalog_doc: Mapping[str, Any]) -> None:
    """Verify a pricing catalog reference against the catalog's JSON document (``id``,
    ``version``, ``content_digest``). ``ValueError`` for another ID or version, or a digest that
    cannot be compared (another scheme, or keyed with another ``key_ref``); two keyed digests with
    the same ``key_ref`` are compared without the key (ADR 0009 Decision 7).
    ``RefDigestMismatchError`` when comparable digests differ."""
    if str(catalog_doc.get("id")) != str(ref.id) or catalog_doc.get("version") != ref.version:
        raise ValueError(
            f"the pricing catalog reference names {ref.id} version {ref.version}, not the catalog "
            f"{catalog_doc.get('id')} version {catalog_doc.get('version')}"
        )
    actual = ContentDigest.model_validate(catalog_doc.get("content_digest"))
    reference = ref.content_digest
    if reference.scheme != actual.scheme or reference.key_ref != actual.key_ref:
        raise ValueError(
            f"the digest on the reference to pricing catalog {ref.id} cannot be compared with the "
            "catalog's (another scheme or another key)"
        )
    if reference.value != actual.value:
        raise RefDigestMismatchError(
            f"the reference to pricing catalog {ref.id} version {ref.version} carries a digest "
            "that does not match the catalog"
        )


def lint_profile_values_in_free_text(
    snapshot: ConfigSnapshot, profile: TestProfile
) -> list[IngestWarning]:
    """One ``IngestWarning`` (code ``profile_value_in_free_text``) per place where a literal value
    of ``profile`` stands in the snapshot (``profile.find_literal_profile_values``): ``path`` is the
    RFC 6901 pointer from the snapshot document root, there is no ``raw``, and the message names
    the profile field, never the value. Sorted like the hits. The lint finds only values of the
    profile it is given (ADR 0006 Decision 9)."""
    return [
        IngestWarning(
            code="profile_value_in_free_text",
            path=hit.path,
            message=(
                f"free text holds a literal value of the test profile field {hit.field}; write "
                f"the placeholder {{{{profile.{hit.field}}}}} instead"
            ),
        )
        for hit in find_literal_profile_values(snapshot, profile)
    ]
