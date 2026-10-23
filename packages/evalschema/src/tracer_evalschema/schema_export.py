# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""JSON Schema export of every registered entity, ``schema.lock`` and the drift check (P1-15).

ADR 0008 Decisions 7 to 10, invariants I10 to I13; contract: ``docs/phase1/api-surface.md``
section 26. One file ``<ClassName>.schema.json`` per entity of ``registry.entity_classes()``;
value objects travel under ``$defs`` of the entities that embed them. Each file is the model's
validation-mode schema with ``$schema`` (draft 2020-12) and a URN ``$id`` added and every
``required`` array sorted, nothing else. The bytes are canonical: ``json.dumps(document,
indent=2, sort_keys=True, ensure_ascii=True)`` plus one LF, so they do not depend on the machine,
the interpreter, the hash seed or the order fields are declared in.

The generated files and ``schema.lock`` (``{algorithm, files: {name: sha256}, schema_version}``)
are committed package data under ``tracer_evalschema/schemas/`` and ship in the wheel and the
sdist. They are regenerated, never edited by hand::

    python -m tracer_evalschema.schema_export --write    # after a model change; review the diff
    python -m tracer_evalschema.schema_export --check    # CI: exit 1 on any drift

This module needs only the standard library, pydantic and the package itself; hashing goes
through ``digest.sha256_hex`` (``digest`` is the one module that imports ``hashlib``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from tracer_evalschema import SCHEMA_VERSION, registry
from tracer_evalschema.digest import sha256_hex

__all__ = [
    "LOCK_ALGORITHM",
    "LOCK_NAME",
    "SCHEMA_DIALECT",
    "SCHEMA_SUFFIX",
    "check_schema_directory",
    "export_json_schema",
    "main",
    "render_lock",
    "render_schemas",
    "schema_directory",
    "schema_id",
]

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
"""The JSON Schema dialect of every exported file (ADR 0008 Decision 7)."""

SCHEMA_SUFFIX = ".schema.json"
LOCK_NAME = "schema.lock"
LOCK_ALGORITHM = "sha256"

_ENTITY_NAME = re.compile(r"[A-Z][A-Za-z0-9]*")
_SCHEMA_FILE = re.compile(r"(?P<entity>[A-Z][A-Za-z0-9]*)\.schema\.json")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_LOCK_KEYS = frozenset({"algorithm", "files", "schema_version"})

# Keys whose values are data, never schemas: a ``required`` list inside them is not a schema's.
_DATA_KEYWORDS = frozenset({"const", "default", "enum", "examples"})
# Keys whose values are maps of name -> schema (the names themselves are not keywords).
_SCHEMA_MAPS = frozenset({"$defs", "dependentSchemas", "patternProperties", "properties"})


# ------------------------------------------------------------------------------ identity


def schema_id(entity: str) -> str:
    """The ``$id`` of an entity's file: ``urn:tracer:evalschema:<SCHEMA_VERSION>:<entity>``.

    ``entity`` is a class name (ASCII letters and digits, starting with a capital letter);
    anything else (a path, a URN, a file name, an empty string) raises ``ValueError``, so no file
    name outside the pinned form can be produced.
    """
    if not isinstance(entity, str) or _ENTITY_NAME.fullmatch(entity) is None:
        raise ValueError(
            f"entity must be a class name such as 'Run' or 'RunBundle', not {entity!r}"
        )
    return f"urn:tracer:evalschema:{SCHEMA_VERSION}:{entity}"


def schema_directory() -> Path:
    """The shipped directory of generated files: ``schemas/`` next to this module.

    It is package data, so it exists in a checkout, in an installed wheel and in an unpacked
    sdist, and it is found from the package, never from the working directory.
    """
    return Path(__file__).resolve().parent / "schemas"


# ------------------------------------------------------------------------------ rendering


def _canonical(document: Any) -> bytes:
    """The byte form of every generated file (contract 2.3)."""
    text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True)
    return text.encode("ascii") + b"\n"


def _sorted_required(node: Any, where: str) -> Any:
    """A copy of a schema with every ``required`` array of every schema object sorted.

    ``required`` is a set, so its order carries no meaning and must not depend on the order of
    the fields in a class. A repeated name is a defect of the model and raises ``ValueError``
    (silently dropping it would be a hidden transform). Data keywords are copied unchanged.
    """
    if isinstance(node, list):
        return [_sorted_required(item, where) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "required" and isinstance(value, list):
            if len(set(value)) != len(value):
                raise ValueError(f"{where}: a 'required' array names a property twice: {value}")
            out[key] = sorted(value)
        elif key in _DATA_KEYWORDS:
            out[key] = value
        elif key in _SCHEMA_MAPS and isinstance(value, dict):
            out[key] = {name: _sorted_required(sub, where) for name, sub in value.items()}
        else:
            out[key] = _sorted_required(value, where)
    return out


def _entity_document(model: Any) -> dict[str, Any]:
    name: str = model.__name__
    identifier = schema_id(name)
    schema: dict[str, Any] = model.model_json_schema(mode="validation")
    if schema.get("title") != name:
        raise ValueError(f"{name}: the schema title {schema.get('title')!r} is not the class name")
    for keyword in ("$schema", "$id"):
        if keyword in schema:
            raise ValueError(f"{name}: the model schema already sets {keyword}")
    document = {"$schema": SCHEMA_DIALECT, "$id": identifier, **schema}
    sorted_document: dict[str, Any] = _sorted_required(document, name)
    return sorted_document


def render_schemas() -> dict[str, bytes]:
    """File name -> bytes of every entity schema, sorted by file name.

    Pure: no file is read or written. The entity set is read from ``registry.entity_classes()``
    on every call, so a new entity gets a file without a change here.
    """
    files: dict[str, bytes] = {}
    for model in registry.entity_classes().values():
        file_name = model.__name__ + SCHEMA_SUFFIX
        if file_name in files:
            raise ValueError(f"two registered entities are named {model.__name__}")
        files[file_name] = _canonical(_entity_document(model))
    folded = [name.lower() for name in files]
    if len(set(folded)) != len(folded):
        raise ValueError("two entity file names differ only in case")
    return dict(sorted(files.items()))


def _require_schema_file_name(name: str) -> None:
    if not isinstance(name, str) or _SCHEMA_FILE.fullmatch(name) is None:
        raise ValueError(f"not a schema file name (<ClassName>{SCHEMA_SUFFIX}): {name!r}")


def render_lock(files: Mapping[str, bytes]) -> bytes:
    """The bytes of ``schema.lock`` for ``files`` (file name -> bytes).

    Canonical JSON of ``algorithm``, ``files`` (name -> lowercase hex SHA-256, sorted by name)
    and ``schema_version``; nothing machine- or time-dependent, independent of the order of
    ``files``. A name that is not ``<ClassName>.schema.json`` raises ``ValueError``.
    """
    for name in files:
        _require_schema_file_name(name)
    return _canonical(
        {
            "algorithm": LOCK_ALGORITHM,
            "files": {name: sha256_hex(files[name]) for name in sorted(files)},
            "schema_version": SCHEMA_VERSION,
        }
    )


# --------------------------------------------------------------------------------- writing


def _write_if_changed(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` through a temporary file in the same directory and an atomic
    rename; a file that already holds exactly ``data`` is left untouched."""
    try:
        if path.is_file() and not path.is_symlink() and path.read_bytes() == data:
            return
    except OSError:
        pass
    handle, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def export_json_schema(directory: str | os.PathLike[str]) -> list[str]:
    """Write every schema file and the lock into ``directory`` and return the sorted names.

    The directory is created (parents too). Existing files are overwritten, a ``*.schema.json``
    that is no longer generated (an entity that was removed) is deleted, and any other file is
    left alone (``check_schema_directory`` still reports it). A second run changes no byte.
    """
    target = Path(directory)
    files = render_schemas()
    lock = render_lock(files)
    target.mkdir(parents=True, exist_ok=True)
    for entry in sorted(target.iterdir()):
        if entry.name.endswith(SCHEMA_SUFFIX) and entry.name not in files:
            if entry.is_dir() and not entry.is_symlink():
                continue
            entry.unlink()
    for name, data in files.items():
        _write_if_changed(target / name, data)
    _write_if_changed(target / LOCK_NAME, lock)
    return sorted([*files, LOCK_NAME])


# -------------------------------------------------------------------------------- checking


def _lock_problems(data: bytes, version: str) -> list[str]:
    """Why ``data`` is not a well-formed lock of ``version`` (empty when it is one).

    A lock is canonical JSON of exactly ``algorithm`` (``sha256``), ``files`` (schema file name
    -> 64 lowercase hex digits) and ``schema_version`` (``version``)."""
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ["is not JSON"]
    if not isinstance(parsed, dict):
        return ["is not a JSON object"]
    problems: list[str] = []
    if set(parsed) != _LOCK_KEYS:
        problems.append(f"has the members {sorted(parsed)}, not {sorted(_LOCK_KEYS)}")
    if parsed.get("algorithm") != LOCK_ALGORITHM:
        problems.append(f"names the algorithm {parsed.get('algorithm')!r}, not {LOCK_ALGORITHM!r}")
    if parsed.get("schema_version") != version:
        problems.append(f"records version {parsed.get('schema_version')!r}, not {version!r}")
    entries = parsed.get("files")
    if not isinstance(entries, dict):
        problems.append("has no 'files' object")
    else:
        for name, value in sorted(entries.items()):
            if _SCHEMA_FILE.fullmatch(name) is None:
                problems.append(f"lists {name!r}, which is not a schema file name")
            elif not isinstance(value, str) or _HEX64.fullmatch(value) is None:
                problems.append(f"has no 64-digit lowercase hex SHA-256 for {name}")
    if not problems and _canonical(parsed) != data:
        problems.append("is not in canonical form (sorted keys, two-space indent, ASCII, one LF)")
    return problems


def _lock_differences(on_disk: bytes, expected: bytes) -> Iterator[str]:
    """``[lock]`` lines for a lock that is not the generated one."""
    problems = _lock_problems(on_disk, SCHEMA_VERSION)
    if problems:
        yield from (f"[lock] {LOCK_NAME} {problem}" for problem in problems)
        return
    have: dict[str, str] = json.loads(on_disk)["files"]
    want: dict[str, str] = json.loads(expected)["files"]
    for name in sorted(set(have) | set(want)):
        if name not in have:
            yield f"[lock] {LOCK_NAME} has no entry for {name}"
        elif name not in want:
            yield f"[lock] {LOCK_NAME} lists {name}, which is not generated"
        elif have[name] != want[name]:
            yield f"[lock] {LOCK_NAME} records another hash for {name}"


def _read_directory(target: Path) -> tuple[dict[str, bytes], list[str]]:
    """(regular files: name -> bytes, names of other entries), or empty when absent."""
    if not target.is_dir():
        return {}, []
    files: dict[str, bytes] = {}
    others: list[str] = []
    for entry in sorted(target.iterdir()):
        if entry.is_file() and not entry.is_symlink():
            files[entry.name] = entry.read_bytes()
        else:
            others.append(entry.name)
    return files, others


def check_schema_directory(
    directory: str | os.PathLike[str], *, released_locks: Mapping[str, bytes] | None = None
) -> list[str]:
    """Problems of ``directory`` against the generator's output; empty when there is none.

    One line per problem, tagged, naming files by their name only (never a path):

    - ``[changed] <name>``: the bytes differ from the generated ones (the generator, not the
      lock, is the reference: a file edited together with its lock entry is still changed);
    - ``[missing] <name>``: a generated file is absent (a missing directory lists them all);
    - ``[extra] <name>``: anything else in the directory but the lock (a file that is not
      generated, a subdirectory, a symbolic link);
    - ``[lock] ...``: the lock is missing, not a lock, not canonical, or not the generated one;
    - ``[released] ...``: ``released_locks`` (version -> the lock recorded at that release)
      holds the current version and the directory's lock differs from it (a schema changed
      without a version bump), or a recorded lock is not a valid lock of its version.
    """
    target = Path(directory)
    files = render_schemas()
    expected_lock = render_lock(files)
    on_disk, others = _read_directory(target)
    problems: list[str] = []
    for name, data in files.items():
        if name not in on_disk:
            problems.append(f"[missing] {name}")
        elif on_disk[name] != data:
            problems.append(f"[changed] {name}")
    extra = {name for name in on_disk if name not in files and name != LOCK_NAME}
    problems.extend(f"[extra] {name}" for name in sorted(extra | set(others)))
    lock = on_disk.get(LOCK_NAME)
    if lock is None:
        problems.append(f"[lock] {LOCK_NAME} is missing")
    elif lock != expected_lock:
        problems.extend(_lock_differences(lock, expected_lock))
    for version in sorted(released_locks or {}):
        recorded = (released_locks or {})[version]
        invalid = _lock_problems(recorded, version)
        if invalid:
            problems.extend(
                f"[released] the lock recorded for version {version} {problem}"
                for problem in invalid
            )
        elif version == SCHEMA_VERSION and lock != recorded:
            problems.append(
                f"[released] version {version} is released and its schemas changed: bump "
                "SCHEMA_VERSION (and the package version) before regenerating"
            )
    return problems


# ---------------------------------------------------------------------------- command line


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tracer_evalschema.schema_export",
        description="Regenerate (--write) or verify (--check) the JSON Schema files and "
        "schema.lock of tracer-evalschema.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the files and the lock")
    mode.add_argument(
        "--check", action="store_true", help="report drift on stderr and exit 1 when there is any"
    )
    parser.add_argument(
        "--dir",
        metavar="DIR",
        help="the schema directory (default: the package's own schemas/ directory)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Command line: ``--write`` or ``--check``, optionally ``--dir DIR``.

    Exit codes: 0 done or no drift, 1 drift (one problem line each on stderr) or an I/O error,
    2 usage error (argparse prints the usage line).
    """
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # usage errors (2) and --help (0) return instead of exiting
        return exc.code if isinstance(exc.code, int) else 2
    directory = Path(args.dir) if args.dir is not None else schema_directory()
    try:
        if args.write:
            written = export_json_schema(directory)
            print(f"wrote {len(written) - 1} schema files and {LOCK_NAME}")
            return 0
        problems = check_schema_directory(directory)
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        print(f"error: cannot access the schema directory: {reason}", file=sys.stderr)
        return 1
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
