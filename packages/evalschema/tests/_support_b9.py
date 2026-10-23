# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch 12 (P1-16), shared helpers: the golden and frozen fixtures. No tests here.

Three parts:

* a deterministic scope (``scope()``): while it is active the ID generator, ``os.urandom`` and the
  catalog entry counter of the batch 4 helpers give the same values on every run, so a fixture
  built twice is byte-identical;
* registries of fixture specifications (``VALID`` and ``INVALID``, filled by the ``_support_b9_*``
  modules) and the functions that turn a specification into files (``render_all``);
* readers and assertions used by ``test_golden_fixtures.py`` and by
  ``scripts/build_golden_fixtures.py``.

Layout written under ``tests/fixtures`` (see ``fixtures/README.md``)::

    golden/<ClassName>/valid/<name>.json          the full JSON-mode dump of a valid document
    golden/<ClassName>/invalid/<name>.json        a document the model must reject
    golden/<ClassName>/invalid/<name>.expected.json
    frozen/0.0.1/<ClassName>.minimal.json         byte copies of the golden minimal and maximal
    frozen/0.0.1/<ClassName>.maximal.json
    frozen/0.0.1/digests.json                     file SHA-256 and entity digest per frozen file

A valid fixture holds every serialized member (an unset optional member is an explicit null), so
that the model's own dump of the loaded document is the file again, byte for byte.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import importlib.util
import itertools
import json
import os
import re
import unicodedata
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import _support_b4 as b4
from ._support import CURRENT_SCHEMA_VERSION, FIXTURES, api

GOLDEN = FIXTURES / "golden"
FROZEN = FIXTURES / "frozen" / CURRENT_SCHEMA_VERSION

_BASE_MS = 1_792_756_800_000  # 2026-10-23T12:00:00Z, fixed so that IDs look like recent UUIDv7s


# ------------------------------------------------------------------------------ determinism


def _stream(label: str, counter: Iterator[int], size: int) -> bytes:
    out = b""
    while len(out) < size:
        out += hashlib.sha256(f"{label}/{next(counter)}".encode()).digest()
    return out[:size]


@contextlib.contextmanager
def scope(label: str) -> Iterator[None]:
    """Make every random source the fixture builders use a pure function of ``label``.

    Patched for the duration of the block: ``tracer_evalschema.ids.uuid7`` (so ``TypedId.new()``),
    ``os.urandom`` (the sealed payload helpers) and the entry-key counter of the batch 4 helpers.
    Two scopes with the same label build the same documents; the order of the scopes does not
    matter."""
    ids = api("ids")
    ids_counter = itertools.count()
    bytes_counter = itertools.count()

    def fake_uuid7() -> uuid.UUID:
        n = next(ids_counter)
        raw = int.from_bytes(_stream(f"id/{label}", itertools.count(n), 10), "big")
        rand_a = raw >> 62 & 0xFFF
        rand_b = raw & ((1 << 62) - 1)
        value = ((_BASE_MS + n) << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
        return uuid.UUID(int=value)

    def fake_urandom(size: int) -> bytes:
        return _stream(f"bytes/{label}", bytes_counter, size)

    saved_uuid7 = ids.uuid7
    saved_urandom = os.urandom
    saved_keys = b4._ENTRY_KEYS
    ids.uuid7 = fake_uuid7
    os.urandom = fake_urandom
    b4._ENTRY_KEYS = itertools.count(1)
    try:
        yield
    finally:
        ids.uuid7 = saved_uuid7
        os.urandom = saved_urandom
        b4._ENTRY_KEYS = saved_keys


# ------------------------------------------------------------------------------- the model


def entity_models() -> dict[str, Any]:
    """Class name -> entity class of every registered entity."""
    registry = api("registry")
    return {cls.__name__: cls for cls in registry.entity_classes().values()}


INDIC = re.compile("[\u0900-\u0d7f]")  # Devanagari to Malayalam: the scripts of the nine languages
_INVISIBLE = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def _escape_invisible(text: str) -> str:
    """Write every control, format, private-use, unassigned and separator character (bidi controls,
    ZWJ, ZWNJ, lone surrogates) as a JSON escape, so that a reviewer sees it in the file. Only
    string contents can hold such characters, so the text stays valid JSON."""
    out: list[str] = []
    for char in text:
        if char.isascii() or unicodedata.category(char) not in _INVISIBLE:
            out.append(char)
            continue
        code = ord(char)
        if code > 0xFFFF:
            code -= 0x10000
            out.append(f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 0x3FF):04x}")
        else:
            out.append(f"\\u{code:04x}")
    return "".join(out)


def pretty(document: Any) -> bytes:
    """The bytes of a fixture file: two-space indent, members in model order, real Unicode except
    invisible characters (written as escapes), one trailing newline."""
    text = json.dumps(document, indent=2, ensure_ascii=False)
    return (_escape_invisible(text) + "\n").encode("utf-8")


def dump(model: Any, document: Mapping[str, Any]) -> dict[str, Any]:
    """The model's own JSON-mode dump of ``document`` (validated first, nulls kept)."""
    dumped: dict[str, Any] = model.model_validate(document).model_dump(mode="json", by_alias=True)
    return dumped


def canonical(document: Any) -> bytes:
    """RFC 8785 bytes of a JSON-mode value (what two equal documents share)."""
    result: bytes = api("digest").jcs_bytes(document)
    return result


# --------------------------------------------------------------------------- specifications

DocBuilder = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class InvalidSpec:
    """A document the model must reject, and where and how.

    ``path`` is the location of the first error (pydantic ``loc``, empty for a model-level rule),
    ``kind`` the pydantic error type or, for a typed error of the package raised unwrapped, the
    class name. ``tag`` is the bracketed tag a bundle rule puts at the start of its message."""

    entity: str
    name: str
    build: DocBuilder
    path: tuple[str | int, ...]
    kind: str
    why: str
    tag: str | None = None


VALID: dict[str, dict[str, DocBuilder]] = {}
INVALID: dict[str, list[InvalidSpec]] = {}


def valid(entity: str, name: str) -> Callable[[DocBuilder], DocBuilder]:
    """Register a builder of a valid document (registered in definition order)."""

    def register(builder: DocBuilder) -> DocBuilder:
        entries = VALID.setdefault(entity, {})
        assert name not in entries, (entity, name)
        entries[name] = builder
        return builder

    return register


def invalid(
    entity: str,
    name: str,
    build: DocBuilder,
    path: Sequence[str | int],
    kind: str,
    why: str,
    tag: str | None = None,
) -> None:
    specs = INVALID.setdefault(entity, [])
    assert name not in {s.name for s in specs}, (entity, name)
    specs.append(InvalidSpec(entity, name, build, tuple(path), kind, why, tag))


# ------------------------------------------------------------------------------ rendering


def build_valid(entity: str, name: str) -> dict[str, Any]:
    """The normalized document of a valid fixture (the model's dump, nulls kept)."""
    model = entity_models()[entity]
    with scope(f"{entity}/valid/{name}"):
        return dump(model, VALID[entity][name]())


def build_invalid(spec: InvalidSpec) -> dict[str, Any]:
    with scope(f"{spec.entity}/invalid/{spec.name}"):
        return spec.build()


def probe(entity: str, document: Mapping[str, Any]) -> tuple[tuple[str | int, ...], str, str]:
    """(location, kind, message) of the first error ``document`` raises for the entity's model.

    Raises ``AssertionError`` when the model accepts the document."""
    from pydantic import ValidationError

    model = entity_models()[entity]
    try:
        model.model_validate(document)
    except ValidationError as error:
        first = error.errors(include_input=False, include_url=False)[0]
        return tuple(first["loc"]), str(first["type"]), str(first["msg"])
    except api("errors").TracerSchemaError as error:
        return (), type(error).__name__, str(error)
    raise AssertionError(f"{entity}: the model accepted a document that must be invalid")


@dataclass
class Rendered:
    """Everything the generator writes, as relative path -> bytes."""

    files: dict[str, bytes] = field(default_factory=dict)


@functools.cache
def _schema_validator(entity: str) -> Any:
    """The validator of the JSON Schema that is the contract: the exported file once
    ``schema_export`` exists, else the schema pydantic generates. ECMA-262 pattern semantics, like
    every portable validator."""
    from . import _support_b8 as b8

    if importlib.util.find_spec("tracer_evalschema.schema_export") is not None:
        return b8.schema_validator(json.loads(b8.rendered_files()[f"{entity}{b8.SCHEMA_SUFFIX}"]))
    return b8.schema_validator(entity_models()[entity].model_json_schema(mode="validation"))


def schema_rejects(entity: str, document: Mapping[str, Any]) -> bool:
    """Whether the JSON Schema of the entity (see ``_schema_validator``) rejects ``document``."""
    return not _schema_validator(entity).is_valid(document)


def expected_document(spec: InvalidSpec, document: Mapping[str, Any]) -> dict[str, Any]:
    """The ``expected.json`` of an invalid fixture. ``schema`` is ``rejects`` when the JSON Schema
    (the exported file) also rejects the document and ``code_only`` when only the model's code
    can."""
    result: dict[str, Any] = {
        "error_path": list(spec.path),
        "error_kind": spec.kind,
        "error_tag": spec.tag,
        "schema": "rejects" if schema_rejects(spec.entity, document) else "code_only",
        "why": spec.why,
    }
    return result


def entity_digest(model: Any, document: Mapping[str, Any]) -> dict[str, Any]:
    """The plain ``sha256-jcs/v1`` digest of a document's digest domain (ADR 0006 Decision 5)."""
    loaded = model.model_validate(document)
    digest = api("digest").compute_digest(loaded)
    dumped: dict[str, Any] = digest.model_dump(mode="json", exclude_none=True)
    return dumped


def render_all() -> Rendered:
    """Build every golden and frozen fixture in memory (no file is touched)."""
    _load_specs()
    models = entity_models()
    out = Rendered()
    problems: list[str] = []
    digests: dict[str, Any] = {}
    for entity in sorted(models):
        for name in VALID.get(entity, {}):
            try:
                document = build_valid(entity, name)
            except Exception as error:
                raise RuntimeError(f"valid fixture {entity}/{name}: {error}") from error
            data = pretty(document)
            out.files[f"golden/{entity}/valid/{name}.json"] = data
            if name in ("minimal", "maximal"):
                frozen_name = f"{entity}.{name}.json"
                out.files[f"frozen/{CURRENT_SCHEMA_VERSION}/{frozen_name}"] = data
                digests[frozen_name] = {
                    "file_sha256": hashlib.sha256(data).hexdigest(),
                    "entity_digest": entity_digest(models[entity], document),
                }
        for spec in INVALID.get(entity, []):
            document = build_invalid(spec)
            try:
                path, kind, _message = probe(entity, document)
            except AssertionError as error:
                problems.append(f"{entity}/{spec.name}: {error}")
                continue
            if (path, kind) != (spec.path, spec.kind):
                problems.append(
                    f"{entity}/{spec.name}: expected {spec.path!r} {spec.kind!r}, "
                    f"the model reports {path!r} {kind!r}: {_message[:160]}"
                )
            if spec.tag is not None and f"[{spec.tag}]" not in _message:
                problems.append(f"{entity}/{spec.name}: the message lacks the tag [{spec.tag}]")
            out.files[f"golden/{entity}/invalid/{spec.name}.json"] = pretty(document)
            out.files[f"golden/{entity}/invalid/{spec.name}.expected.json"] = pretty(
                expected_document(spec, document)
            )
    if problems:
        raise RuntimeError("invalid fixtures disagree with the models:\n" + "\n".join(problems))
    out.files["golden/needs_native_review.json"] = pretty(
        {
            "status": "needs_native_review",
            "note": "Every string in an Indic script in these fixtures is synthetic and has not "
            "been reviewed by a native speaker. The list is generated.",
            "files": sorted(
                name
                for name, data in out.files.items()
                if name.startswith("golden/") and INDIC.search(data.decode("utf-8"))
            ),
        }
    )
    out.files[f"frozen/{CURRENT_SCHEMA_VERSION}/digests.json"] = pretty(
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "note": "file_sha256 is the SHA-256 of the file bytes; entity_digest is the plain "
            "sha256-jcs/v1 digest of the entity (digest domain of ADR 0006 Decision 5)",
            "files": dict(sorted(digests.items())),
        }
    )
    return out


def _load_specs() -> None:
    """Import the specification modules (they register on import)."""
    from . import _support_b9_invalid, _support_b9_valid  # noqa: F401


# --------------------------------------------------------------------------------- reading


def golden_valid_files(entity: str) -> list[Path]:
    return sorted((GOLDEN / entity / "valid").glob("*.json"))


def golden_invalid_files(entity: str) -> list[Path]:
    return sorted(
        p
        for p in (GOLDEN / entity / "invalid").glob("*.json")
        if not p.name.endswith(".expected.json")
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------- frozen files

FROZEN_ROOT = FIXTURES / "frozen"
FROZEN_EXTRA_FILES = frozenset({"schema.lock", "README.md"})


def frozen_directories() -> list[Path]:
    """Every ``frozen/<version>/`` directory, oldest name first."""
    if not FROZEN_ROOT.is_dir():
        return []
    return sorted(p for p in FROZEN_ROOT.iterdir() if p.is_dir())


def frozen_problems(directory: Path) -> list[str]:
    """Tagged problems of a frozen directory: files not recorded in ``digests.json``, recorded
    files that are missing or have other bytes, and a manifest that names another version."""
    problems: list[str] = []
    manifest_path = directory / "digests.json"
    if not manifest_path.is_file():
        return [f"[manifest] {directory.name}/digests.json is missing"]
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != directory.name:
        problems.append(f"[version] digests.json of {directory.name} names another version")
    recorded: Mapping[str, Any] = manifest.get("files", {})
    for name, entry in sorted(recorded.items()):
        path = directory / name
        if not path.is_file():
            problems.append(f"[missing] {name}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != entry["file_sha256"]:
            problems.append(f"[changed] {name}")
    on_disk = {p.name for p in directory.iterdir() if p.is_file()}
    for name in sorted(on_disk - set(recorded) - FROZEN_EXTRA_FILES - {"digests.json"}):
        problems.append(f"[extra] {name}")
    return problems
