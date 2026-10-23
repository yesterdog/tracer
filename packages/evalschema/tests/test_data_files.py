# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-03 / P1-03b: the vendored data files (languages, IANA subset, ISO 4217, supported currencies).

File-level contract tests only; the types built from these files are tested in test_language.py
and test_primitives.py. This module carries the negative language parameters, so it belongs on the
T-DRIFT allowlist next to test_language.py (ADR 0005 Decision 10).
"""

from __future__ import annotations

import datetime as dt
import importlib
import re
import subprocess
import sys
from typing import Any

import pytest

from ._support import DATA_DIR, ROOT, data_json

NINE = {"en", "hi", "mr", "te", "ta", "kn", "ml", "pa", "gu"}
DATA_FILES = [
    "languages.json",
    "iana_subtag_registry_subset.json",
    "iso4217_active.json",
    "supported_currencies.json",
]
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _languages() -> list[dict[str, Any]]:
    rows = data_json("languages.json")["languages"]
    assert isinstance(rows, list)
    return rows


def _iana() -> dict[str, Any]:
    data = data_json("iana_subtag_registry_subset.json")
    assert isinstance(data, dict)
    return data


def _subtags(kind: str) -> set[str]:
    return {r["subtag"] for r in _iana()["records"] if r["type"] == kind}


def test_data_directory_ships_only_known_files() -> None:
    """Guards against a stray data file; the loader and T-DRIFT read this directory. The four
    vendored files must be there; the only other file allowed is the display marker table
    (bidi_markers.json, P1-04d)."""
    present = {p.name for p in DATA_DIR.glob("*.json")}
    assert set(DATA_FILES) <= present
    assert present - set(DATA_FILES) <= {"bidi_markers.json"}


def test_languages_exactly_nine() -> None:
    codes = [row["language"] for row in _languages()]
    assert len(codes) == 9
    assert len(set(codes)) == 9
    assert set(codes) == NINE


@pytest.mark.parametrize("tag", ["bn", "ur", "or", "as", "pa-Arab"])
def test_excluded_languages_absent(tag: str) -> None:
    primary, _, script = tag.partition("-")
    rows = _languages()
    blob = (DATA_DIR / "languages.json").read_text(encoding="utf-8")
    assert f'"{tag}"' not in blob
    if script:
        # Shahmukhi: the language itself is supported, but only in its native Gurmukhi script
        assert primary in NINE
        row = next(r for r in rows if r["language"] == primary)
        assert script not in row["allowed_scripts"]
        assert row["allowed_scripts"] == ["Guru"]
        assert script not in _subtags("script")
        assert all(script not in r["allowed_scripts"] for r in rows)
    else:
        assert primary not in {r["language"] for r in rows}
        assert primary not in _subtags("language"), (
            "the vendored subset carries an excluded language"
        )


def test_scripts_in_vendored_iso15924() -> None:
    scripts = _subtags("script")
    assert scripts, "the vendored registry subset has no script records"
    for row in _languages():
        code = row["language"]
        assert row["default_script"] in row["allowed_scripts"], code
        for script in row["allowed_scripts"]:
            assert re.fullmatch(r"[A-Z][a-z]{3}", script), (
                f"{code}: {script} is not a 4-letter code"
            )
            assert script in scripts, f"{code}: script {script} missing from the vendored subset"
    # native script only, one script per language
    assert all(len(row["allowed_scripts"]) == 1 for row in _languages())
    suppress = {
        r["subtag"]: r.get("suppress_script") for r in _iana()["records"] if r["type"] == "language"
    }
    for row in _languages():
        assert suppress[row["language"]] == row["default_script"], row["language"]


def test_vendored_registry_has_file_date() -> None:
    data = _iana()
    file_date = data["file_date"]
    assert ISO_DATE.match(file_date), file_date
    dt.date.fromisoformat(file_date)
    dated = [s["registry_file_date"] for s in data["_meta"]["sources"] if "registry_file_date" in s]
    assert dated == [file_date], "_meta.sources must state the same registry File-Date"


def test_vendored_data_has_verification_note() -> None:
    """Every vendored file, including supported_currencies.json, carries sources, a retrieval date
    and a verification note in ``_meta`` (SCOPE P1-03, P1-03b)."""
    problems: list[str] = []
    for name in DATA_FILES:
        meta = data_json(name).get("_meta")
        if not isinstance(meta, dict):
            problems.append(f"{name}: no _meta object")
            continue
        verification = meta.get("verification")
        if not (isinstance(verification, str) and len(verification.strip()) >= 20):
            problems.append(f"{name}: _meta.verification missing or too short")
        retrieved = meta.get("retrieved_at")
        if not (isinstance(retrieved, str) and ISO_DATE.match(retrieved)):
            problems.append(f"{name}: _meta.retrieved_at missing or not YYYY-MM-DD")
        sources = meta.get("sources")
        if not (isinstance(sources, list) and sources):
            problems.append(f"{name}: _meta.sources missing or empty")
    assert not problems, "; ".join(problems)


def test_languages_native_names_need_native_review() -> None:
    for row in _languages():
        code = row["language"]
        assert isinstance(row["native_name"], str), code
        assert row["native_name"].strip(), code
        assert isinstance(row["english_name"], str), code
        assert row["english_name"].strip(), code
        assert row["verified"]["native_name"] == "needs_native_review", code
    meta = data_json("languages.json")["_meta"]
    assert meta["no_native_speaker_verification"] is True


def test_iso4217_data_keeps_all_list_one_rows() -> None:
    rows = data_json("iso4217_active.json")["currencies"]
    assert len(rows) == 178
    codes = [r["code"] for r in rows]
    assert len(set(codes)) == 178
    for r in rows:
        assert set(r) == {"code", "numeric", "minor_units", "name", "is_fund", "is_x_code"}, r
        assert re.fullmatch(r"[A-Z]{3}", r["code"]), r
        assert re.fullmatch(r"\d{3}", r["numeric"]), r
        assert r["minor_units"] is None or isinstance(r["minor_units"], int)
        assert isinstance(r["is_fund"], bool)
        assert isinstance(r["is_x_code"], bool)
        assert r["name"], r
        assert r["name"] == r["name"].strip(), r
    by_code = {r["code"]: r for r in rows}
    # fund rows and X-code rows are kept with the source flags (ADR 0014 Decision 3)
    for fund in ("BOV", "CLF"):
        assert by_code[fund]["is_fund"] is True, fund
    for x_code in ("XXX", "XAU"):
        assert by_code[x_code]["is_x_code"] is True, x_code
    assert by_code["USD"]["is_fund"] is False
    assert by_code["USD"]["is_x_code"] is False
    assert by_code["USD"]["numeric"] == "840"
    assert by_code["USD"]["minor_units"] == 2
    assert {c for c, r in by_code.items() if r["is_x_code"]} == {
        c for c in by_code if c.startswith("X")
    }
    assert by_code["INR"]["name"] == "Indian Rupee"


def test_vendored_regions_exactly_in_us_gb() -> None:
    """P-14: the vendored region subtags, and so the configured regions, are exactly IN, US, GB."""
    assert _subtags("region") == {"IN", "US", "GB"}


def test_supported_currencies_file_is_exactly_usd() -> None:
    """Proposed name. File-level half of test_supported_currency_set_is_data_driven: the shipped
    content is exactly ["USD"] under the ``currencies`` key (ADR 0003, ADR 0014 Decision 2)."""
    data = data_json("supported_currencies.json")
    assert set(data) == {"_meta", "currencies"}
    assert data["currencies"] == ["USD"]


def test_supported_currencies_exist_in_iso4217() -> None:
    supported = data_json("supported_currencies.json")["currencies"]
    iso = {r["code"] for r in data_json("iso4217_active.json")["currencies"]}
    assert supported, "the supported set is empty"
    assert len(set(supported)) == len(supported)
    missing = [c for c in supported if c not in iso]
    assert not missing, f"supported currencies absent from ISO 4217 List One: {missing}"
    assert all(re.fullmatch(r"[A-Z]{3}", c) for c in supported)


def test_language_data_scripts_run_offline_checks() -> None:
    """Proposed name. scripts/verify_language_data.py is importable and its offline parts work
    (the network comparison itself is not part of the test run, per its docstring)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        module = importlib.import_module("verify_language_data")
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    assert callable(module.main)
    text = (
        "File-Date: 2026-09-17\n%%\nType: language\nSubtag: hi\nDescription: Hindi\n"
        "Added: 2005-10-16\nSuppress-Script: Deva\n"
    )
    file_date, records = module.parse_iana(text)
    assert file_date.endswith("2026-09-17")
    assert ("language", "hi") in records
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "verify_language_data.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "--data-dir" in proc.stdout
