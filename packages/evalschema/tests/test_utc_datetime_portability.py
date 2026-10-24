# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 1 gate finding B1: ``UtcDatetime`` serializes the same bytes on every platform.

The bug (found on hosted Linux CI, hidden on macOS): ``primitives._format_instant`` used
``strftime("%Y-...")``. glibc does not zero-pad ``%Y`` for years below 1000, so it wrote
``1-01-01T...`` where macOS writes ``0001-01-01T...``; the output then failed
``UTC_DATETIME_PATTERN`` and the round trip. Fixed by explicit ``f"{year:04d}"`` formatting.

These tests pin the behaviour on any platform, so they pass on macOS today and must keep passing on
glibc, musl and Windows. They are given teeth in two ways: the expected strings are written by hand
(never computed with ``strftime`` or ``isoformat``), and a source scan fails if any module of the
package calls the locale and platform dependent ``strftime`` or ``strptime``. The red proof (a
scratch copy whose formatter writes the year unpadded, as glibc does) is in
``docs/phase1/evidence/red/batch28-gate/``.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, create_model

from ._support import PACKAGE_DIR, ROOT, api

SRC = PACKAGE_DIR / "src" / "tracer_evalschema"

# year -> the exact text of 01 January of that year at midnight UTC, written by hand.
YEAR_TEXT: dict[int, str] = {
    1: "0001-01-01T00:00:00.000000Z",
    9: "0009-01-01T00:00:00.000000Z",
    99: "0099-01-01T00:00:00.000000Z",
    999: "0999-01-01T00:00:00.000000Z",
    1000: "1000-01-01T00:00:00.000000Z",
    9999: "9999-01-01T00:00:00.000000Z",
}
YEARS = tuple(YEAR_TEXT)


def _model() -> type[BaseModel]:
    instant: Any = api("primitives").UtcDatetime
    return create_model("Stamped", at=(instant, ...))


def _adapter() -> TypeAdapter[Any]:
    instant: Any = api("primitives").UtcDatetime
    return TypeAdapter(instant)


def _pattern() -> str:
    assert _adapter().json_schema(mode="validation")["type"] == "string"
    pattern = _adapter().json_schema(mode="serialization")["pattern"]
    assert isinstance(pattern, str)
    return pattern


@pytest.mark.parametrize("year", YEARS)
def test_model_dump_json_mode_is_zero_padded(year: int) -> None:
    model = _model()
    value = model(at=datetime(year, 1, 1, tzinfo=UTC))
    assert value.model_dump(mode="json") == {"at": YEAR_TEXT[year]}


@pytest.mark.parametrize("year", YEARS)
def test_model_dump_json_text_is_zero_padded(year: int) -> None:
    model = _model()
    value = model(at=datetime(year, 1, 1, tzinfo=UTC))
    text = value.model_dump_json()
    assert text == '{"at":"' + YEAR_TEXT[year] + '"}'
    assert json.loads(text) == {"at": YEAR_TEXT[year]}


@pytest.mark.parametrize("year", YEARS)
def test_type_adapter_json_is_zero_padded(year: int) -> None:
    adapter = _adapter()
    instant = datetime(year, 1, 1, tzinfo=UTC)
    assert adapter.dump_python(instant, mode="json") == YEAR_TEXT[year]
    assert adapter.dump_json(instant) == b'"' + YEAR_TEXT[year].encode("ascii") + b'"'


@pytest.mark.parametrize("year", YEARS)
def test_serialized_text_matches_the_json_schema_pattern(year: int) -> None:
    pattern = _pattern()
    text = _model()(at=datetime(year, 1, 1, tzinfo=UTC)).model_dump(mode="json")["at"]
    assert re.fullmatch(pattern, text), text
    assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z", text)
    assert len(text) == len("0001-01-01T00:00:00.000000Z")


@pytest.mark.parametrize("year", YEARS)
def test_round_trip_is_equal(year: int) -> None:
    model = _model()
    original = model(at=datetime(year, 1, 1, tzinfo=UTC))
    again = model.model_validate_json(original.model_dump_json())
    assert again == original
    assert again.at == datetime(year, 1, 1, tzinfo=UTC)  # type: ignore[attr-defined]
    from_python = model.model_validate(original.model_dump(mode="json"))
    assert from_python == original
    # and the text round trips to the same bytes
    assert again.model_dump_json() == original.model_dump_json()


@pytest.mark.parametrize("year", YEARS)
def test_parsing_the_padded_text_gives_the_instant(year: int) -> None:
    parsed = _adapter().validate_python(YEAR_TEXT[year])
    assert parsed == datetime(year, 1, 1, tzinfo=UTC)
    assert parsed.utcoffset() == timedelta(0)


SHORT_YEARS = (1, 9, 99, 999)


@pytest.mark.parametrize("year", SHORT_YEARS)
def test_unpadded_year_text_is_rejected(year: int) -> None:
    """The text a glibc ``strftime("%Y")`` writes is not accepted back (it never parses)."""
    unpadded = f"{year}-01-01T00:00:00.000000Z"
    assert unpadded != YEAR_TEXT[year]
    assert not re.fullmatch(_pattern(), unpadded)
    with pytest.raises(ValueError, match="UTC instant"):
        _adapter().validate_python(unpadded)


def test_the_short_years_are_exactly_those_with_fewer_than_four_digits() -> None:
    assert [year for year in YEARS if year < 1000] == list(SHORT_YEARS)


@pytest.mark.parametrize("year", YEARS)
def test_all_fields_are_padded_not_only_the_year(year: int) -> None:
    """Single-digit month, day, hour, minute, second and microsecond keep their zeros."""
    instant = datetime(year, 2, 3, 4, 5, 6, 7, tzinfo=UTC)
    expected = f"{YEAR_TEXT[year][:4]}-02-03T04:05:06.000007Z"
    text = _model()(at=instant).model_dump(mode="json")["at"]
    assert text == expected
    assert re.fullmatch(_pattern(), text)
    assert _adapter().validate_python(text) == instant


@pytest.mark.parametrize("year", [99, 999, 1000, 9998])
def test_offset_input_is_converted_and_the_year_stays_padded(year: int) -> None:
    local = datetime(year, 6, 15, 12, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    text = _model()(at=local).model_dump(mode="json")["at"]
    assert text == f"{year:04d}-06-15T06:30:00.000000Z"
    assert re.fullmatch(_pattern(), text)
    assert _adapter().validate_python(text) == local


def test_last_instant_of_the_last_year_round_trips() -> None:
    instant = datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
    text = _model()(at=instant).model_dump(mode="json")["at"]
    assert text == "9999-12-31T23:59:59.999999Z"
    assert _adapter().validate_python(text) == instant


def test_the_entity_base_instant_is_the_serializer_under_test() -> None:
    """``EntityBase.created_at`` is annotated with this very ``UtcDatetime``: every record
    serializes its instants this way."""
    assert api("base").UtcDatetime is api("primitives").UtcDatetime
    metadata = api("base").EntityBase.model_fields["created_at"].metadata
    assert any(type(item).__name__ == "_UtcDatetimeRule" for item in metadata)


# ------------------------------------------------------------------------- the source scan


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


FORBIDDEN_NAMES = frozenset({"strftime", "strptime"})


def platform_dependent_uses(source: str) -> list[tuple[int, str]]:
    """Every use of ``strftime`` or ``strptime`` in code, found by the AST (a name, an attribute,
    an import alias or a string passed to ``getattr``); comments and docstrings are not code."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            hits.append((node.lineno, node.attr))
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            hits.append((node.lineno, node.id))
        elif isinstance(node, ast.ImportFrom):
            hits.extend((node.lineno, a.name) for a in node.names if a.name in FORBIDDEN_NAMES)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in FORBIDDEN_NAMES
        ):
            hits.append((node.lineno, node.value))
    return sorted(hits)


def _wider_files() -> list[Path]:
    """The test-side code and scripts that format or parse dates: the converters under
    ``tests/support/mappings``, the ``_support_b*.py`` helpers, every ``test_*.py`` (this file
    excluded: it names the functions on purpose), and ``scripts/*.py``."""
    tests = PACKAGE_DIR / "tests"
    files: set[Path] = set((tests / "support" / "mappings").rglob("*.py"))
    files |= set(tests.glob("_support*.py"))
    files |= set(tests.glob("test_*.py"))
    files |= set((ROOT / "scripts").glob("*.py"))
    files.discard(Path(__file__).resolve())
    return sorted(files)


def test_the_wider_scan_finds_its_files() -> None:
    files = _wider_files()
    names = {path.name for path in files}
    assert "_support_b13.py" in names
    assert "test_primitives.py" in names
    assert any("mappings" in path.parts for path in files)
    assert any(path.parent.name == "scripts" for path in files)
    assert Path(__file__).resolve() not in files


def test_no_test_helper_converter_or_script_uses_strftime_or_strptime() -> None:
    """The same class of bug in code that is not shipped but builds fixtures and converts provider
    files: hosted Linux CI showed glibc year padding failures there too (gate finding B1). Dates
    are written with explicit zero-padded f-string fields."""
    found = {
        f"{path.relative_to(ROOT).as_posix()}:{line}: {name}"
        for path in _wider_files()
        for line, name in platform_dependent_uses(path.read_text(encoding="utf-8"))
    }
    assert not found, sorted(found)


def test_the_scan_finds_the_modules() -> None:
    names = {path.name for path in _modules()}
    assert "primitives.py" in names
    assert len(names) >= 30


def test_no_module_uses_strftime_or_strptime() -> None:
    """``strftime`` and ``strptime`` depend on the platform C library and the locale (the glibc
    year bug is one case; ``%b``, ``%p`` and ``%c`` follow the locale). Dates are formatted with
    explicit f-string fields and parsed with a fixed grammar."""
    found = {
        f"{path.relative_to(SRC).as_posix()}:{line}: {name}"
        for path in _modules()
        for line, name in platform_dependent_uses(path.read_text(encoding="utf-8"))
    }
    assert not found, sorted(found)


@pytest.mark.parametrize(
    "source",
    [
        "x = value.strftime('%Y')",
        "from time import strftime",
        "import datetime\ndatetime.datetime.strptime(a, b)",
        "f = datetime.strftime\nf(v, '%Y')",
        "getattr(v, 'strftime')('%Y')",
        "from time import strptime as p",
    ],
)
def test_the_scan_detects_each_form(source: str) -> None:
    assert platform_dependent_uses(source)


@pytest.mark.parametrize(
    "source",
    [
        "# strftime is platform dependent\nx = 1",
        '"""strftime in a docstring"""\nx = 1',
        'def f(v):\n    """About strftime and strptime."""\n    return f"{v.year:04d}"',
        "text = 'used strftime once'",
    ],
)
def test_the_scan_ignores_comments_docstrings_and_prose(source: str) -> None:
    assert platform_dependent_uses(source) == []
