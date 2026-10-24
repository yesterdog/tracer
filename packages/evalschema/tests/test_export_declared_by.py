# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 1 gate finding M5 / N19: ``LanguageDeclaration.declared_by`` in exports.

``declared_by`` names the actor who declared the language of an imported transcript. It is a
``MachineKey`` without ``@`` and can still hold a phone number or a user handle (N19). Decision
(gate, auto-approved): in a ``shared_export`` and a ``benchmark`` ``ExportBundle`` it must be absent
(``None``), exactly like ``Run.caller_external_id`` (which an export never holds); an ``internal``
export, a bare ``RunBundle`` and a bare ``Transcript`` keep it.

Contract: ``docs/phase1/api-surface-gate-fixes.md`` section 2. Pinned readings:

* **P-N1** ``declared_by`` becomes optional, default ``None`` (absent and ``null`` are the same, as
  for every optional field); a present value is validated as before. ``language``, ``source`` and
  ``declared_at`` stay required. The three generated schemas that hold a transcript and the lock are
  regenerated: the member leaves ``required`` and may be ``null``.
* **P-N2** the rule is tagged ``[declared_by]`` (a ``ValueError``; in a model load a
  ``ValidationError`` whose text holds the tag), runs for ``shared_export`` and ``benchmark`` only,
  over the transcripts of every embedded run bundle.
* **P-N3** the message names the run bundle ID, the transcript ID and the transcript position as
  ``transcripts[<j>]`` (0-based, in ``RunBundle.transcripts``) and never the value of
  ``declared_by`` or any text. The first offender is named: run bundles in order, then transcripts.
* **P-N4** nothing else about language changes: ``detected_language``, ``detected_language_raw``,
  the turn tags and the rest of the declaration are accepted exactly as before in every purpose.

Fails today for the right reason: a declaration without ``declared_by`` is ``missing`` (the field is
required), and a shared export with one loads (``DID NOT RAISE``). Guards (internal exports, bare
records, the validation of a present value) pass today and must keep passing.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api, load_json
from ._support_b2 import new_id
from ._support_b7 import (
    assert_error_free_of,
    export_class,
    export_doc,
    protect_by_redaction,
    run_bundle_doc,
    shared_ready,
)
from ._support_b8 import SHIPPED_DIR
from ._support_b15 import text_of_error
from ._support_b17 import declaration_of, declared_export, set_declared_by

TAG = "[declared_by]"
PURPOSES = ("internal", "shared_export", "benchmark")
NON_INTERNAL = ("shared_export", "benchmark")
ACTOR = "reviewer-7"
OTHER_ACTOR = "ops-handle-42"


def _transcript_class() -> Any:
    return api("transcript").Transcript


def _declaration_class() -> Any:
    return api("transcript").LanguageDeclaration


def _load(doc: dict[str, Any]) -> Any:
    return export_class().model_validate(doc)


def _reject(doc: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as caught:
        _load(doc)
    return text_of_error(caught.value)


def _bare_declaration(**over: Any) -> dict[str, Any]:
    return {
        "language": "hi-IN",
        "source": "user_declared",
        "declared_by": ACTOR,
        "declared_at": "2026-10-23T08:00:00.000000Z",
        **over,
    }


# ===================================================================== the field (P-N1)


def test_declared_by_is_optional_and_defaults_to_none() -> None:
    field = _declaration_class().model_fields["declared_by"]
    assert field.is_required() is False
    assert field.default is None


@pytest.mark.parametrize("form", ["absent", "null"])
def test_a_declaration_without_declared_by_loads(form: str) -> None:
    doc = _bare_declaration()
    if form == "absent":
        del doc["declared_by"]
    else:
        doc["declared_by"] = None
    loaded = _declaration_class().model_validate(doc)
    assert loaded.declared_by is None
    assert loaded.language == "hi-IN"
    assert loaded.declared_at is not None


def test_the_other_three_members_stay_required() -> None:
    for missing in ("language", "source", "declared_at"):
        doc = _bare_declaration()
        del doc[missing]
        with pytest.raises(ValidationError):
            _declaration_class().model_validate(doc)


@pytest.mark.parametrize("bad", ["someone@example.com", "", " x", "x y", "+91 98765 43210", 7, []])
def test_a_present_declared_by_is_still_validated(bad: Any) -> None:
    """GUARD: only absence is new; a present value must still be a machine key without ``@``."""
    with pytest.raises(ValidationError):
        _declaration_class().model_validate(_bare_declaration(declared_by=bad))


def test_a_present_declared_by_round_trips() -> None:
    loaded = _declaration_class().model_validate(_bare_declaration())
    again = _declaration_class().model_validate_json(loaded.model_dump_json())
    assert again == loaded
    assert again.declared_by == ACTOR


def test_an_absent_declared_by_round_trips_and_dumps_null() -> None:
    doc = _bare_declaration()
    del doc["declared_by"]
    loaded = _declaration_class().model_validate(doc)
    dumped = json.loads(loaded.model_dump_json())
    assert dumped["declared_by"] is None
    assert _declaration_class().model_validate_json(loaded.model_dump_json()) == loaded
    assert "declared_by" not in loaded.model_dump(mode="json", exclude_none=True)


def test_the_json_schema_makes_the_member_optional_and_nullable() -> None:
    """The shipped ``Transcript`` schema (regenerated): ``declared_by`` is not required, accepts
    ``null`` and still has the machine-key pattern for a string."""
    schema = load_json(SHIPPED_DIR / "Transcript.schema.json")
    declaration = schema["$defs"]["LanguageDeclaration"]
    assert "declared_by" in declaration["properties"]
    assert "declared_by" not in declaration["required"]
    assert set(declaration["required"]) == {"language", "source", "declared_at"}
    text = json.dumps(declaration["properties"]["declared_by"])
    assert '"null"' in text
    assert "A-Za-z0-9" in text


@pytest.mark.parametrize("name", ["RunBundle", "ExportBundle"])
def test_the_bundle_schemas_agree(name: str) -> None:
    schema = load_json(SHIPPED_DIR / f"{name}.schema.json")
    declaration = schema["$defs"]["LanguageDeclaration"]
    assert "declared_by" not in declaration["required"]
    assert '"null"' in json.dumps(declaration["properties"]["declared_by"])


# ================================================ the rule by purpose (P-N2): the matrix


@pytest.mark.parametrize("purpose", PURPOSES)
def test_an_export_without_declared_by_loads_in_every_purpose(purpose: str) -> None:
    bundle = _load(declared_export(purpose, declared_by=None))
    declaration = bundle.run_bundles[0].transcripts[0].declared_language
    assert declaration is not None
    assert declaration.declared_by is None
    assert declaration.language == "hi-IN"


def test_an_internal_export_keeps_declared_by() -> None:
    """GUARD (passes today): the internal export is the producer's own record."""
    bundle = _load(declared_export("internal", declared_by=ACTOR))
    assert bundle.run_bundles[0].transcripts[0].declared_language.declared_by == ACTOR


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_a_shared_or_benchmark_export_rejects_declared_by(purpose: str) -> None:
    text = _reject(declared_export(purpose, declared_by=ACTOR))
    assert TAG in text


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_an_explicit_null_is_absent(purpose: str) -> None:
    doc = declared_export(purpose, declared_by=ACTOR)
    declaration_of(doc)["declared_by"] = None
    _load(doc)


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_the_message_names_the_position_and_the_ids_and_not_the_value(purpose: str) -> None:
    doc = declared_export(purpose, declared_by=ACTOR)
    rb = doc["run_bundles"][0]
    with pytest.raises(ValidationError) as caught:
        _load(doc)
    text = text_of_error(caught.value)
    assert TAG in text
    assert "transcripts[0]" in text
    assert rb["transcripts"][0]["id"] in text
    assert rb["id"] in text
    assert_error_free_of(caught.value, ACTOR)
    for turn in rb["turns"]:
        assert turn["text"] not in text


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_the_rule_is_a_value_error_of_the_function(purpose: str) -> None:
    """``check_export_bundle`` on a bundle that was not validated: an internal export (which loads
    with ``declared_by``) relabelled as ``purpose`` is judged by the function. Its payloads are all
    redacted, so the tag is the only thing wrong."""
    check = api("bundle").check_export_bundle
    loaded = _load(declared_export("internal", declared_by=ACTOR))
    relabelled = loaded.model_copy(update={"purpose": purpose})
    with pytest.raises(ValueError, match=r"\[declared_by\]") as caught:
        check(relabelled)
    assert ACTOR not in str(caught.value)
    clean = _load(declared_export("internal", declared_by=None)).model_copy(
        update={"purpose": purpose}
    )
    check(clean)


# ============================================================== position and embedding (P-N3)


def _with_second_transcript(rb: dict[str, Any], declared_by: str | None) -> dict[str, Any]:
    """A copy of ``rb`` with a second, declared transcript (a copy of the first, new ID) whose
    declaration is by ``declared_by``; every result names the first transcript."""
    out = copy.deepcopy(rb)
    first = out["transcripts"][0]
    second = copy.deepcopy(first)
    second["id"] = new_id("tsc")
    if declared_by is None:
        second["declared_language"].pop("declared_by", None)
    else:
        second["declared_language"]["declared_by"] = declared_by
    out["transcripts"].append(second)
    for result in out["results"]:
        result["transcript_ids"] = [first["id"]]
    return out


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_the_second_transcript_of_a_run_bundle_is_found(purpose: str) -> None:
    rb = run_bundle_doc(declared=True)
    del rb["transcripts"][0]["declared_language"]["declared_by"]
    rb = _with_second_transcript(rb, OTHER_ACTOR)
    doc = export_doc(purpose, [rb], payload_redactions=protect_by_redaction(rb))
    text = _reject(doc)
    assert TAG in text
    assert "transcripts[1]" in text
    assert "transcripts[0]" not in text
    assert rb["transcripts"][1]["id"] in text
    assert OTHER_ACTOR not in text


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_the_first_transcript_is_found_and_the_first_offender_is_named(purpose: str) -> None:
    """The offender is the first transcript (the second is clean), and both offend: in both cases
    the first position is named."""
    for second_by in (None, OTHER_ACTOR):
        rb = run_bundle_doc(declared=True)
        rb = _with_second_transcript(rb, second_by)
        doc = export_doc(purpose, [rb], payload_redactions=protect_by_redaction(rb))
        text = _reject(doc)
        assert TAG in text
        assert "transcripts[0]" in text
        assert "transcripts[1]" not in text
        assert rb["transcripts"][0]["id"] in text


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_the_clean_two_transcript_export_loads(purpose: str) -> None:
    """GUARD for the previous test (passes after the change, fails today for the missing member)."""
    rb = run_bundle_doc(declared=True)
    del rb["transcripts"][0]["declared_language"]["declared_by"]
    rb = _with_second_transcript(rb, None)
    _load(export_doc(purpose, [rb], payload_redactions=protect_by_redaction(rb)))


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_a_later_run_bundle_is_checked_too(purpose: str) -> None:
    clean = run_bundle_doc(declared=True)
    del clean["transcripts"][0]["declared_language"]["declared_by"]
    dirty = run_bundle_doc(declared=True)
    assert clean["id"] != dirty["id"]
    redactions = protect_by_redaction(clean) + protect_by_redaction(dirty)
    doc = export_doc(purpose, [clean, dirty], payload_redactions=redactions)
    text = _reject(doc)
    assert TAG in text
    assert dirty["id"] in text
    assert clean["id"] not in text


def test_an_internal_export_of_several_run_bundles_keeps_every_declared_by() -> None:
    first = run_bundle_doc(declared=True)
    second = run_bundle_doc(declared=True)
    bundle = _load(export_doc("internal", [first, second]))
    actors = [rb.transcripts[0].declared_language.declared_by for rb in bundle.run_bundles]
    assert actors == ["cli", "cli"]


# ================================================================ bare records keep it


def test_a_run_bundle_keeps_declared_by() -> None:
    """GUARD: only the export purposes change; the run bundle is a record of fact."""
    bundle = api("bundle").RunBundle.model_validate(run_bundle_doc(declared=True))
    assert bundle.transcripts[0].declared_language.declared_by == "cli"


def test_a_transcript_keeps_declared_by_and_may_omit_it() -> None:
    doc = run_bundle_doc(declared=True)["transcripts"][0]
    assert _transcript_class().model_validate(doc).declared_language.declared_by == "cli"
    del doc["declared_language"]["declared_by"]
    assert _transcript_class().model_validate(doc).declared_language.declared_by is None


# ============================================================ nothing else changes (P-N4)


@pytest.mark.parametrize("purpose", PURPOSES)
def test_a_tagged_transcript_is_unaffected_in_every_purpose(purpose: str) -> None:
    """GUARD: the default world's transcript carries ``detected_language`` and no declaration."""
    doc = shared_ready(purpose)
    transcript = doc["run_bundles"][0]["transcripts"][0]
    assert "declared_language" not in transcript
    assert transcript["detected_language"] == "hi-IN"
    bundle = _load(doc)
    assert bundle.run_bundles[0].transcripts[0].detected_language == "hi-IN"


@pytest.mark.parametrize("purpose", PURPOSES)
def test_the_provider_label_and_the_turn_tags_are_unaffected(purpose: str) -> None:
    """GUARD: ``detected_language_raw`` (the provider's own label) and the tags on the turns are
    not actor names and are accepted in every purpose."""
    doc = shared_ready(purpose)
    transcript = doc["run_bundles"][0]["transcripts"][0]
    transcript["detected_language_raw"] = "hi"
    for turn in doc["run_bundles"][0]["turns"]:
        turn["language"] = "hi-IN"
    _load(doc)


@pytest.mark.parametrize("purpose", NON_INTERNAL)
def test_the_rest_of_the_declaration_survives_the_export(purpose: str) -> None:
    bundle = _load(declared_export(purpose, declared_by=None))
    declaration = bundle.run_bundles[0].transcripts[0].declared_language
    assert (declaration.language, declaration.source) == ("hi-IN", "user_declared")
    assert declaration.declared_at.isoformat().startswith("2026-10-23T08:00:00")
    result = bundle.run_bundles[0].results[0]
    assert result.language_source == "declared"
    assert [str(i) for i in result.declared_language_transcript_ids] == [
        str(bundle.run_bundles[0].transcripts[0].id)
    ]


def test_set_declared_by_helper_is_symmetric() -> None:
    """Helper self-check: setting then removing leaves the document as it was."""
    doc = declared_export("shared_export", declared_by=ACTOR)
    before = copy.deepcopy(doc)
    set_declared_by(doc, None)
    assert "declared_by" not in declaration_of(doc)
    set_declared_by(doc, ACTOR)
    assert doc == before
