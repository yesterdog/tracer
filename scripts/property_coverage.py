# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Report which schema properties and branches the valid golden fixtures cover (P1-16, gate 4).

For every registered entity the script takes the pydantic-generated JSON Schema (validation mode)
and lists its coverage points:

* ``prop``    a property of an object schema, at a path from the entity root;
* ``item``    the items of an array property (covered by a non-empty array);
* ``entries`` the values of a typed free-form object (covered by a non-empty object);
* ``branch``  one alternative of a union or enum (``anyOf``/``oneOf``/``enum``), labelled;
* ``bool``    each value of a boolean property.

A point is covered when some valid fixture of the entity (``golden/<Class>/valid/*.json``) holds a
non-null value there (for ``branch`` and ``bool``: the alternative that validates, or that value).
An optional property that every valid fixture leaves out or sets to null is a gap, and so is an
enum member or union alternative that no fixture uses. Coverage is 100% only without gaps.

Usage: ``uv run python scripts/property_coverage.py [--fixtures DIR] [--entity NAME] [--list]``.
Exit status 0 only at 100%. The module is also imported by ``test_golden_fixtures.py``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parent.parent
PACKAGE_SRC: Final = ROOT / "packages" / "evalschema" / "src"
DEFAULT_FIXTURES: Final = ROOT / "packages" / "evalschema" / "tests" / "fixtures" / "golden"

if str(PACKAGE_SRC) not in sys.path:  # allow running without the package installed
    sys.path.insert(0, str(PACKAGE_SRC))


class _Missing:
    """Marks 'no value here': the walk then only lists the points below."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING: Final = _Missing()

Point = tuple[str, str]  # (kind, location)

# Points no valid fixture may reach, by regular expression on the location, each with its reason.
# They are listed in every report; nothing else is excluded.
_OWN_DIGEST = (
    "an entity's own content_digest is verified on load and a keyed digest cannot be "
    "(DigestKeyUnavailable); keyed digests and key references occur in references"
)
EXCLUDED: Final[tuple[tuple[str, str], ...]] = (
    (r"^\w+\.content_digest\.key_ref", _OWN_DIGEST),
    (r'^\w+\.content_digest\.scheme="hmac-sha256-jcs/v1"$', _OWN_DIGEST),
    (
        r'\.plaintext_digest\.scheme="sha256-jcs/v1"$',
        "a sealed payload's plaintext_digest is absent or keyed (P-42): a plain digest of the "
        "plaintext is rejected",
    ),
    (
        r"\.metadata_flags\[\]",
        "a metadata flag exists only when a metadata string looks like a credential, and no "
        "fixture may hold a credential-shaped value (the flags are tested in test_entity_base.py)",
    ),
)


def excluded_reason(point: Point) -> str | None:
    for pattern, reason in EXCLUDED:
        if re.search(pattern, point[1]) is not None:
            return reason
    return None


@dataclass
class Coverage:
    """The points of one entity and the subset the fixtures hit."""

    entity: str
    points: set[Point] = field(default_factory=set)
    hit: set[Point] = field(default_factory=set)
    excluded: set[Point] = field(default_factory=set)

    @property
    def missing(self) -> list[Point]:
        return sorted(self.points - self.hit)

    @property
    def percent(self) -> float:
        return 100.0 if not self.points else 100.0 * len(self.hit) / len(self.points)


def _jsonschema() -> Any:
    return importlib.import_module("jsonschema")


class _Walker:
    def __init__(self, root: Mapping[str, Any], boundaries: frozenset[str] = frozenset()) -> None:
        self.root = root
        self.boundaries = boundaries
        self._validators: dict[str, Any] = {}

    # -- schema helpers
    def resolve(self, node: Any) -> Any:
        seen = 0
        while isinstance(node, dict) and seen < 50:
            seen += 1
            if "$ref" in node:
                target: Any = self.root
                for part in node["$ref"].removeprefix("#/").split("/"):
                    target = target[part]
                node = target
            elif "allOf" in node and len(node["allOf"]) == 1:
                rest = {k: v for k, v in node.items() if k != "allOf"}
                node = {**node["allOf"][0], **rest} if rest else node["allOf"][0]
            else:
                break
        return node

    def ref_name(self, node: Any) -> str | None:
        if isinstance(node, dict) and "$ref" in node:
            return str(node["$ref"]).rsplit("/", 1)[-1]
        return None

    def valid(self, alternative: Any, value: Any) -> bool:
        if not isinstance(alternative, dict):
            return False
        schema = {**alternative, "$defs": self.root.get("$defs", {})}
        key = json.dumps(schema, sort_keys=True)
        validator = self._validators.get(key)
        if validator is None:
            module = _jsonschema()
            validator = module.Draft202012Validator(schema)
            self._validators[key] = validator
        try:
            return bool(validator.is_valid(value))
        except Exception:
            return False

    def label(self, alternative: Any, index: int) -> str:
        name = self.ref_name(alternative)
        node = self.resolve(alternative)
        if name is not None:
            return name
        if isinstance(node, dict):
            if "const" in node:
                return f"const:{json.dumps(node['const'], ensure_ascii=False)}"
            if "enum" in node:
                return "enum:" + ",".join(json.dumps(m, ensure_ascii=False) for m in node["enum"])
            kind = node.get("type")
            if kind == "array":
                inner = self.label(node.get("items", {}), 0)
                return f"array<{inner}>"
            if isinstance(kind, str):
                extra = ""
                if "pattern" in node:
                    extra = f" /{node['pattern']}/"
                return f"{kind}{extra}"
        return f"alt{index}"

    # -- the walk
    def visit(
        self,
        node: Any,
        value: Any,
        path: str,
        points: set[Point],
        hit: set[Point],
        stack: tuple[str, ...] = (),
    ) -> None:
        name = self.ref_name(node)
        if name is not None:
            if name in stack:
                return
            if name in self.boundaries and stack:
                return  # an embedded entity: covered by its own fixtures
            stack = (*stack, name)
        node = self.resolve(node)
        if not isinstance(node, dict):
            return
        if value is None:
            value = MISSING
        alternatives = node.get("anyOf") or node.get("oneOf")
        if alternatives:
            non_null = [a for a in alternatives if self.resolve(a).get("type") != "null"]
            if len(non_null) == 1:
                self.visit(non_null[0], value, path, points, hit, stack)
                return
            matched = False
            for index, alternative in enumerate(non_null):
                label = self.label(alternative, index)
                point = ("branch", f"{path}|{label}")
                points.add(point)
                hits_here = False
                if value is not MISSING and not matched and self.valid(alternative, value):
                    matched = True
                    hits_here = True
                    hit.add(point)
                self.visit(
                    alternative,
                    value if hits_here else MISSING,
                    f"{path}|{label}",
                    points,
                    hit,
                    stack,
                )
            return
        if "enum" in node:
            members = node["enum"]
            if len(members) > 1:
                for member in members:
                    point = ("branch", f"{path}={json.dumps(member, ensure_ascii=False)}")
                    points.add(point)
                    if value is not MISSING and value == member and type(value) is type(member):
                        hit.add(point)
            return
        kind = node.get("type")
        if kind == "boolean":
            for flag in (True, False):
                point = ("bool", f"{path}={json.dumps(flag)}")
                points.add(point)
                if value is flag:
                    hit.add(point)
            return
        if kind == "object" or "properties" in node:
            properties = node.get("properties", {})
            as_dict = value if isinstance(value, dict) else {}
            for prop, sub in properties.items():
                point = ("prop", f"{path}.{prop}")
                points.add(point)
                present = prop in as_dict and as_dict[prop] is not None
                if present:
                    hit.add(point)
                self.visit(
                    sub, as_dict[prop] if present else MISSING, f"{path}.{prop}", points, hit, stack
                )
            extra = node.get("additionalProperties")
            if not properties and extra in (True, {}, None) and kind == "object":
                point = ("entries", f"{path}{{}}")
                points.add(point)
                if as_dict:
                    hit.add(point)
            if isinstance(extra, dict) and extra and not properties:
                point = ("entries", f"{path}{{}}")
                points.add(point)
                if as_dict:
                    hit.add(point)
                if as_dict:
                    for item in as_dict.values():
                        self.visit(extra, item, f"{path}{{}}", points, hit, stack)
                else:
                    self.visit(extra, MISSING, f"{path}{{}}", points, hit, stack)
            return
        if kind == "array" and isinstance(node.get("items"), dict):
            point = ("item", f"{path}[]")
            points.add(point)
            elements = value if isinstance(value, list) else []
            if elements:
                hit.add(point)
                for element in elements:
                    self.visit(node["items"], element, f"{path}[]", points, hit, stack)
            else:
                self.visit(node["items"], MISSING, f"{path}[]", points, hit, stack)


def entity_schema(model: Any) -> dict[str, Any]:
    schema: dict[str, Any] = model.model_json_schema(mode="validation")
    return schema


def coverage(model: Any, documents: Sequence[Any], *, deep: bool = False) -> Coverage:
    """Points of ``model`` and the points hit by ``documents`` (JSON-mode values).

    The walk stops at an embedded entity (a snapshot's evaluator, a bundle's turns): its own
    fixtures cover it. ``deep=True`` descends into embedded entities as well.
    """
    schema = entity_schema(model)
    boundaries = frozenset() if deep else frozenset(entity_models()) - {model.__name__}
    walker = _Walker(schema, boundaries)
    result = Coverage(entity=model.__name__)
    root = (
        {"$ref": f"#/$defs/{model.__name__}"}
        if "$ref" in schema
        else {k: v for k, v in schema.items() if k != "$defs"}
    )
    walker.visit(root, MISSING, model.__name__, result.points, set(), (model.__name__,))
    for document in documents:
        walker.visit(root, document, model.__name__, result.points, result.hit, (model.__name__,))
    result.excluded = {point for point in result.points if excluded_reason(point) is not None}
    result.points -= result.excluded
    result.hit &= result.points
    return result


def entity_models() -> dict[str, Any]:
    registry = importlib.import_module("tracer_evalschema.registry")
    return {cls.__name__: cls for cls in registry.entity_classes().values()}


def valid_documents(fixtures: Path, name: str) -> list[Any]:
    directory = fixtures / name / "valid"
    if not directory.is_dir():
        return []
    return [
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))
    ]


def report(fixtures: Path, only: str | None = None, *, deep: bool = False) -> list[Coverage]:
    models = entity_models()
    names = sorted(models) if only is None else [only]
    return [coverage(models[name], valid_documents(fixtures, name), deep=deep) for name in names]


def _lines(results: Sequence[Coverage], list_missing: bool) -> Iterator[str]:
    total = sum(len(r.points) for r in results)
    covered = sum(len(r.hit) for r in results)
    for result in results:
        yield (
            f"{result.entity:16s} {len(result.hit):5d}/{len(result.points):<5d} "
            f"{result.percent:6.2f}%  ({len(result.excluded)} excluded)"
        )
        if list_missing:
            for kind, where in result.missing:
                yield f"    missing {kind}: {where}"
    percent = 100.0 if total == 0 else 100.0 * covered / total
    for pattern, reason in EXCLUDED:
        yield f"excluded points matching {pattern}: {reason}"
    yield f"TOTAL            {covered:5d}/{total:<5d} {percent:6.2f}%"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--entity", default=None, help="report one entity only")
    parser.add_argument("--list", action="store_true", help="list every uncovered point")
    parser.add_argument("--deep", action="store_true", help="also descend into embedded entities")
    args = parser.parse_args(argv)
    results = report(args.fixtures, args.entity, deep=args.deep)
    for line in _lines(results, args.list):
        print(line)
    return 0 if all(not r.missing for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
