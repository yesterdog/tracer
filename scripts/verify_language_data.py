# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Compare the vendored language data with the upstream sources (uses the network).

Not part of the default test run or CI. Run it by hand before a release or when the data files
change:

    uv run python scripts/verify_language_data.py

It downloads the IANA language subtag registry, the SIX ISO 4217 List One XML and Unicode 18.0.0
Blocks.txt and extracted/DerivedNumericType.txt into a fresh temporary directory, treats them as
untrusted data (size cap, HTTPS only, no XML DTDs or entities), and compares them with the three
JSON files in packages/evalschema/src/tracer_evalschema/data/ (including the IANA-derived fields
of languages.json: language subtag, default_script vs Suppress-Script, english_name vs
Description). Exit 0 when everything matches, 1 on any mismatch, 2 when a download or parse fails.

Documented allowance: the SIX file stores the KMF currency name with a trailing space
("Comorian Franc "); the vendored file stores it stripped. Only that one code and only that
difference is accepted, and it is reported as a note.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / "packages" / "evalschema" / "src" / "tracer_evalschema" / "data"

UNICODE_VERSION = "18.0.0"
SOURCES = {
    "iso4217.xml": (
        "https://www.six-group.com/dam/download/financial-information/data-center/"
        "iso-currrency/lists/list-one.xml"
    ),
    "iana.txt": "https://www.iana.org/assignments/language-subtag-registry/language-subtag-registry",
    "blocks.txt": f"https://www.unicode.org/Public/{UNICODE_VERSION}/ucd/Blocks.txt",
    "derived_numeric_type.txt": (
        f"https://www.unicode.org/Public/{UNICODE_VERSION}/ucd/extracted/DerivedNumericType.txt"
    ),
}

# code -> the only accepted difference between source name and vendored name.
TRAILING_SPACE_ALLOWANCE = {"KMF"}

MAX_DOWNLOAD_BYTES = 20_000_000
TIMEOUT_SECONDS = 60


class Report:
    def __init__(self) -> None:
        self.mismatches: list[str] = []
        self.notes: list[str] = []

    def fail(self, msg: str) -> None:
        self.mismatches.append(msg)
        print(f"MISMATCH: {msg}")

    def note(self, msg: str) -> None:
        self.notes.append(msg)
        print(f"note: {msg}")


def download(url: str, dest: Path) -> None:
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS url: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "tracer-verify-language-data"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
        data = resp.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"{url} exceeds {MAX_DOWNLOAD_BYTES} bytes")
    dest.write_bytes(data)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def compare_iso4217(xml_bytes: bytes, vendored: dict[str, Any], rep: Report) -> None:
    head = xml_bytes[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in xml_bytes.lower():
        raise ValueError("ISO 4217 XML contains a DTD or entity declaration; refusing to parse")
    root = ET.fromstring(xml_bytes)  # noqa: S314
    src: dict[str, dict[str, Any]] = {}
    for e in root.iter("CcyNtry"):
        code = e.findtext("Ccy")
        if not code:
            continue  # entries without a currency (Antarctica, ...) are not vendored
        nm = e.find("CcyNm")
        if nm is None or nm.text is None:
            rep.fail(f"source entry {code} has no CcyNm")
            continue
        row = {
            "numeric": e.findtext("CcyNbr"),
            "minor": e.findtext("CcyMnrUnts"),
            "name": nm.text,
            "fund": nm.get("IsFund") == "true",
        }
        if code in src:
            for k in ("numeric", "minor"):
                if src[code][k] != row[k]:
                    rep.fail(f"source is inconsistent for {code} {k}")
        else:
            src[code] = row
    rows: list[dict[str, Any]] = vendored["currencies"]
    mine = {r["code"]: r for r in rows}
    print(f"ISO 4217: source codes {len(src)}, vendored codes {len(mine)}, rows {len(rows)}")
    if len(rows) != len(mine):
        rep.fail("vendored ISO 4217 file has duplicate codes")
    for c in sorted(set(src) - set(mine)):
        rep.fail(f"ISO code missing from vendored file: {c}")
    for c in sorted(set(mine) - set(src)):
        rep.fail(f"ISO code not in source: {c}")
    for c in sorted(set(src) & set(mine)):
        s, m = src[c], mine[c]
        minor = None if s["minor"] in ("N.A.", None) else int(s["minor"])
        if m["numeric"] != s["numeric"]:
            rep.fail(f"{c} numeric {m['numeric']!r} != {s['numeric']!r}")
        if m["minor_units"] != minor:
            rep.fail(f"{c} minor_units {m['minor_units']!r} != {minor!r}")
        if bool(m["is_fund"]) != s["fund"]:
            rep.fail(f"{c} is_fund {m['is_fund']!r} != {s['fund']!r}")
        if bool(m["is_x_code"]) != c.startswith("X"):
            rep.fail(f"{c} is_x_code is wrong")
        if m["name"] != s["name"]:
            if c in TRAILING_SPACE_ALLOWANCE and s["name"] == m["name"] + " ":
                rep.note(
                    f"{c} name differs only by the known trailing space in the source (allowed)"
                )
            else:
                rep.fail(f"{c} name {m['name']!r} != {s['name']!r}")
    published = root.get("Pblshd")
    declared = vendored["_meta"]["sources"][0].get("published")
    print(f"ISO 4217: source Pblshd {published!r}, vendored published {declared!r}")
    if published != declared:
        rep.fail(f"ISO 4217 publication date {declared!r} != source {published!r}")


def parse_iana(text: str) -> tuple[str, dict[tuple[str, str], dict[str, list[str]]]]:
    first = text.split("\n", 1)[0]
    index: dict[tuple[str, str], dict[str, list[str]]] = {}
    for block in text.split("\n%%\n")[1:]:
        rec: dict[str, list[str]] = {}
        last: str | None = None
        for line in block.split("\n"):
            if line.startswith("  ") and last:
                rec[last][-1] += " " + line.strip()
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                last = key.strip()
                rec.setdefault(last, []).append(value.strip())
        if "Subtag" in rec and "Type" in rec:
            index[(rec["Type"][0], rec["Subtag"][0].lower())] = rec
    return first, index


def first_value(rec: dict[str, list[str]], key: str) -> str | None:
    values = rec.get(key)
    return values[0] if values else None


def compare_iana(
    text: str, vendored: dict[str, Any], rep: Report
) -> dict[tuple[str, str], dict[str, list[str]]]:
    first, index = parse_iana(text)
    print(f"IANA: vendored file_date {vendored['file_date']}, source first line {first!r}")
    if first != "File-Date: " + vendored["file_date"]:
        rep.fail(f"IANA File-Date differs: vendored {vendored['file_date']!r}, source {first!r}")
    known = {"type", "subtag", "description", "added", "suppress_script"}
    for r in vendored["records"]:
        key = (r["type"], r["subtag"].lower())
        s = index.get(key)
        if s is None:
            rep.fail(f"IANA record not in source: {key}")
            continue
        if r.get("description") != s.get("Description"):
            rep.fail(f"{key} description {r.get('description')!r} != {s.get('Description')!r}")
        if r.get("added") != first_value(s, "Added"):
            rep.fail(f"{key} added {r.get('added')!r} != {s.get('Added')!r}")
        suppress = first_value(s, "Suppress-Script")
        if r.get("suppress_script") != suppress:
            rep.fail(f"{key} suppress_script {r.get('suppress_script')!r} != {suppress!r}")
        extra = set(r) - known
        if extra:
            rep.note(f"{key} has vendored fields that are not compared: {sorted(extra)}")
    print(f"IANA: {len(vendored['records'])} vendored records checked")
    return index


def compare_languages_to_iana(
    languages: dict[str, Any],
    index: dict[tuple[str, str], dict[str, list[str]]],
    rep: Report,
) -> None:
    for lang in languages["languages"]:
        code = lang["language"]
        s = index.get(("language", code.lower()))
        if s is None:
            rep.fail(f"languages.json: language subtag {code!r} not in the IANA registry")
            continue
        suppress = first_value(s, "Suppress-Script")
        if suppress is None:
            rep.fail(
                f"languages.json: {code} has no IANA Suppress-Script to compare default_script"
            )
        elif lang["default_script"] != suppress:
            rep.fail(
                f"{code} default_script {lang['default_script']!r} "
                f"!= IANA Suppress-Script {suppress!r}"
            )
        if lang["english_name"] not in s.get("Description", []):
            rep.fail(
                f"{code} english_name {lang['english_name']!r} not in IANA Description "
                f"{s.get('Description')!r}"
            )
        elif first_value(s, "Description") != lang["english_name"]:
            rep.note(f"{code} english_name matches a non-primary IANA Description")
    print(f"IANA: {len(languages['languages'])} languages.json entries checked")


def parse_range(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"U\+([0-9A-F]+)\.\.U\+([0-9A-F]+)", text)
    if not m:
        raise ValueError(f"bad range {text!r}")
    return int(m.group(1), 16), int(m.group(2), 16)


def compare_unicode(
    blocks_text: str, dnt_text: str, languages: dict[str, Any], rep: Report
) -> None:
    if not blocks_text.startswith(f"# Blocks-{UNICODE_VERSION}.txt"):
        rep.fail(f"Blocks.txt is not version {UNICODE_VERSION}: {blocks_text.splitlines()[0]!r}")
    if f"DerivedNumericType-{UNICODE_VERSION}.txt" not in dnt_text.splitlines()[0]:
        rep.fail(f"DerivedNumericType.txt is not version {UNICODE_VERSION}")
    blocks: dict[str, tuple[int, int]] = {}
    for line in blocks_text.splitlines():
        m = re.match(r"([0-9A-F]+)\.\.([0-9A-F]+); (.+)", line.strip())
        if m:
            blocks[m.group(3)] = (int(m.group(1), 16), int(m.group(2), 16))
    decimals: list[tuple[int, int]] = []
    for line in dnt_text.splitlines():
        m = re.match(r"([0-9A-F]+)(?:\.\.([0-9A-F]+))?\s*;\s*Decimal", line)
        if m:
            decimals.append((int(m.group(1), 16), int(m.group(2) or m.group(1), 16)))
    for lang in languages["languages"]:
        code = lang["language"]
        nd = lang["native_digits"]
        if not nd["exists"]:
            rep.note(f"{code}: no native digit block claimed")
            continue
        block = blocks.get(nd["block"])
        if block is None:
            rep.fail(f"{code}: block {nd['block']!r} not in Blocks.txt")
            continue
        if parse_range(nd["block_range"]) != block:
            rep.fail(f"{code}: block_range {nd['block_range']} != {block}")
        lo, hi = parse_range(nd["digit_range"])
        run = [d for d in decimals if d[0] <= lo and d[1] >= hi]
        if not run or hi - lo != 9:
            rep.fail(f"{code}: digit_range {nd['digit_range']} is not a Decimal run of 10")
        elif not (block[0] <= lo and hi <= block[1]):
            rep.fail(f"{code}: digit_range {nd['digit_range']} is outside block {nd['block']}")
    print(f"Unicode {UNICODE_VERSION}: {len(languages['languages'])} languages checked")


def run(data_dir: Path, work_dir: Path) -> int:
    rep = Report()
    for name, url in SOURCES.items():
        print(f"downloading {url}")
        download(url, work_dir / name)
    compare_iso4217(
        (work_dir / "iso4217.xml").read_bytes(), load_json(data_dir / "iso4217_active.json"), rep
    )
    iana_text = (work_dir / "iana.txt").read_text(encoding="utf-8")
    index = compare_iana(iana_text, load_json(data_dir / "iana_subtag_registry_subset.json"), rep)
    languages = load_json(data_dir / "languages.json")
    compare_languages_to_iana(languages, index, rep)
    compare_unicode(
        (work_dir / "blocks.txt").read_text(encoding="utf-8"),
        (work_dir / "derived_numeric_type.txt").read_text(encoding="utf-8"),
        languages,
        rep,
    )
    print(f"TOTAL MISMATCHES: {len(rep.mismatches)} (notes: {len(rep.notes)})")
    return 1 if rep.mismatches else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    ap.add_argument("--keep", type=Path, help="download into this new, empty directory and keep it")
    args = ap.parse_args(argv)
    try:
        if args.keep:
            args.keep.mkdir(parents=True)
            return run(args.data_dir, args.keep)
        with tempfile.TemporaryDirectory(prefix="tracer-langdata-") as tmp:
            return run(args.data_dir, Path(tmp))
    except (OSError, ValueError, ET.ParseError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
