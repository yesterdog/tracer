# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch 20 (qa), part 2: the consumer can demand an export purpose (``expected_purpose``).

Decision (owner, auto-approved on the adversarial findings): ``ExportBundle.purpose`` is written by
the producer, so a bundle can claim to be ``internal`` (the producer's own data, human-subject
payloads in the clear, the rules of shared exports off) while a consumer takes it for a shared one.
A keyword-only ``expected_purpose`` (default ``None``, which means "accept any purpose") on
``bundle.check_export_bundle`` and ``loader.load_bundle`` lets a consumer demand the purpose; a
bundle whose own label differs is refused.

Contract pinned here (the architect implements; SCHEMA_POLICY.md and api-surface section 25 say it):

- ``check_export_bundle(bundle, *, expected_purpose=None)`` and
  ``load_bundle(model, data, *, max_bytes=..., max_depth=..., expected_purpose=None)``: the new
  parameter is keyword-only with default ``None`` and is documented (``expected_purpose``, "any
  purpose" for ``None`` and that the label is self-declared).
- ``None``: every behaviour of today, unchanged (all three purposes accepted by the same rules).
- A value of ``internal``, ``shared_export`` or ``benchmark`` equal to the bundle's own purpose:
  the same rules and the same result as ``None``. Different: ``ValueError`` whose message starts
  with the tag ``[purpose]`` and names the expected and the found purpose (both are values of the
  closed set, never user data) and nothing of the bundle's content. The purpose check comes first:
  it is cheap and the other rules depend on the label the consumer does not trust.
- Any other value is a call error, raised before the bundle or the data is looked at, and it is
  not tagged ``[purpose]``: ``TypeError`` if it is not a ``str`` (also a ``bool``, ``bytes``, a
  list), ``ValueError`` if it is a ``str`` outside the three purposes (case and white space
  matter).
- ``load_bundle`` keeps its order: argument errors, then the size limit (a ``BundleSizeError``
  wins over a mismatch, which can only be known after parsing), then the strict parse and the
  model's own errors, then the purpose. A ``RunBundle`` has no purpose, so
  ``expected_purpose`` other than ``None`` with ``RunBundle`` is a ``TypeError``.
- The model itself (``ExportBundle.model_validate``) still accepts any of the three purposes; the
  demand belongs to the consumer's call.
"""

from __future__ import annotations

import copy
import inspect
import json
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b7 import (
    EXPORT_PURPOSES,
    export_class,
    export_doc,
    load_export,
    protect_by_redaction,
    run_bundle_doc,
    run_class,
    shared_ready,
)

PURPOSES = ("internal", "shared_export", "benchmark")
SENTINEL = "SENTINEL_purpose_5c2e"


def ready(purpose: str) -> dict[str, Any]:
    """An accepted export document of the purpose; its metadata holds a sentinel the errors must
    never show."""
    doc = shared_ready(purpose)
    doc["metadata"] = {"note": SENTINEL}
    return doc


def bundle_of(purpose: str) -> Any:
    return load_export(ready(purpose))


def check(bundle: Any, **kwargs: Any) -> Any:
    return api("bundle").check_export_bundle(bundle, **kwargs)


def encoded(purpose: str) -> bytes:
    return json.dumps(ready(purpose)).encode("utf-8")


# ----------------------------------------------------------------------------- the signatures


def test_purposes_constant_is_the_three_values() -> None:
    assert tuple(EXPORT_PURPOSES) == PURPOSES


@pytest.mark.parametrize(
    ("owner", "name"), [("bundle", "check_export_bundle"), ("loader", "load_bundle")]
)
def test_expected_purpose_is_keyword_only_with_default_none(owner: str, name: str) -> None:
    function = getattr(api(owner), name)
    parameter = inspect.signature(function).parameters["expected_purpose"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None


def test_expected_purpose_cannot_be_passed_by_position() -> None:
    bundle = bundle_of("internal")
    assert api("bundle").check_export_bundle(bundle, expected_purpose="internal") is None
    with pytest.raises(TypeError):
        api("bundle").check_export_bundle(bundle, "internal")
    data = encoded("internal")
    assert load(data, expected_purpose="internal") == load(data)
    with pytest.raises(TypeError):
        api("loader").load_bundle(export_class(), data, 10**9, 256, "internal")


@pytest.mark.parametrize(
    ("owner", "name"), [("bundle", "check_export_bundle"), ("loader", "load_bundle")]
)
def test_expected_purpose_is_documented(owner: str, name: str) -> None:
    """The docstring names the parameter, says that ``None`` accepts any purpose and that a
    bundle's purpose is the producer's own claim (self-declared)."""
    doc = inspect.getdoc(getattr(api(owner), name)) or ""
    assert "expected_purpose" in doc
    assert "None" in doc
    lowered = doc.lower()
    assert "any purpose" in lowered
    assert "self-declared" in lowered or "self declared" in lowered


# ----------------------------------------------------------------------- check_export_bundle


@pytest.mark.parametrize("purpose", PURPOSES)
def test_none_keeps_every_current_behaviour(purpose: str) -> None:
    bundle = bundle_of(purpose)
    assert check(bundle) is None
    assert check(bundle, expected_purpose=None) is None
    assert bundle.purpose == purpose


@pytest.mark.parametrize("purpose", PURPOSES)
def test_the_own_purpose_is_accepted(purpose: str) -> None:
    bundle = bundle_of(purpose)
    before = bundle.model_dump(mode="json", by_alias=True)
    assert check(bundle, expected_purpose=purpose) is None
    assert check(bundle, expected_purpose=purpose) is None  # idempotent
    assert bundle.model_dump(mode="json", by_alias=True) == before  # pure


MISMATCHES = [(own, wanted) for own in PURPOSES for wanted in PURPOSES if own != wanted]


@pytest.mark.parametrize(("own", "wanted"), MISMATCHES)
def test_a_different_purpose_is_refused_with_the_purpose_tag(own: str, wanted: str) -> None:
    bundle = bundle_of(own)
    before = bundle.model_dump(mode="json", by_alias=True)
    with pytest.raises(ValueError, match=r"(?s).") as caught:
        check(bundle, expected_purpose=wanted)
    error = caught.value
    assert type(error) is ValueError  # not a ValidationError: the bundle itself is valid
    text = str(error)
    assert text.startswith("[purpose]"), text
    assert own in text
    assert wanted in text
    assert SENTINEL not in text
    assert bundle.model_dump(mode="json", by_alias=True) == before


def test_a_forged_internal_label_is_refused_when_a_shared_export_is_demanded() -> None:
    """The finding: an ``internal`` bundle keeps human-subject payloads in the clear and skips the
    shared rules; a consumer that needs a shared export must not accept it by its own label. The
    bundle itself loads (the model accepts any purpose)."""
    human = run_bundle_doc(tier="T0")
    assert human["artifacts"][0]["privacy"]["human_subject"] is True
    forged = export_doc("internal", [human])  # no seal, no redaction: valid only as internal
    bundle = load_export(forged)
    assert check(bundle) is None
    for demanded in ("shared_export", "benchmark"):
        with pytest.raises(ValueError, match=r"^\[purpose\]"):
            check(bundle, expected_purpose=demanded)
    # the same content labelled for sharing is rejected by the shared rules, as before
    relabelled = copy.deepcopy(forged)
    relabelled["purpose"] = "shared_export"
    with pytest.raises(ValidationError) as unprotected:
        load_export(relabelled)
    assert "[unprotected]" in str(unprotected.value)


def test_a_shared_export_is_not_mislabelled_as_internal() -> None:
    """The reverse: a consumer that expects the producer's own data (internal) is not handed a
    shared export (redacted, keyed) by mistake."""
    bundle = bundle_of("shared_export")
    with pytest.raises(ValueError, match=r"^\[purpose\]"):
        check(bundle, expected_purpose="internal")


def test_the_purpose_check_comes_before_the_rules_of_the_label() -> None:
    """A bundle that breaks a rule of its own label and also differs from the demanded purpose
    reports the purpose first; the same bundle with the matching demand reports its rule. (The
    object is built by ``model_copy``, which does not validate, as a stand-in for a bundle that
    reached the check without the model's validators.)"""
    internal = bundle_of("internal")
    internal.run_bundles[0].artifacts[0].uri = "file:///var/recordings/call-0001.wav"
    broken = internal.model_copy(update={"purpose": "shared_export"})
    with pytest.raises(ValueError, match=r"^\[purpose\]"):
        check(broken, expected_purpose="internal")
    with pytest.raises(ValueError, match=r"(?s).") as caught:
        check(broken, expected_purpose="shared_export")
    assert not str(caught.value).startswith("[purpose]")
    with pytest.raises(ValueError, match=r"(?s).") as without:
        check(broken)
    assert str(without.value) == str(caught.value)


BAD_TYPES: list[Any] = [
    1,
    0,
    True,
    False,
    1.5,
    b"internal",
    bytearray(b"internal"),
    ["internal"],
    ("internal",),
    {"internal"},
    {"internal": 1},
    object(),
]
BAD_VALUES = [
    "",
    " ",
    "Internal",
    "INTERNAL",
    "SHARED_EXPORT",
    "internal ",
    " internal",
    "internal\n",
    "shared-export",
    "shared export",
    "shared",
    "export",
    "bench",
    "benchmarks",
    "any",
    "*",
    "none",
    "None",
    "null",
]


@pytest.mark.parametrize("purpose", PURPOSES)
@pytest.mark.parametrize(
    "bad", BAD_TYPES, ids=[type(b).__name__ + str(i) for i, b in enumerate(BAD_TYPES)]
)
def test_an_expected_purpose_of_another_type_is_a_type_error(purpose: str, bad: Any) -> None:
    bundle = bundle_of(purpose)
    assert check(bundle, expected_purpose=purpose) is None  # the keyword exists: the type is wrong
    with pytest.raises(TypeError) as caught:
        check(bundle, expected_purpose=bad)
    assert "[purpose]" not in str(caught.value)


@pytest.mark.parametrize("purpose", PURPOSES)
@pytest.mark.parametrize("bad", BAD_VALUES, ids=[repr(b) for b in BAD_VALUES])
def test_an_unknown_expected_purpose_is_a_value_error_not_a_mismatch(
    purpose: str, bad: str
) -> None:
    """Not tagged ``[purpose]``, so a caller can tell a wrong argument from a wrong bundle."""
    bundle = bundle_of(purpose)
    assert check(bundle, expected_purpose=purpose) is None  # the keyword exists: the value is wrong
    with pytest.raises(ValueError, match=r"(?s).") as caught:
        check(bundle, expected_purpose=bad)
    assert not isinstance(caught.value, (TypeError, ValidationError))
    assert "[purpose]" not in str(caught.value)


# ------------------------------------------------------------------------------ load_bundle


def load(data: Any, **kwargs: Any) -> Any:
    return api("loader").load_bundle(export_class(), data, **kwargs)


@pytest.mark.parametrize("purpose", PURPOSES)
@pytest.mark.parametrize("kind", [str, bytes])
def test_load_bundle_none_and_the_own_purpose_load_the_same_bundle(
    purpose: str, kind: type
) -> None:
    raw = encoded(purpose)
    data: Any = raw.decode("utf-8") if kind is str else raw
    default = load(data)
    assert default.purpose == purpose
    assert load(data, expected_purpose=None) == default
    assert load(data, expected_purpose=purpose) == default
    assert default == load_export(json.loads(raw))


@pytest.mark.parametrize(("own", "wanted"), MISMATCHES)
@pytest.mark.parametrize("kind", [str, bytes])
def test_load_bundle_refuses_a_different_purpose(own: str, wanted: str, kind: type) -> None:
    raw = encoded(own)
    data: Any = raw.decode("utf-8") if kind is str else raw
    with pytest.raises(ValueError, match=r"(?s).") as caught:
        load(data, expected_purpose=wanted)
    text = str(caught.value)
    assert text.startswith("[purpose]"), text
    assert own in text
    assert wanted in text
    assert SENTINEL not in text
    assert not isinstance(caught.value, api("errors").BundleSizeError)


def test_load_bundle_does_not_demand_a_purpose_by_default() -> None:
    """Today's call, with no ``expected_purpose``, loads each purpose (the demand is opt-in)."""
    for purpose in PURPOSES:
        assert load(encoded(purpose)).purpose == purpose


def test_load_bundle_keeps_its_other_errors_with_a_matching_demand() -> None:
    """The demand adds nothing for a bundle that fails anyway: the model's own errors come out as
    before (``ValidationError`` for a broken document, the typed errors of the strict reader,
    ``BundleSizeError`` for size)."""
    errors = api("errors")
    broken = ready("shared_export")
    broken["payload_redactions"] = []
    data = json.dumps(broken).encode("utf-8")
    with pytest.raises(ValidationError) as plain:
        load(data)
    with pytest.raises(ValidationError) as demanded:
        load(data, expected_purpose="shared_export")
    assert "[unprotected]" in str(plain.value)
    assert "[unprotected]" in str(demanded.value)
    text = json.dumps(ready("internal"))
    duplicated = text.replace(
        '"purpose": "internal"', '"purpose": "internal", "purpose": "internal"'
    )
    assert duplicated != text
    with pytest.raises(errors.DuplicateKeyError):
        load(duplicated, expected_purpose="internal")
    with pytest.raises(errors.DepthLimitExceeded):
        load(text, expected_purpose="internal", max_depth=2)
    with pytest.raises(errors.BundleSizeError):
        load(text, expected_purpose="internal", max_bytes=10)
    # a mismatch cannot be told before parsing, so the size limit comes first
    with pytest.raises(errors.BundleSizeError):
        load(text, expected_purpose="benchmark", max_bytes=10)
    # not JSON at all: the reader's error, not a purpose error
    with pytest.raises(ValueError, match=r"(?s).") as syntax:
        load("{", expected_purpose="internal")
    assert "[purpose]" not in str(syntax.value)


@pytest.mark.parametrize(
    "bad", BAD_TYPES, ids=[type(b).__name__ + str(i) for i, b in enumerate(BAD_TYPES)]
)
def test_load_bundle_expected_purpose_of_another_type_is_a_type_error_before_reading(
    bad: Any,
) -> None:
    assert load(encoded("internal"), expected_purpose="internal").purpose == "internal"
    with pytest.raises(TypeError):
        load(encoded("internal"), expected_purpose=bad)
    with pytest.raises(TypeError) as unread:
        load("{ not json", expected_purpose=bad)  # the argument is judged first
    assert not isinstance(unread.value, json.JSONDecodeError)


@pytest.mark.parametrize("bad", BAD_VALUES, ids=[repr(b) for b in BAD_VALUES])
def test_load_bundle_unknown_expected_purpose_is_a_value_error_before_reading(bad: str) -> None:
    assert load(encoded("internal"), expected_purpose="internal").purpose == "internal"
    with pytest.raises(ValueError, match=r"(?s).") as caught:
        load(encoded("internal"), expected_purpose=bad)
    assert "[purpose]" not in str(caught.value)
    assert not isinstance(caught.value, (TypeError, ValidationError))
    with pytest.raises(ValueError, match=r"(?s).") as unread:
        load("{ not json", expected_purpose=bad)
    assert not isinstance(unread.value, json.JSONDecodeError)
    assert "[purpose]" not in str(unread.value)
    with pytest.raises(ValueError, match=r"(?s).") as oversize:
        load(encoded("internal"), expected_purpose=bad, max_bytes=1)
    assert not isinstance(oversize.value, api("errors").BundleSizeError)


def test_load_bundle_run_bundle_has_no_purpose_to_demand() -> None:
    """A ``RunBundle`` carries no purpose: ``None`` is today's call; any demand is refused instead
    of being ignored (an ignored demand would let a caller believe it had checked something)."""
    loader = api("loader")
    data = json.dumps(run_bundle_doc(tier="T0")).encode("utf-8")
    plain = loader.load_bundle(run_class(), data)
    assert loader.load_bundle(run_class(), data, expected_purpose=None) == plain
    for purpose in PURPOSES:
        with pytest.raises(TypeError):
            loader.load_bundle(run_class(), data, expected_purpose=purpose)


def test_load_bundle_still_loads_only_bundles() -> None:
    assert load(encoded("internal"), expected_purpose="internal").purpose == "internal"
    with pytest.raises(TypeError):
        api("loader").load_bundle(dict, b"{}", expected_purpose="internal")
    with pytest.raises(TypeError):
        api("loader").load_bundle(export_class(), 12345, expected_purpose="internal")


def test_the_model_still_accepts_every_purpose_without_a_demand() -> None:
    """The model validator calls the check with no demand; the demand is the consumer's."""
    for purpose in PURPOSES:
        doc = ready(purpose)
        assert export_class().model_validate(doc).purpose == purpose
        assert export_class().model_validate_json(json.dumps(doc)).purpose == purpose


def test_the_helper_documents_are_not_vacuous() -> None:
    """The three documents differ from each other in the protection they carry, so a mismatch is
    between three real states: the internal one has no redaction; the others are redacted."""
    internal = export_doc("internal", [run_bundle_doc(tier="T0")])
    assert internal["payload_redactions"] == []
    run = run_bundle_doc(tier="T0")
    shared = export_doc("shared_export", [run], payload_redactions=protect_by_redaction(run))
    assert shared["payload_redactions"]
