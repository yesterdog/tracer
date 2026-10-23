# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-15, part 1: ``export_json_schema()`` and the content of the generated JSON Schema files.

Contract: docs/phase1/api-surface-schema-export.md. ADRs 0002, 0003 (Decision 6), 0007
(Decision 3), 0008 (Decisions 7 to 10), 0009. Tests of ``schema.lock``, the drift check, the CLI
and packaging are in ``test_schema_lock.py``; the agreement of the models with the schemas is in
``test_schema_agreement.py``; the register of rules JSON Schema cannot state is in
``test_schema_gaps.py``.

Pinned readings (P) that the text leaves open are listed in section 2 of the contract; each is
named in the docstring of the test that pins it. The implementation module is
``tracer_evalschema.schema_export``. The entity list comes from the registry walk, never from a
hard-coded count; the bundle schemas (``RunBundle``, ``ExportBundle``) are asserted by name and
stay red until P1-14 lands.
"""

from __future__ import annotations

import ast
import functools
import json
import os
import platform
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from ._support import CURRENT_SCHEMA_VERSION, PACKAGE_DIR, ROOT, api
from ._support_b2 import collect_enums, jsonschema_module, secret_like
from ._support_b3 import (
    artifact_doc,
    channel_map_doc,
    inferred_map_doc,
    privacy_human,
    privacy_synthetic,
    span_doc,
)
from ._support_b8 import (
    A22_NO_UNIQUE_ITEMS,
    DIALECT,
    EXTENSION_KEYWORDS,
    FILE_NAME_FORM,
    KNOWN_KEYWORDS,
    LOCK_NAME,
    REPR_LEAKS,
    SCHEMA_SUFFIX,
    UNIQUE_ITEMS_PROPERTIES,
    URN_FORM,
    all_strings,
    canonical_dumps,
    definition_node,
    definition_validator,
    ecma_validator_class,
    entity_class_by_name,
    entity_class_names,
    export_module,
    find_definition,
    if_then_count,
    keywords_used,
    model_accepts,
    pattern_values,
    property_names_anywhere,
    read_directory,
    refs_of,
    rendered_files,
    resolve,
    run_python,
    sample_document,
    schema_nodes,
    schema_validator,
)

# ------------------------------------------------------------------------------------ helpers


@functools.cache
def parsed() -> dict[str, dict[str, Any]]:
    """Entity class name -> parsed schema, from the files ``render_schemas()`` returns."""
    out: dict[str, dict[str, Any]] = {}
    for name, data in rendered_files().items():
        match = FILE_NAME_FORM.match(name)
        assert match is not None, name
        out[match.group("entity")] = json.loads(data)
    return out


def _entities_and_schemas() -> list[tuple[str, dict[str, Any]]]:
    return sorted(parsed().items())


def _sample_id(prefix: str) -> str:
    return str(api("ids").id_type(prefix).new())


# ------------------------------------------------------------------- the module and its files


def test_export_module_surface() -> None:
    """P: the generator is ``tracer_evalschema.schema_export``. It has ``render_schemas()``
    (file name -> bytes, pure, no I/O), ``render_lock(files)``, ``export_json_schema(directory)``
    (SCOPE name, writes), ``check_schema_directory(directory)``, ``schema_directory()`` (the
    shipped package-data directory), ``schema_id(entity)`` and ``main(argv)``, and the constants
    ``SCHEMA_SUFFIX`` (``.schema.json``), ``LOCK_NAME`` (``schema.lock``) and ``SCHEMA_DIALECT``."""
    module = export_module()
    for name in (
        "render_schemas",
        "render_lock",
        "export_json_schema",
        "check_schema_directory",
        "schema_directory",
        "schema_id",
        "main",
    ):
        assert callable(getattr(module, name)), name
    assert module.SCHEMA_SUFFIX == SCHEMA_SUFFIX == ".schema.json"
    assert module.LOCK_NAME == LOCK_NAME == "schema.lock"
    assert module.SCHEMA_DIALECT == DIALECT
    assert module.schema_id("Run") == f"urn:tracer:evalschema:{CURRENT_SCHEMA_VERSION}:Run"


def test_one_file_per_entity_and_no_other_file() -> None:
    """ADR 0008 Decision 7, SCOPE P1-15: one file per registered entity and nothing else (the
    lock is a separate output). The set comes from the registry walk, so a new entity is exported
    without a test edit and an entity without a file fails here. P: value objects get no file of
    their own; they travel in ``$defs`` of the entities that embed them."""
    files = rendered_files()
    expected = {name + SCHEMA_SUFFIX for name in entity_class_names()}
    assert set(files) == expected
    assert LOCK_NAME not in files
    value_objects = set(api("registry").value_object_classes())
    assert not {n.removesuffix(SCHEMA_SUFFIX) for n in files} & value_objects


def test_bundle_schemas_are_exported() -> None:
    """SCOPE P1-15 ("one file per entity plus bundle"): ``RunBundle`` and ``ExportBundle`` are
    registered entities (prefixes ``rbnd`` and ``exp``), so each has a file. Red until P1-14."""
    files = rendered_files()
    assert "RunBundle" + SCHEMA_SUFFIX in files
    assert "ExportBundle" + SCHEMA_SUFFIX in files


def test_file_names_are_class_names_and_safe_on_every_file_system() -> None:
    """P (contract 2.2): ``<ClassName>.schema.json``. The names are ASCII, start with a capital,
    are unique even when compared without case (macOS and Windows file systems), and are listed
    in sorted order."""
    names = list(rendered_files())
    assert names == sorted(names)
    for name in names:
        assert FILE_NAME_FORM.fullmatch(name), name
    assert len({n.lower() for n in names}) == len(names)


def test_render_schemas_reads_the_registry_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """P: the file set is derived from ``registry.entity_classes()`` on every call (no import-time
    cache), and the output does not depend on the order the registry lists the entities in
    (stable ordering). The registry is replaced by a reversed copy and the output compared."""
    module = export_module()
    registry = api("registry")
    original = registry.entity_classes
    calls: list[int] = []

    def reversed_registry() -> dict[str, Any]:
        calls.append(1)
        entities = original()
        return dict(reversed(list(entities.items())))

    monkeypatch.setattr(registry, "entity_classes", reversed_registry)
    monkeypatch.setattr(module, "entity_classes", reversed_registry, raising=False)
    again = module.render_schemas()
    assert calls, "render_schemas() must call registry.entity_classes() at call time"
    assert list(again) == sorted(again)
    assert again == rendered_files()


def test_render_schemas_is_pure(tmp_path: Path) -> None:
    """P: rendering neither writes files nor changes the working directory, and returns bytes."""
    module = export_module()
    before = os.getcwd()
    with_cwd = tmp_path / "cwd"
    with_cwd.mkdir()
    os.chdir(with_cwd)
    try:
        files = module.render_schemas()
        assert os.listdir(with_cwd) == []
    finally:
        os.chdir(before)
    assert all(isinstance(data, bytes) for data in files.values())


def test_export_json_schema_writes_exactly_the_rendered_bytes(tmp_path: Path) -> None:
    """SCOPE P1-15: ``export_json_schema(directory)`` creates the directory (parents too), writes
    every file of ``render_schemas()`` and the lock, overwrites existing files, and returns the
    sorted names it wrote (schemas and ``schema.lock``)."""
    module = export_module()
    target = tmp_path / "nested" / "schemas"
    written = module.export_json_schema(target)
    files = rendered_files()
    assert written == sorted([*files, LOCK_NAME])
    on_disk = read_directory(target)
    assert set(on_disk) == {*files, LOCK_NAME}
    for name, data in files.items():
        assert on_disk[name] == data, name
    assert on_disk[LOCK_NAME] == module.render_lock(files)
    (target / "Run.schema.json").write_bytes(b"{}")
    module.export_json_schema(str(target))  # a str path is accepted as well
    assert read_directory(target)["Run.schema.json"] == files["Run.schema.json"]


def test_jsonschema_byte_identical_two_runs(tmp_path: Path) -> None:
    """ADR 0008 I10 (SCOPE name): two runs write identical bytes, in-process and into two
    directories."""
    module = export_module()
    assert module.render_schemas() == module.render_schemas()
    first = module.export_json_schema(tmp_path / "a")
    second = module.export_json_schema(tmp_path / "b")
    assert first == second
    assert read_directory(tmp_path / "a") == read_directory(tmp_path / "b")


@pytest.mark.parametrize(
    "env",
    [
        {"PYTHONHASHSEED": "0"},
        {"PYTHONHASHSEED": "1", "TZ": "Asia/Kolkata", "LC_ALL": "C"},
        {
            "PYTHONHASHSEED": "4242",
            "TZ": "America/Los_Angeles",
            "LANG": "hi_IN.UTF-8",
            "PYTHONIOENCODING": "ascii",
        },
    ],
    ids=["hashseed-0", "tz-kolkata-locale-c", "tz-la-hindi-locale-ascii-io"],
)
def test_output_is_independent_of_hash_seed_time_zone_locale_and_cwd(
    env: dict[str, str], tmp_path: Path
) -> None:
    """No environment-dependent content: a fresh interpreter with another hash seed, time zone,
    locale, I/O encoding and working directory renders the same bytes as this process."""
    workdir = tmp_path / "elsewhere"
    workdir.mkdir()
    code = """
        import hashlib, json
        from tracer_evalschema import schema_export
        files = schema_export.render_schemas()
        print(json.dumps({k: hashlib.sha256(v).hexdigest() for k, v in files.items()}))
    """
    out = run_python(code, env=env, cwd=workdir)
    from ._support_b8 import sha256_hex

    expected = {name: sha256_hex(data) for name, data in rendered_files().items()}
    assert json.loads(out) == expected


def test_rendering_imports_no_test_only_package() -> None:
    """The generator is not a runtime dependency beyond pydantic (ADR 0008 Decision 10): rendering
    the schemas in a fresh interpreter never imports a test-only package."""
    code = """
        import sys
        from tracer_evalschema import schema_export
        schema_export.render_schemas()
        forbidden = {"jsonschema", "hypothesis", "yaml", "cryptography", "pytest", "_pytest",
                     "referencing", "rpds"}
        print(json.dumps(sorted(forbidden & {m.split(".")[0] for m in sys.modules})))
    """
    assert json.loads(run_python(code)) == []


def test_schema_export_module_imports_only_stdlib_pydantic_and_the_package() -> None:
    """ADR 0008 Decision 10, by source: every import of ``schema_export.py`` is the standard
    library, pydantic (and its own dependencies) or ``tracer_evalschema``."""
    module = export_module()
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    allowed = set(sys.stdlib_module_names) | {
        "pydantic",
        "pydantic_core",
        "annotated_types",
        "typing_extensions",
        "tracer_evalschema",
        "__future__",
    }
    assert imported <= allowed, sorted(imported - allowed)
    assert "tracer_evalschema" in imported


# ---------------------------------------------------------------------------- the file bytes


@pytest.mark.parametrize("entity", entity_class_names())
def test_files_are_canonical_json_bytes(entity: str) -> None:
    """P (contract 2.3): each file is ``json.dumps(doc, indent=2, sort_keys=True,
    ensure_ascii=True)`` plus one LF: ASCII only, object keys sorted at every level, two-space
    indent, no BOM, no CR, no tab, no trailing space, one final newline. Checked by parsing and
    re-serializing independently of the module."""
    data = rendered_files()[entity + SCHEMA_SUFFIX]
    assert data == canonical_dumps(json.loads(data))
    assert data.endswith(b"}\n")
    assert not data.endswith(b"\n\n")
    assert b"\r" not in data
    assert b"\t" not in data
    assert not data.startswith(b"\xef\xbb\xbf")
    assert all(not line.endswith(b" ") for line in data.split(b"\n"))
    data.decode("ascii")


def test_required_arrays_are_sorted_and_unique_everywhere() -> None:
    """P (contract 2.3, stable ordering): ``required`` is a set, so every ``required`` array of
    every schema object is sorted and has no repeat; reordering the fields of a model changes no
    byte. ``enum``, ``anyOf``, ``allOf``, ``oneOf``, ``items`` and ``prefixItems`` keep their
    declared order, which is the order of the closed set (see
    ``test_enum_order_is_the_models_order``)."""
    for entity, schema in _entities_and_schemas():
        for node in schema_nodes(schema):
            required = node.get("required")
            if isinstance(required, list):
                assert required == sorted(set(required)), (entity, required)


def test_enum_order_is_the_models_order() -> None:
    """P: the exported schema is the model's validation schema with ``$schema`` and ``$id`` added,
    ``required`` sorted and keys sorted (no hidden transform), so every ``enum`` keeps the order
    the model declares. Compared against ``model_json_schema(mode="validation")``."""
    classes = entity_class_by_name()
    for entity, schema in _entities_and_schemas():
        model_schema = classes[entity].model_json_schema(mode="validation")
        exported_enums = [n["enum"] for n in schema_nodes(schema) if "enum" in n]
        model_enums = [n["enum"] for n in schema_nodes(model_schema) if "enum" in n]
        assert sorted(map(json.dumps, exported_enums)) == sorted(map(json.dumps, model_enums))
        for enum in exported_enums:
            assert json.dumps(enum) in {json.dumps(e) for e in model_enums}, (entity, enum)


def test_exported_content_is_the_validation_schema_plus_id_and_dialect() -> None:
    """P (contract 2.4): no hidden transform. Removing ``$schema`` and ``$id``, every exported
    schema equals ``model_json_schema(mode="validation")`` once ``required`` arrays are sorted."""

    def sort_required(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: sorted(v) if k == "required" and isinstance(v, list) else sort_required(v)
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [sort_required(x) for x in node]
        return node

    classes = entity_class_by_name()
    for entity, schema in _entities_and_schemas():
        body = {k: v for k, v in schema.items() if k not in ("$schema", "$id")}
        assert body == sort_required(classes[entity].model_json_schema(mode="validation")), entity


def test_validation_eq_serialization_schema() -> None:
    """ADR 0008 I13 (SCOPE name): pydantic's validation-mode and serialization-mode schemas are
    identical for every entity, and the exported file is that schema."""
    for entity, cls in sorted(entity_class_by_name().items()):
        assert cls.model_json_schema(mode="validation") == cls.model_json_schema(
            mode="serialization"
        ), entity


# ----------------------------------------------------------------- identity and the dialect


def test_schema_ids_are_urns() -> None:
    """ADR 0008 I12 (SCOPE name), Decision 7 and OQ1: ``$id`` is
    ``urn:tracer:evalschema:<SCHEMA_VERSION>:<entity>`` where ``<entity>`` is the class name and
    the file stem; ids are unique; nothing else in the file carries an ``$id``; no id is a URL."""
    seen: set[str] = set()
    for entity, schema in _entities_and_schemas():
        value = schema["$id"]
        assert value == f"urn:tracer:evalschema:{CURRENT_SCHEMA_VERSION}:{entity}"
        match = URN_FORM.fullmatch(value)
        assert match is not None
        assert match.group("entity") == entity == schema["title"]
        assert not value.startswith(("http:", "https:"))
        assert value not in seen
        seen.add(value)
        nested = [n for n in schema_nodes(schema) if "$id" in n]
        assert nested == [schema]


def test_schema_id_helper_follows_the_schema_version() -> None:
    """P: ``schema_id(entity)`` uses ``SCHEMA_VERSION`` and rejects a name that is not a class
    name (a path, a URN, an empty string) with ``ValueError`` so a bad file name cannot be
    produced."""
    module = export_module()
    assert (
        module.schema_id("RunBundle") == f"urn:tracer:evalschema:{api('').SCHEMA_VERSION}:RunBundle"
    )
    for bad in ("", "run", "Run.schema.json", "../Run", "urn:x:Run", "Run Bundle", "R\u00fcn"):
        with pytest.raises(ValueError, match="entity"):
            module.schema_id(bad)


def test_every_file_declares_draft_2020_12_and_is_a_valid_schema() -> None:
    """ADR 0008 Decision 7 (draft 2020-12): ``$schema`` is the 2020-12 meta-schema URI, the file
    passes ``Draft202012Validator.check_schema``, and ``validator_for`` selects that draft."""
    jsonschema = jsonschema_module()

    for entity, schema in _entities_and_schemas():
        assert schema["$schema"] == DIALECT, entity
        jsonschema.Draft202012Validator.check_schema(schema)
        assert jsonschema.validators.validator_for(schema) is jsonschema.Draft202012Validator


def test_the_pinned_dialect_is_the_one_the_adrs_name() -> None:
    """The dialect URI is derived from the ADR text, so a change of draft needs an ADR change:
    ADR 0008 Decision 7 and ADR 0002 name 2020-12, and the URI carries the same year."""
    assert "Draft 2020-12" in (
        ROOT / "docs/adr/0008-schema-versioning-and-compatibility.md"
    ).read_text(encoding="utf-8")
    assert "Draft 2020-12" in (ROOT / "docs/adr/0002-time-and-units.md").read_text(encoding="utf-8")
    assert DIALECT == "https://json-schema.org/draft/2020-12/schema"


def test_every_schema_version_property_is_the_one_current_version() -> None:
    """ADR 0008 Decisions 1 and 2: a file of version V accepts only ``schema_version`` V (a closed
    set of one member, as an ``enum``), because a reader requires an exact match during 0.0.x. The
    exported ``$id`` carries the same version."""
    for entity, schema in _entities_and_schemas():
        prop = schema["properties"]["schema_version"]
        assert prop["type"] == "string", entity
        assert prop["enum"] == [CURRENT_SCHEMA_VERSION], entity
        assert "const" not in prop
        assert "schema_version" in schema["required"]
        assert schema["$id"].split(":")[3] == CURRENT_SCHEMA_VERSION


def test_only_known_keywords_are_used() -> None:
    """P (contract 2.5): every keyword on every schema object is a JSON Schema 2020-12 keyword or
    one of the declared extensions (``x-pii``, ADR 0006 Decision 2). ``const`` and
    ``discriminator`` (an OpenAPI keyword) are not allowed, so a consumer needs no vendor
    vocabulary."""
    assert "const" not in KNOWN_KEYWORDS
    assert "discriminator" not in KNOWN_KEYWORDS
    for entity, schema in _entities_and_schemas():
        unknown = keywords_used(schema) - KNOWN_KEYWORDS
        assert not unknown, (entity, sorted(unknown))
        extensions = keywords_used(schema) & EXTENSION_KEYWORDS
        assert extensions <= {"x-pii"}


def test_format_keyword_is_annotation_only_and_always_backed_by_a_pattern() -> None:
    """Draft 2020-12 treats ``format`` as an annotation and ``jsonschema`` without extras does not
    assert it, so a ``format`` must never be the only constraint: each ``format: date-time`` node
    also carries the canonical-UTC ``pattern`` (ADR 0002 Decision 1) and no other format is used."""
    for entity, schema in _entities_and_schemas():
        for node in schema_nodes(schema):
            if "format" in node:
                assert node["format"] == "date-time", (entity, node["format"])
                assert node.get("type") == "string", (entity, node)
                assert "pattern" in node, (entity, node)


def test_references_are_local_resolvable_and_all_used() -> None:
    """P (contract 2.4): a file is self-contained. Every ``$ref`` is ``#/$defs/<Name>`` and
    resolves inside the file; every ``$defs`` entry is referenced (no dead definitions); no
    ``$anchor``, ``$dynamicRef``, nested ``$schema`` or reference to another file's URN."""
    for entity, schema in _entities_and_schemas():
        defs = schema.get("$defs", {})
        refs = refs_of(schema)
        for ref in refs:
            assert ref.startswith("#/$defs/"), (entity, ref)
            assert ref.removeprefix("#/$defs/") in defs, (entity, ref)
        assert set(defs) <= {r.removeprefix("#/$defs/") for r in refs}, (entity, sorted(defs))
        for node in schema_nodes(schema):
            assert not {"$anchor", "$dynamicRef", "$dynamicAnchor", "$vocabulary"} & set(node)
            assert "$schema" not in node or node is schema
        assert not any(s.startswith("urn:tracer:") for s in refs)


def test_every_defined_value_object_name_is_a_model_or_a_primitive() -> None:
    """The names under ``$defs`` are the pydantic class names (a stable schema name, ADR 0001
    Decision 8), never a generic like ``VersionedRef[PersonaId]`` or a module path."""
    pattern = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
    for entity, schema in _entities_and_schemas():
        for name in schema.get("$defs", {}):
            assert pattern.fullmatch(name), (entity, name)


# Value objects that no entity embeds yet and so are in no exported file. P: each one is either
# reached through a bundle (the sealing types, which stop being listed when ``ExportBundle``
# lands: ``test_every_value_object_is_in_some_file_or_listed`` then requires them) or is a type
# of an importer result or a pure helper that is never part of a stored document.
NOT_IN_ANY_ENTITY_SCHEMA: dict[str, str] = {
    "BidiFlaggedText": "helper base of Turn and Segment text, never a field type",
    "LanguageLabelWarning": "importer result that becomes an IngestWarning (P1-10c)",
    "LiteralProfileValue": "lint finding of the profile placeholder check, never stored",
    "ObservedLanguage": "language helper result; Transcript and Turn store its two fields",
    "VersionedRef": "generic base of the typed references (N6), never a field type itself",
}


def test_every_value_object_is_in_some_file_or_listed() -> None:
    """P (contract 2.2): a consumer must be able to validate every stored value object, so each
    value object of the registry walk appears under ``$defs`` of at least one exported file, or is
    listed in ``NOT_IN_ANY_ENTITY_SCHEMA`` with the reason it is never part of a stored document.
    A listed name that is now in a file, or no longer a class, fails (the list cannot rot). The
    sealing types (``SealedPayload``, ``DekWrap``, ``SealedAadFields``, ``RedactionRecord``) are
    not listed: they must be reached through ``ExportBundle``."""
    defined = {name for schema in parsed().values() for name in schema.get("$defs", {})}
    value_objects = set(api("registry").value_object_classes())
    missing = value_objects - defined - set(NOT_IN_ANY_ENTITY_SCHEMA)
    assert not missing, f"value objects in no exported schema: {sorted(missing)}"
    stale = {n for n in NOT_IN_ANY_ENTITY_SCHEMA if n in defined or n not in value_objects}
    assert not stale, f"listed but now exported or removed: {sorted(stale)}"


# ------------------------------------------------------------------- closed sets, never const


def test_no_const_in_exported_schema_files() -> None:
    """ADR 0003 Decision 6 (``test_no_const_in_generated_schemas``, on the exported files): no
    schema object of any file uses ``const``; a one-member set is a one-element ``enum``."""
    for name, data in rendered_files().items():
        parsed_file = json.loads(data)
        assert all("const" not in node for node in schema_nodes(parsed_file, skip_data=False)), name
        assert "const" not in keywords_used(parsed_file), name


SINGLE_MEMBER_SETS: tuple[tuple[str, str, list[Any]], ...] = (
    ("Money", "currency", ["USD"]),
    ("RangeRule", "kind", ["range"]),
    ("EqualsTrueRule", "kind", ["equals_true"]),
    ("OneOfRule", "kind", ["one_of"]),
    ("WebSocketEndpoint", "transport", ["ws"]),
    ("WebRtcEndpoint", "transport", ["webrtc"]),
    ("SipEndpoint", "transport", ["sip"]),
    ("CostRounding", "mode", ["half_even"]),
    ("CostRounding", "scale", [18]),
    ("CostRounding", "exact", [False]),
    ("SealedPayload", "format", ["tracer.sealed.v1"]),
    ("SealedPayload", "alg", ["A256GCM"]),
    ("DekWrap", "alg", ["A256KW"]),
)


@pytest.mark.parametrize(("definition", "prop", "members"), SINGLE_MEMBER_SETS)
def test_single_member_closed_sets_and_variant_tags_are_enums(
    definition: str, prop: str, members: list[Any]
) -> None:
    """ADR 0003 Decision 6: single-member closed sets (``SupportedCurrency``,
    ``SealedPayload.format`` and ``alg``, ``dek_wrap.alg``, ``CostRounding.mode``) and the tag of
    every discriminated-union variant are ``{"type": "string", "enum": [...]}``. The members are
    the values the contract of each ADR names (the sealing ones are red until P1-14)."""
    found = find_definition(definition)
    assert found is not None, f"{definition} is in no exported schema file"
    _file, schema = found
    node = definition_node(schema, definition)
    prop_schema = resolve(schema, node["properties"][prop])
    assert collect_enums(schema, prop_schema) == members
    if all(isinstance(m, str) for m in members):
        assert prop_schema.get("type") == "string" or any(
            resolve(schema, b).get("type") == "string" for b in prop_schema.get("anyOf", [])
        )
    assert "const" not in json.dumps(prop_schema)


def test_every_enum_is_a_nonempty_unique_closed_set() -> None:
    """An ``enum`` never repeats a member and is never empty; a string enum holds strings only."""
    for entity, schema in _entities_and_schemas():
        for node in schema_nodes(schema):
            if "enum" in node:
                members = node["enum"]
                assert members, entity
                assert len(set(map(json.dumps, members))) == len(members), (entity, members)
                if node.get("type") == "string":
                    assert all(isinstance(m, str) for m in members), (entity, members)


# --------------------------------------------------------------------- patterns and portability


ID_PATTERN = re.compile(r"^\^(?P<alt>\(\?:)?(?P<prefixes>[a-z]+(?:\|[a-z]+)*)\)?_\[0-9a-f\]\{8\}-")


def _id_prefixes(pattern: str) -> list[str] | None:
    match = ID_PATTERN.match(pattern)
    return None if match is None else match.group("prefixes").split("|")


def test_typed_id_patterns_are_the_canonical_wire_pattern() -> None:
    """ADR 0001 Decision 3 and the per-type pattern of P1-04a: every ID ``pattern`` in a file is
    exactly ``ids.wire_pattern(prefix)`` (``(?:a|b)`` for a union), for registered prefixes only.
    A typo or a looser variant (``[0-9a-f-]{36}``) fails."""
    ids = api("ids")
    registered = set(ids.ID_PREFIXES)
    found = 0
    for entity, schema in _entities_and_schemas():
        for pattern in pattern_values(schema):
            prefixes = _id_prefixes(pattern)
            if prefixes is None:
                continue
            found += 1
            assert set(prefixes) <= registered, (entity, pattern)
            joined = prefixes[0] if len(prefixes) == 1 else "(?:" + "|".join(prefixes) + ")"
            assert pattern == ids.wire_pattern(joined), (entity, pattern)
    assert found >= len(entity_class_names()), "every entity has an id pattern"


def test_typed_id_patterns_accept_their_own_kind_only() -> None:
    """For every node with an ID ``pattern``, an ID of each listed prefix is accepted and an ID of
    every other registered prefix is rejected, as are a bare UUIDv7, an uppercase ID, an ID
    followed by a newline and one preceded by a space (ECMA-262 semantics, S-N90)."""
    ids = api("ids")
    import uuid

    bare = str(uuid.UUID(str(ids.RunId.new()).partition("_")[2]))
    for entity, schema in _entities_and_schemas():
        for node in schema_nodes(schema):
            prefixes = _id_prefixes(node.get("pattern", ""))
            if prefixes is None:
                continue
            validator = ecma_validator_class()({"type": "string", "pattern": node["pattern"]})
            for prefix in ids.ID_PREFIXES:
                wire = _sample_id(prefix)
                assert validator.is_valid(wire) is (prefix in prefixes), (entity, prefix)
            own = _sample_id(prefixes[0])
            for bad in (bare, own.upper(), own + "\n", " " + own, own[:-1], own + "0"):
                assert not validator.is_valid(bad), (entity, bad)


def test_typed_id_unions_have_pattern_branches_that_never_overlap() -> None:
    """Union shapes of typed IDs: wherever an ``anyOf`` or ``oneOf`` has ID-pattern branches, (a)
    the branches name distinct prefixes, (b) a nullable ID is ``anyOf [id, {"type": "null"}]``
    with the null branch last, and (c) the union accepts an ID of exactly the prefixes its
    branches name and rejects every other registered kind. Behavioural, so any correct shape
    passes (``anyOf`` of patterns, one alternation pattern, a ``oneOf`` with an object branch)."""
    ids = api("ids")
    union_nodes = 0
    for entity, schema in _entities_and_schemas():
        for node in schema_nodes(schema):
            for keyword in ("anyOf", "oneOf"):
                branches = node.get(keyword)
                if not branches:
                    continue
                named: list[str] = []
                for branch in branches:
                    prefixes = (
                        _id_prefixes(branch.get("pattern", ""))
                        if isinstance(branch, dict)
                        else None
                    )
                    if prefixes:
                        named.extend(prefixes)
                if not named:
                    continue
                union_nodes += 1
                assert len(named) == len(set(named)), (entity, keyword, named)
                if {"type": "null"} in branches:
                    assert branches[-1] == {"type": "null"}, (entity, branches)
                    assert keyword == "anyOf", (entity, keyword)
                validator = ecma_validator_class()({"$defs": schema.get("$defs", {}), **node})
                for prefix in ids.ID_PREFIXES:
                    assert validator.is_valid(_sample_id(prefix)) is (prefix in named), (
                        entity,
                        prefix,
                        named,
                    )
    assert union_nodes >= 1, "the entities hold at least one typed-ID union (nullable references)"


def test_timeline_origin_ref_is_a_union_of_an_artifact_id_and_an_external_ref() -> None:
    """N39 and ADR 0002 Decision 3: ``TimelineOrigin.ref`` is one field holding an ``art_`` ID or
    an ``ExternalRef`` object (a ``oneOf`` of the two), and the exported shape accepts both and
    rejects every other kind of ID, a bare UUID, a number, a list and an empty object."""
    found = find_definition("TimelineOrigin")
    assert found is not None, "TimelineOrigin is in no exported schema file"
    _file, schema = found
    node = definition_node(schema, "TimelineOrigin")
    ref_schema = node["properties"]["ref"]

    def leaves(branch: Any) -> list[Any]:
        for keyword in ("anyOf", "oneOf"):
            if isinstance(branch, dict) and keyword in branch:
                return [leaf for sub in branch[keyword] for leaf in leaves(sub)]
        return [branch]

    options = [b for b in leaves(ref_schema) if b != {"type": "null"}]
    assert len(options) == 2, options
    assert {"$ref": "#/$defs/ExternalRef"} in options
    assert any(_id_prefixes(o.get("pattern", "")) == ["art"] for o in options)
    validator = ecma_validator_class()({"$defs": schema["$defs"], **ref_schema})
    external = {"system": "otel", "kind": "span_id", "value": "00f067aa0ba902b7"}
    assert validator.is_valid(_sample_id("art"))
    assert validator.is_valid(external)
    assert validator.is_valid(None) or "null" not in json.dumps(ref_schema)
    bad_refs: tuple[Any, ...] = (
        _sample_id("run"),
        _sample_id("turn"),
        "0190f2a0-0000-7000-8000-000000000000",
        7,
        [],
        {},
    )
    for bad in bad_refs:
        assert not validator.is_valid(bad), bad


def test_patterns_are_anchored_and_portable() -> None:
    """P (contract 2.6): every ``pattern`` starts with ``^`` and ends with ``$`` (an unanchored
    pattern is a search, a trap), compiles, and uses only syntax with one meaning in ECMA-262,
    Python and the common Rust/Go engines: no inline flags, named groups, atomic groups,
    possessive quantifiers, lookbehind, ``\\A``/``\\Z``, and no shorthand classes (``\\d``,
    ``\\w``, ``\\s``, ``\\b``, ``\\p``), whose Unicode meaning differs between engines. Lookahead
    ``(?!...)`` is allowed (used by the language-tag pattern)."""
    forbidden = re.compile(
        r"\(\?[imsxaulU-]|\(\?P|\(\?<|\(\?>|\(\?#|\*\+|\+\+|\?\+|\\[AZzdDwWsSbBpPhHkKRXG]|\[\[:"
    )
    for entity, schema in _entities_and_schemas():
        for pattern in pattern_values(schema):
            assert pattern.startswith("^"), (entity, pattern)
            assert pattern.endswith("$"), (entity, pattern)
            assert not pattern.endswith("\\$"), (entity, pattern)
            re.compile(pattern)
            outside_classes = re.sub(r"\[(?:\\.|[^\]\\])*\]", "[]", pattern)
            assert forbidden.search(outside_classes) is None, (entity, pattern)
            for cls in re.findall(r"\[(?:\\.|[^\]\\])*\]", pattern):
                assert re.search(r"\\[AZzdDwWsSbBpPhHkKRXG]|\[:", cls) is None, (entity, cls)


# --------------------------------------------------------------------- presence rules (if/then)

# (definition, minimum number of ``if``/``then`` rules in its ``allOf``). The definitions are the
# ones api-surface 22.13 item 11 and 23.9 item 5 say emit presence rules; the counts are the
# number emitted when this batch was written, so removing a rule fails. SealedAadFields (one rule
# per payload type) lives in the bundle's schema and stays red until P1-14.
PRESENCE_RULES: dict[str, int] = {
    "PrivacyMarker": 1,
    "ChannelMap": 1,
    "Turn": 1,
    "ToolCallEvent": 1,
    "MetricResult": 7,
    "DatasetItem": 4,
    "StageSpan": 1,
    "ExpectedToolCall": 3,
    "MetricDefinition": 6,
    "UsageQuantity": 1,
    "UsageRecord": 1,
    "AppliesWhenCondition": 3,
    "PriceEntry": 1,
    "ReportedCostRaw": 1,
    "CostRecord": 11,
    "CostExclusion": 1,
    "CostSummary": 1,
    "SealedAadFields": 1,
}


@pytest.mark.parametrize(("definition", "minimum"), sorted(PRESENCE_RULES.items()))
def test_documented_presence_rules_are_emitted_as_if_then(definition: str, minimum: int) -> None:
    """N12/N38 (revision 11 recommendation, applied in batches 3 to 5): a presence rule keyed on
    a closed discriminator is emitted as draft 2020-12 ``allOf`` of ``if``/``then``. Each
    definition listed holds at least the documented number of such rules, and every ``if`` names
    a property (a closed-set ``enum`` or a presence ``required``) of the same object."""
    found = find_definition(definition)
    assert found is not None, f"{definition} is in no exported schema file"
    _file, schema = found
    node = definition_node(schema, definition)
    assert if_then_count(node) >= minimum, (definition, if_then_count(node))
    for rule in node["allOf"]:
        if "if" in rule:
            keys = set(rule["if"].get("properties", {})) | set(rule["if"].get("required", []))
            assert keys, (definition, rule["if"])
            assert "then" in rule


def test_unique_items_are_kept_where_the_models_reject_a_repeat() -> None:
    """N78 and the earlier lists: the properties of ``UNIQUE_ITEMS_PROPERTIES`` carry
    ``uniqueItems: true`` in the exported files (a repeat is rejected by the schema, not only by
    the model). The set may grow, never shrink; the register (``CO-KEYED-UNIQUENESS`` and
    ``CO-LONG-LIST-UNIQUENESS`` claim no exact repeat of these) must not hide the loss of one.
    The set is the 17 property names that carry the keyword in the files of revision 23; the
    long lists of the bundles are not in it on purpose
    (``test_long_lists_carry_no_unique_items``)."""
    found: set[str] = set()
    for _entity, schema in _entities_and_schemas():
        containers = [schema, *schema.get("$defs", {}).values()]
        for container in containers:
            for name, prop in container.get("properties", {}).items():
                if _carries_unique_items(prop):
                    found.add(name)
    assert found >= UNIQUE_ITEMS_PROPERTIES, sorted(UNIQUE_ITEMS_PROPERTIES - found)


def _carries_unique_items(prop: dict[str, Any]) -> bool:
    if prop.get("uniqueItems") is True:
        return True
    return any(_carries_unique_items(branch) for branch in prop.get("anyOf", []))


def _containers_named(schema: dict[str, Any], class_name: str) -> list[dict[str, Any]]:
    """The schema objects that describe ``class_name`` in one file: the root when the file is
    that entity, and its entry in ``$defs`` (a bundle embeds the records it holds)."""
    found = [schema] if schema.get("title") == class_name else []
    if class_name in schema.get("$defs", {}):
        found.append(schema["$defs"][class_name])
    return found


def test_long_lists_carry_no_unique_items() -> None:
    """A22 (security, auto-approved; amends A13): the record lists of ``RunBundle`` and
    ``ExportBundle`` and ``PricingCatalog.entries`` carry NO ``uniqueItems``, in their own files
    and in every file that embeds them. A validator compares every pair of objects for that
    keyword, so a 4,000-turn bundle took 3.63 s against 0.18 s and the cost grows with the
    square of the length: a hostile bundle would keep a validator busy for hours. The models
    reject a repeated record in linear time (``CO-LONG-LIST-UNIQUENESS``). The keyword stays on
    the short lists of ``UNIQUE_ITEMS_PROPERTIES``."""
    checked = 0
    for entity, name in A22_NO_UNIQUE_ITEMS:
        assert name in parsed()[entity]["properties"], (entity, name)
        for file_entity, schema in _entities_and_schemas():
            for container in _containers_named(schema, entity):
                prop = container["properties"][name]
                assert not _carries_unique_items(prop), (file_entity, entity, name)
                checked += 1
    assert checked >= len(A22_NO_UNIQUE_ITEMS)


def test_unique_items_is_only_for_arrays_of_scalars() -> None:
    """A22 as a rule, not a list: ``uniqueItems: true`` appears only on arrays whose items are
    strings (or other scalars), in every file, wherever the array lives. An array of objects
    (``$ref``, ``type: object``, ``properties``) never carries it: a validator compares every pair
    of objects (quadratic). A new object list therefore cannot arrive with the keyword. (A21
    decided: ``ConfigSnapshot.metric_definitions`` and ``metric_bindings`` carry none either; the
    models reject a repeat and the register claims it.) The properties found are the ones of
    ``UNIQUE_ITEMS_PROPERTIES`` and no others."""
    scalars = {"string", "integer", "number", "boolean", "null"}
    offenders: list[str] = []
    carriers: set[str] = set()
    for file_entity, schema in _entities_and_schemas():
        for container_name, container in [("<root>", schema), *schema.get("$defs", {}).items()]:
            class_name = schema["title"] if container_name == "<root>" else container_name
            for name, prop in container.get("properties", {}).items():
                for branch in [prop, *prop.get("anyOf", [])]:
                    if branch.get("uniqueItems") is not True:
                        continue
                    carriers.add(name)
                    items = branch.get("items", {})
                    is_scalar = items.get("type") in scalars or "enum" in items
                    if not is_scalar:
                        offenders.append(f"{file_entity}:{class_name}.{name}")
    assert not offenders, sorted(set(offenders))
    assert carriers == UNIQUE_ITEMS_PROPERTIES, sorted(carriers ^ UNIQUE_ITEMS_PROPERTIES)


def _bundle_with_long_lists(turns: int, tool_calls: int) -> dict[str, Any]:
    """The batch 7 run bundle with ``turns`` distinct turns and ``tool_calls`` distinct tool calls
    (fresh IDs; everything else copied), valid for the schema. The models are not asked: this is
    a measurement of the schema, and the elements must differ or ``uniqueItems`` would reject at
    once."""
    doc: dict[str, Any] = json.loads(json.dumps(sample_document("RunBundle", "t1_human")))
    turn, call = doc["turns"][0], doc["tool_calls"][0]
    doc["turns"] = [{**turn, "id": _sample_id("turn")} for _ in range(turns)]
    doc["tool_calls"] = [{**call, "id": _sample_id("tcl")} for _ in range(tool_calls)]
    return doc


def _without_unique_items(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _without_unique_items(v) for k, v in node.items() if k != "uniqueItems"}
    if isinstance(node, list):
        return [_without_unique_items(v) for v in node]
    return node


def _with_unique_items_on_long_lists(schema: dict[str, Any]) -> dict[str, Any]:
    """The previous behaviour (A13 as first implemented), to prove the bound can fail."""
    copy_: dict[str, Any] = json.loads(json.dumps(schema))
    for name in ("turns", "tool_calls"):
        copy_["properties"][name]["uniqueItems"] = True
    return copy_


def _fastest(validator: Any, doc: dict[str, Any], runs: int) -> float:
    import time

    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        assert validator.is_valid(doc)
        best = min(best, time.perf_counter() - started)
    return best


def test_validating_a_4000_record_bundle_costs_no_more_than_without_the_keyword() -> None:
    """A22 (calibrated, no absolute time): a 4,000-turn, 600-tool-call bundle (the A-05 shape) is
    validated against the exported ``RunBundle`` schema and against the same schema with every
    ``uniqueItems`` removed. Both are linear in the length, so the ratio of their best-of-three
    times stays near 1; with the keyword on the long lists it was about 20 (3.63 s against 0.18
    s). The bound is 3, a margin of 3 over the noise seen and of about 7 under the failure.
    The baseline is measured on the same machine in the same process, so a slow runner moves both.
    A last check proves the bound can fail: the same schema with the keyword put back on the two
    lists exceeds it (once, because it is the slow case)."""
    schema = parsed()["RunBundle"]
    doc = _bundle_with_long_lists(4000, 600)
    baseline_schema = _without_unique_items(schema)
    baseline = _fastest(schema_validator(baseline_schema), doc, 3)
    shipped = _fastest(schema_validator(schema), doc, 3)
    assert shipped <= 3.0 * baseline, (shipped, baseline, shipped / baseline)
    restored = schema_validator(_with_unique_items_on_long_lists(baseline_schema))
    slow = _fastest(restored, doc, 1)
    assert slow > 3.0 * baseline, ("the bound cannot fail", slow, baseline)


def test_result_complete_is_an_if_then_rule_and_never_stricter_than_the_model() -> None:
    """A19: a non-null ``result_complete`` needs ``result_raw``, expressed as draft 2020-12
    ``if``/``then`` (the ``if`` names ``result_complete`` as a boolean; the ``then`` requires a
    string ``result_raw``) and not as ``dependentRequired``, which counts a present ``null`` as
    present: the model treats ``result_complete: null`` as absent, so a ``dependentRequired``
    schema would be stricter than the model (26.7 forbids that). Behaviour on the batch 3 tool
    call: null or absent ``result_complete`` without ``result_raw`` is valid for model and schema;
    a boolean without ``result_raw`` is invalid for both; with it, valid for both."""
    schema = parsed()["ToolCallEvent"]
    assert "dependentRequired" not in json.dumps(schema)
    rules = [
        rule
        for rule in schema["allOf"]
        if "result_complete" in rule.get("if", {}).get("properties", {})
    ]
    assert len(rules) == 1, rules
    condition, outcome = rules[0]["if"], rules[0]["then"]
    assert condition["required"] == ["result_complete"]
    assert condition["properties"]["result_complete"] == {"type": "boolean"}
    assert outcome["required"] == ["result_raw"]
    assert outcome["properties"]["result_raw"] == {"type": "string"}

    base = json.loads(json.dumps(sample_document("ToolCallEvent", "complete")))
    for key in ("result_raw", "result_complete"):
        base.pop(key, None)
    validator = schema_validator(schema)
    model = entity_class_by_name()["ToolCallEvent"]
    cases: list[tuple[str, dict[str, Any], bool]] = [
        ("absent", {}, True),
        ("null", {"result_complete": None}, True),
        ("both null", {"result_complete": None, "result_raw": None}, True),
        ("null with raw", {"result_complete": None, "result_raw": "{}"}, True),
        ("true without raw", {"result_complete": True}, False),
        ("false without raw", {"result_complete": False}, False),
        ("true with null raw", {"result_complete": True, "result_raw": None}, False),
        ("true with raw", {"result_complete": True, "result_raw": "{}"}, True),
        ("false with raw", {"result_complete": False, "result_raw": "{}"}, True),
    ]
    for label, extra, expected in cases:
        doc = {**base, **extra}
        assert validator.is_valid(doc) is expected, (label, "schema")
        assert model_accepts(model, doc) is expected, (label, "model")


def test_presence_rules_behave_for_privacy_marker_stage_span_and_channel_map() -> None:
    """The emitted rules reject what the models reject, checked on the exported files with
    documents built by the batch 3 helpers: a human-subject marker needs consent and retention
    and a synthetic one forbids them (P-46); ``stage=other`` needs ``stage_raw`` and any other
    stage forbids it (P-05); an inferred channel map needs ``method`` and ``confidence`` and a
    declared one forbids ``confidence`` (ADR 0012 I8, I9)."""
    _f, art_schema = find_definition("Artifact") or ("", {})
    privacy = definition_validator(art_schema, "PrivacyMarker")
    assert privacy.is_valid(privacy_human())
    assert privacy.is_valid(privacy_synthetic())
    assert not privacy.is_valid({"human_subject": True})
    assert not privacy.is_valid({**privacy_synthetic(), "consent": privacy_human()["consent"]})
    _f, span_schema = find_definition("StageSpan") or ("", {})
    span = ecma_validator_class()(span_schema)
    base = dict(span_doc())
    assert span.is_valid(json.loads(json.dumps(base)))
    assert not span.is_valid({**base, "stage": "other"})
    assert span.is_valid({**base, "stage": "other", "stage_raw": "vad"})
    assert not span.is_valid({**base, "stage_raw": "vad"})
    _f, run_schema = find_definition("Run") or ("", {})
    channels = definition_validator(run_schema, "ChannelMap")
    assert channels.is_valid(channel_map_doc())
    assert channels.is_valid(inferred_map_doc())
    assert not channels.is_valid({**channel_map_doc(), "confidence": 0.9})
    assert not channels.is_valid({k: v for k, v in inferred_map_doc().items() if k != "confidence"})
    assert artifact_doc()["privacy"]["human_subject"] is True


# ------------------------------------------------------------------ secrets in the schemas


def test_no_secret_like_property_names_or_example_keys_in_any_exported_schema() -> None:
    """ADR 0007 Decision 3 (and the batch 5 sealed walk), on the exported files: no property name
    (including ``patternProperties`` keys and ``$defs`` names) is secret-like (``api_key``,
    ``password``, ``bearer``, ``client_secret``, ...); no schema object carries ``examples`` or
    ``example``; no ``default`` or ``enum`` member or description holds a credential-shaped value
    (known provider prefixes, high-entropy runs); ``input_tokens`` and ``key_id`` stay allowed."""
    credentials = api("credentials")
    for entity, schema in _entities_and_schemas():
        for name in property_names_anywhere(schema):
            assert not secret_like(name), (entity, name)
        for name in schema.get("$defs", {}):
            assert not secret_like(name), (entity, name)
        for node in schema_nodes(schema):
            assert "examples" not in node, entity
            assert "example" not in node, entity
            for key in node.get("patternProperties", {}):
                assert not secret_like(key), (entity, key)
        for node in schema_nodes(schema, skip_data=False):
            for value in (node.get("default"), *node.get("enum", [])):
                if isinstance(value, str):
                    assert credentials.credential_reason(value) is None, (entity, value)
            description = node.get("description")
            if isinstance(description, str):
                assert not credentials.contains_credential_pattern(description), entity


def test_secret_name_detector_still_flags_the_known_names() -> None:
    """Self-test of the walk's detector (it is the batch 2 helper): the ADR 0007 names are
    flagged and the legitimate near misses are not, so the test above cannot pass by accident."""
    for flagged in ("api_key", "x-api-key", "clientSecret", "password", "bearer", "private_key"):
        assert secret_like(flagged), flagged
    for allowed in ("input_tokens", "key_id", "cache_read_input_tokens", "entry_key", "keyword"):
        assert not secret_like(allowed), allowed


# ------------------------------------------------------------- environment-dependent content


def test_no_environment_dependent_content_in_the_files() -> None:
    """No path of the machine, no interpreter or dependency version, no host name, no object
    representation and no memory address in any generated byte (ADR 0008 Decision 9:
    reproducible artifacts). Checks the working directory, home, temporary directory,
    ``sys.prefix``, this checkout, the Python and pydantic versions, the host name, the
    ``site-packages`` marker and absolute-path prefixes of the three common systems."""
    pydantic = __import__("pydantic")
    needles = {
        "cwd": os.getcwd(),
        "home": str(Path.home()),
        "tmp": tempfile.gettempdir(),
        "prefix": sys.prefix,
        "executable": sys.executable,
        "checkout": str(ROOT),
        "package dir": str(PACKAGE_DIR),
        "python version": sys.version.split()[0],
        "pydantic version": pydantic.VERSION,
        "site-packages": "site-packages",
        "mac home": "/Users/",
        "linux home": "/home/",
        "windows path": "C:\\\\",
        "windows users": "\\\\Users\\\\",
        "private tmp": "/private/var/",
    }
    host = platform.node()
    if len(host) > 4 and host not in {"localhost"}:
        needles["host name"] = host
    for name, data in rendered_files().items():
        text = data.decode("ascii")
        for label, needle in needles.items():
            if len(needle) > 3:
                assert needle not in text, (name, label)
        for leak in REPR_LEAKS:
            assert leak.search(text) is None, (name, leak.pattern)
        assert not any("\n" in s and s.strip() == "" for s in all_strings(json.loads(data)))


def test_descriptions_are_dedented_text_without_trailing_space() -> None:
    """Docstring-derived ``description`` text is normalized: pydantic cleans the indentation, and
    the schema holds no line with trailing white space and no tab, so a Python version that
    dedents docstrings differently (3.13) cannot change a byte."""
    for entity, schema in _entities_and_schemas():
        for node in schema_nodes(schema):
            description = node.get("description")
            if isinstance(description, str):
                assert "\t" not in description, entity
                assert all(line == line.rstrip() for line in description.split("\n")), entity
                assert description == description.strip(), entity


def test_exporting_does_not_write_outside_the_target(tmp_path: Path) -> None:
    """``export_json_schema(directory)`` touches only ``directory`` (no ``.pyc`` or cache next to
    it, no file in the working directory)."""
    module = export_module()
    workdir = tmp_path / "work"
    workdir.mkdir()
    previous = os.getcwd()
    os.chdir(workdir)
    try:
        module.export_json_schema(tmp_path / "out")
        assert list(workdir.iterdir()) == []
    finally:
        os.chdir(previous)
    assert {p.name for p in tmp_path.iterdir()} == {"work", "out"}
