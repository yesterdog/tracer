# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch D: the test doubles of the data migration do what the tests assume.

The fake 0.0.2 migration of ``test_evaldb_version_policy.py`` is only as good as its text-level
upgrade step, so the step is tested here by itself: it changes the ``schema_version`` of every
entity level outside user ``metadata`` and no other byte, in both directions, for every golden
document of the 26 entities; and the variants that the package must refuse are really bad.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evaldb_migration_support import (  # type: ignore[import-not-found,unused-ignore]
    DIGESTED_TABLES,
    SCHEMA_V1,
    SCHEMA_V2,
    fake_functions,
    rewrite_schema_version,
    table_of_entity,
)
from evaldb_support import all_golden_valid, load_entity  # type: ignore[import-not-found,unused-ignore]


def _levels(value: Any, *, inside: bool = False) -> list[str]:
    """Every ``schema_version`` value outside a ``metadata`` object (structural walk)."""
    found: list[str] = []
    if isinstance(value, list):
        for item in value:
            found += _levels(item, inside=inside)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == "schema_version" and not inside:
                found.append(item)
            else:
                found += _levels(item, inside=inside or key == "metadata")
    return found


def test_rewrite_schema_version_touches_the_version_members_and_nothing_else() -> None:
    text = (
        '{"id":"x","schema_version":"0.0.1","metadata":{"schema_version":"0.0.1",'
        '"k":"\\"schema_version\\":\\"0.0.1\\"","deep":[{"schema_version":"0.0.1"}]},'
        '"embedded":{"schema_version":"0.0.1","items":[{"schema_version":"0.0.1","n":1e300}],'
        '"metadata":{}},"free":"schema_version","float":1.0E+2,"u":"\\u0000\\ud83d\\ude00 \u00e9"}'
    )
    out = rewrite_schema_version(text, SCHEMA_V1, SCHEMA_V2)
    parsed = json.loads(out)
    assert parsed["schema_version"] == SCHEMA_V2
    assert parsed["embedded"]["schema_version"] == SCHEMA_V2
    assert parsed["embedded"]["items"][0]["schema_version"] == SCHEMA_V2
    assert parsed["metadata"]["schema_version"] == SCHEMA_V1  # user metadata is not ours
    assert parsed["metadata"]["deep"][0]["schema_version"] == SCHEMA_V1
    assert parsed["metadata"]["k"] == '"schema_version":"0.0.1"'
    assert parsed["free"] == "schema_version"
    # exactly three 5-character spans differ; every other byte is the original, escapes and float
    # spelling included
    assert len(out) == len(text)
    assert [i for i, (a, b) in enumerate(zip(text, out, strict=True)) if a != b].__len__() == 3
    assert rewrite_schema_version(out, SCHEMA_V2, SCHEMA_V1) == text
    # a value that is not the old version is left alone, and so is a key that merely looks alike
    other = '{"schema_version":"0.0.7","x_schema_version":"0.0.1"}'
    assert rewrite_schema_version(other, SCHEMA_V1, SCHEMA_V2) == other


@pytest.mark.parametrize(
    ("entity", "path"), all_golden_valid(), ids=lambda v: getattr(v, "stem", v)
)
def test_plain_fake_step_is_exact_in_both_directions_on_every_golden_document(
    entity: str, path: Path
) -> None:
    text = load_entity(entity, path.read_text("utf-8")).model_dump_json()
    table = table_of_entity_inverse()[entity]
    forward, backward, _validate = fake_functions("plain", "persona")
    moved = forward(table, text)
    assert _levels(json.loads(text))
    assert set(_levels(json.loads(text))) == {SCHEMA_V1}
    assert set(_levels(json.loads(moved))) == {SCHEMA_V2}
    assert backward(table, moved) == text  # byte for byte
    stripped = json.dumps(json.loads(moved), sort_keys=True).replace(SCHEMA_V2, SCHEMA_V1)
    original = json.dumps(json.loads(text), sort_keys=True)
    assert len(stripped) == len(original)


def table_of_entity_inverse() -> dict[str, str]:
    return {entity: table for table, entity in table_of_entity().items()}


def test_the_refused_variants_are_really_bad() -> None:
    text = load_entity("Persona", Path(_persona_path()).read_text("utf-8")).model_dump_json()
    forward, backward, _ = fake_functions("lossy", "persona")
    assert backward("persona", forward("persona", text)) != text  # no downgrade can restore it
    forward, backward, _ = fake_functions("skip_one", "persona")
    assert forward("persona", text) == text
    assert json.loads(forward("agent_target", text))["schema_version"] == SCHEMA_V2
    forward, backward, _ = fake_functions("digest_member", "persona")
    moved = forward("persona", text)
    assert json.loads(moved)["content_digest"] != json.loads(text)["content_digest"]
    assert backward("persona", moved) == text  # reversible, so only the digest rule can refuse it
    assert (
        json.loads(forward("rubric", text))["content_digest"] == json.loads(text)["content_digest"]
    )
    forward, backward, validate = fake_functions("digest_member_lax", "persona")
    validate("persona", forward("persona", text))  # accepts the changed digest: nothing to see
    forward, backward, _ = fake_functions("digest_content", "persona")
    moved = forward("persona", text)
    assert json.loads(moved)["created_at"] != json.loads(text)["created_at"]
    assert json.loads(moved)["content_digest"] == json.loads(text)["content_digest"]
    assert backward("persona", moved) == text
    assert "persona" in DIGESTED_TABLES


def _persona_path() -> str:
    from evaldb_support import GOLDEN  # type: ignore[import-not-found,unused-ignore]

    return str(GOLDEN / "Persona" / "valid" / "minimal.json")


def test_the_validate_double_accepts_the_plain_step_and_sees_a_changed_content() -> None:
    from tracer_evalschema.errors import DigestMismatch

    text = load_entity("Persona", Path(_persona_path()).read_text("utf-8")).model_dump_json()
    forward, _, validate = fake_functions("plain", "persona")
    validate("persona", forward("persona", text))  # the new document verifies
    forward, _, validate = fake_functions("digest_content", "persona")
    with pytest.raises(DigestMismatch):
        validate("persona", forward("persona", text))
