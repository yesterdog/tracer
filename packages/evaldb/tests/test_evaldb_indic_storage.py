# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Phase 2 batch E, P2-21: Indic text is stored byte for byte, on both backends.

Contract: docs/phase2/api-surface-batch-e.md section 5. The cases are the file
``fixtures/indic_storage.json`` written by the indic-linguistics agent (79 cases, every one
``needs_native_review``; they are storage fixtures, never WER references). The tests assert what a
database and a driver could get wrong: normalization (NFC or NFD), case folding, truncation at
U+0000, trimming, joiner removal, locale-dependent ordering, and canonical case of language tags.
No test here claims that a text is correct Indic; they only claim it comes back unchanged.
"""

from __future__ import annotations

import json
import unicodedata
from collections import defaultdict
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa

import evaldb_support as _support  # type: ignore[import-not-found,unused-ignore]
from evaldb_repo_support import (  # type: ignore[import-not-found,unused-ignore]
    load_run_bundle_doc,
    new_id,
    renew_ids,
    run_bundle_doc,
    sized_bundle_doc,
    store_bundle,
)
from evaldb_support import Db, golden_doc, load_entity, new_uuid, redigest  # type: ignore[import-not-found,unused-ignore]

db = _support.db
codecs = _support.codecs

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "indic_storage.json"
NINE = ("en", "hi", "mr", "te", "ta", "kn", "ml", "pa", "gu")
SCRIPT_OF = {
    "en": "Latn",
    "hi": "Deva",
    "mr": "Deva",
    "te": "Telu",
    "ta": "Taml",
    "kn": "Knda",
    "ml": "Mlym",
    "pa": "Guru",
    "gu": "Gujr",
}
# The cases the Project label rule (ADR 0017: a label has no control, no format-control mark other
# than the joiners, no edge white space) refuses. Pinned by id: a model change that starts to refuse
# an Indic joiner, or a fixture change, shows here.
NOT_A_LABEL = frozenset(
    {
        "en-nul-embedded",
        "en-nul-leading-trailing",
        "en-whitespace-edges",
        "en-line-breaks-mixed",
        "en-bom-leading",
        "und-whitespace-exotic",
        "und-bidi-fsi-pdi-metadata",
        "und-bidi-override-marks",
        "hi-whitespace-danda",
    }
)


def _cases() -> list[dict[str, Any]]:
    data = json.loads(FIXTURE.read_text("utf-8"))
    cases: list[dict[str, Any]] = data["cases"]
    return cases


CASES = _cases()
BY_ID = {c["id"]: c for c in CASES}
LABELS = [c for c in CASES if c["id"] not in NOT_A_LABEL]


def _equivalence_groups(cases: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Cases whose texts are canonically equivalent (same NFC) but differ in code points."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        groups[unicodedata.normalize("NFC", case["text"])].append(case)
    return [g for g in groups.values() if len({c["text"] for c in g}) > 1]


# ============================================================================== the fixture


def test_indic_storage_fixture_is_well_formed() -> None:
    raw = FIXTURE.read_bytes()
    assert raw.isascii()  # escapes only: no editor or tool can have normalized the text
    data = json.loads(raw)
    assert set(data) == {"cases"}
    ids = [c["id"] for c in CASES]
    assert len(set(ids)) == len(ids) == 79
    for case in CASES:
        assert set(case) == {"id", "language", "script", "text", "purpose", "needs_native_review"}
        assert case["needs_native_review"] is True  # nothing here is native-reviewed
        assert isinstance(case["text"], str)
        assert case["text"]
    assert set(NINE) <= {c["language"] for c in CASES}
    for language in NINE:
        scripts = {c["script"] for c in CASES if c["language"] == language}
        assert SCRIPT_OF[language] in scripts, language
    joined = "".join(c["text"] for c in CASES)
    assert "\u200d" in joined
    assert "\u200c" in joined  # ZWJ and ZWNJ
    assert sum("\x00" in c["text"] for c in CASES) == 2
    assert sum(len(c["text"]) == 2000 for c in CASES) == 2
    assert len(_equivalence_groups(CASES)) == 10
    assert set(BY_ID) >= NOT_A_LABEL


def test_indic_label_rule_refuses_exactly_the_pinned_cases() -> None:
    """The split between the 70 texts that can be a project name and the 9 that cannot is the
    model's (ADR 0017: no control, no bidi control, no line or paragraph separator, no BOM, no
    edge white space), not the database's: the nine are still stored, inside documents."""
    refused = set()
    for case in CASES:
        doc = golden_doc("Project", "minimal")
        doc["name"] = case["text"]
        try:
            load_entity("Project", json.dumps(doc))
        except ValueError:  # pydantic's ValidationError is a ValueError
            refused.add(case["id"])
    assert refused == NOT_A_LABEL, sorted(refused ^ NOT_A_LABEL)
    for case in CASES:
        if case["id"] in NOT_A_LABEL:
            text = case["text"]
            has_mark = any(
                unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") and ch not in "\u200c\u200d"
                for ch in text
            )
            assert has_mark or text != text.strip(), case["id"]


# ========================================================================= byte-exact round trip


def _project(case: dict[str, Any]) -> Any:
    doc = golden_doc("Project", "minimal")
    doc["id"] = new_id("prj")
    doc["name"] = case["text"]
    return load_entity("Project", json.dumps(doc))


def _scalar(db: Db, sql: str, **params: Any) -> Any:
    with db.engine.connect() as conn:
        found = conn.execute(sa.text(sql), params).all()
        conn.rollback()
    return found


def test_indic_text_round_trip_byte_exact(db: Db, evaldb: ModuleType, codecs: ModuleType) -> None:
    """Two placements. As a project name (a projected, indexed text column; the 70 cases a label
    can hold, the 2,000 code point ones included) the text comes back equal, with the same UTF-8
    bytes and the same length in code points as stored by the database itself. As a turn text
    (inside the document; all 79 cases, U+0000 included) it comes back equal from the repository
    and from the raw document."""
    # ---- projected column
    stored: dict[str, Any] = {}
    for case in LABELS:
        project = _project(case)
        evaldb.store_project(db.engine, project)
        stored[case["id"]] = project
    assert len(LABELS) == 70
    bad: list[str] = []
    for case in LABELS:
        project = stored[case["id"]]
        text = case["text"]
        loaded = evaldb.load_project(db.engine, project.id)
        row = _scalar(db, "SELECT name, doc, doc_sha256 FROM project WHERE name = :n", n=text)[0]
        if db.backend == "sqlite":
            hexed, size = _scalar(
                db,
                "SELECT hex(CAST(name AS BLOB)), length(name) FROM project WHERE name = :n",
                n=text,
            )[0]
        else:
            hexed, size = _scalar(
                db,
                "SELECT upper(encode(convert_to(name, 'UTF8'), 'hex')), char_length(name) "
                "FROM project WHERE name = :n",
                n=text,
            )[0]
        good = (
            loaded.name == text
            and loaded.name.encode("utf-8") == text.encode("utf-8")
            and row[0] == text
            and json.loads(row[1])["name"] == text
            and str(hexed).upper() == text.encode("utf-8").hex().upper()
            and size == len(text)
            and bytes(row[2]) == bytes(codecs.encode_project(project).row["doc_sha256"])
        )
        if not good:
            bad.append(case["id"])
    assert bad == [], bad
    # ---- inside the document
    doc = sized_bundle_doc(len(CASES))
    for turn, case in zip(doc["turns"], CASES, strict=True):
        turn["text"] = case["text"]
    bundle = load_run_bundle_doc(doc)
    assert [t.text for t in bundle.turns] == [c["text"] for c in CASES]
    store_bundle(db.engine, evaldb, bundle)
    loaded_bundle = evaldb.load_run_bundle(db.engine, bundle.id).bundle
    assert [t.text for t in loaded_bundle.turns] == [c["text"] for c in CASES]
    turn = db.metadata.tables["turn"]
    with db.engine.connect() as conn:
        rows = conn.execute(
            sa.select(turn.c.seq, turn.c.doc)
            .where(turn.c.run_id == bundle.run.id.uuid)
            .order_by(turn.c.seq)
        ).all()
        conn.rollback()
    assert [json.loads(doc_text)["text"] for _seq, doc_text in rows] == [c["text"] for c in CASES]
    assert [c["id"] for c in CASES if "\x00" in c["text"]]  # the NUL cases are in the comparison


# ==================================================================== no normalization anywhere


def test_indic_text_no_normalization_by_driver(db: Db, evaldb: ModuleType) -> None:
    """The driver and the database do not normalize, fold, trim or truncate: (1) a bound text
    echoed by the server is unchanged; (2) texts that are canonically equivalent, case twins,
    Kelvin and Angstrom signs, dotless i, sharp s and ZWJ/ZWNJ spellings are distinct values for
    equality, DISTINCT and lookup; (3) a lookup by one spelling finds exactly the rows stored under
    that spelling."""
    echoed = [c for c in CASES if "\x00" not in c["text"]]  # U+0000 cannot be a Postgres text
    changed = []
    with db.engine.connect() as conn:
        for case in echoed:
            got = conn.execute(sa.text("SELECT CAST(:x AS TEXT)"), {"x": case["text"]}).scalar_one()
            if got != case["text"]:
                changed.append(case["id"])
        conn.rollback()
    assert changed == [], changed
    for case in LABELS:
        evaldb.store_project(db.engine, _project(case))
    texts = [c["text"] for c in LABELS]
    assert len(set(texts)) == len(texts)  # the fixture has no duplicate text
    (distinct,) = _scalar(db, "SELECT count(DISTINCT name) FROM project")[0]
    assert distinct == len(texts)
    groups = _equivalence_groups(LABELS)
    assert len(groups) == 10
    for group in groups:
        for case in group:
            found = _scalar(db, "SELECT name FROM project WHERE name = :n", n=case["text"])
            assert [r[0] for r in found] == [case["text"]], case["id"]  # not its twins
    case_twins = [
        BY_ID[i]["text"] for i in ("en-id-mixed-case-a", "en-id-mixed-case-b", "en-id-mixed-case-c")
    ]
    for text in case_twins:
        assert len(_scalar(db, "SELECT name FROM project WHERE name = :n", n=text)) == 1
    for text in ("Ticket-ABC", "ticket-abc", "TICKET-ABC"):
        assert text in case_twins
    pairs = [
        ("en-id-kelvin-sign", "en-id-ascii-k"),
        ("en-angstrom-sign", "en-a-ring-above"),
        ("en-sharp-s", "en-sharp-s-twin"),
        ("hi-conjunct-plain", "hi-conjunct-zwj"),
        ("hi-conjunct-plain", "hi-conjunct-zwnj"),
        ("hi-conjunct-zwj", "hi-conjunct-zwnj"),
        ("te-conjunct-plain", "te-conjunct-zwnj"),
        ("gu-conjunct-plain", "gu-conjunct-zwnj"),
        ("ml-chillu-atomic-n", "ml-chillu-zwj-sequence-n"),
        ("ml-chillu-zwj-sequence-n", "ml-virama-zwnj"),
        ("hi-ascii-digits", "hi-native-digits"),
    ]
    for left, right in pairs:
        found = _scalar(
            db,
            "SELECT name FROM project WHERE name IN (:a, :b) ORDER BY name",
            a=BY_ID[left]["text"],
            b=BY_ID[right]["text"],
        )
        assert len(found) == 2, (left, right)
        for case_id in (left, right):
            one = _scalar(db, "SELECT name FROM project WHERE name = :n", n=BY_ID[case_id]["text"])
            assert len(one) == 1, case_id


def test_indic_text_ordering_is_never_language_sensitive(db: Db, evaldb: ModuleType) -> None:
    """ORDER BY on a text column is the order of the UTF-8 bytes (code point order) on both
    backends, whatever the collation of the database: no locale puts a Devanagari word after a
    Latin one differently, and no accent sorts next to its base letter."""
    for case in LABELS:
        evaldb.store_project(db.engine, _project(case))
    got = [r[0] for r in _scalar(db, "SELECT name FROM project ORDER BY name")]
    expected = sorted((c["text"] for c in LABELS), key=lambda s: s.encode("utf-8"))
    assert got == expected
    # a range test uses the same order
    low, high = BY_ID["hi-greeting"]["text"], BY_ID["gu-greeting"]["text"]
    inside = [
        r[0]
        for r in _scalar(
            db, "SELECT name FROM project WHERE name > :a AND name < :b", a=low, b=high
        )
    ]
    assert sorted(inside) == sorted(
        t
        for t in expected
        if t.encode("utf-8") > low.encode("utf-8") and t.encode("utf-8") < high.encode("utf-8")
    )


# ============================================================================== language tags


def _canonical(language: str, script: str | None, region: str | None) -> str:
    return "-".join(
        p
        for p in (language.lower(), script and script.capitalize(), region and region.upper())
        if p
    )


def _spellings(language: str, script: str | None, region: str | None) -> list[str]:
    """The same tag written in four letter cases."""
    parts = [p for p in (language, script, region) if p]
    return [
        "-".join(p.lower() for p in parts),
        "-".join(p.upper() for p in parts),
        "-".join(p.swapcase() if i else p.upper() for i, p in enumerate(parts)),
        _canonical(language, script, region),
    ]


def _tags() -> list[tuple[str, str]]:
    """(written, canonical) for the nine languages, with and without script and region."""
    found: list[tuple[str, str]] = []
    for language in NINE:
        region = "US" if language == "en" else "IN"
        for script, reg in ((None, None), (None, region), (SCRIPT_OF[language], region)):
            canonical = _canonical(language, script, reg)
            found.extend((written, canonical) for written in _spellings(language, script, reg))
    return found


TAGS = _tags()


def _persona(written: str, canonical: str) -> Any:
    doc = golden_doc("Persona", "minimal")
    doc["id"] = new_id("per")
    doc["language"] = canonical
    doc = redigest("Persona", doc)  # the content digest is over the canonical spelling
    doc["language"] = written
    return load_entity("Persona", json.dumps(doc))


def test_language_tags_stored_canonical_case(
    db: Db, evaldb: ModuleType, codecs: ModuleType
) -> None:
    """A language tag is stored exactly as the model returns it: language lower case, script title
    case, region upper case (ADR 0005 Decision 1). The projected column, the document and the model
    agree for every spelling of every tag of the nine languages; the database compares the column
    as bytes (a lookup by a differently cased spelling finds nothing); and it holds for the three
    projected language columns: persona.language, dataset_item.language and
    transcript.detected_language (observed tags included)."""
    # ---- persona.language and dataset_item.language: configured languages
    assert len(TAGS) == 108
    for written, canonical in TAGS:
        persona = _persona(written, canonical)
        assert persona.language == canonical, (written, str(persona.language))
        evaldb.store_definition(db.engine, persona)
        item_doc = golden_doc("DatasetItem", "minimal")
        item_doc["id"] = new_id("dti")
        item_doc["language"] = written
        item = load_entity("DatasetItem", json.dumps(item_doc))
        assert item.language == canonical
        evaldb.store_dataset_item(db.engine, item)
    for table in ("persona", "dataset_item"):
        rows = _scalar(db, f"SELECT language, doc FROM {table}")  # noqa: S608
        assert len(rows) == len(TAGS)
        assert sorted(r[0] for r in rows) == sorted(c for _w, c in TAGS)
        for column, doc_text in rows:
            assert json.loads(doc_text)["language"] == column
        for written, _canonical_form in TAGS:
            found = _scalar(db, f"SELECT count(*) FROM {table} WHERE language = :t", t=written)  # noqa: S608
            expected = sum(1 for _w, c in TAGS if c == written)
            assert found[0][0] == expected, (table, written)
    # ---- transcript.detected_language: observed tags (syntax only), through the codec
    observed = [(w, c) for w, c in TAGS] + [
        ("UND", "und"),
        ("Bn-in", "bn-IN"),
        ("XQ-IN", "xq-IN"),
        ("HI-latn-IN", "hi-Latn-IN"),
        ("zxx", "zxx"),
    ]
    run_id = new_uuid()
    rows_factory = _support.Rows(db.metadata)
    for written, canonical in observed:
        doc = golden_doc("Transcript", "minimal")
        doc["id"] = new_id("tsc")
        doc["detected_language"] = written
        model = load_entity("Transcript", json.dumps(doc))
        assert model.detected_language == canonical, (written, str(model.detected_language))
        row = dict(codecs.encode_transcript(model, run_id=run_id).row)
        assert row["detected_language"] == canonical
        with db.begin() as conn:
            rows_factory.insert_exact(conn, "transcript", row)
    stored = _scalar(db, "SELECT detected_language, doc FROM transcript")
    assert sorted(r[0] for r in stored) == sorted(c for _w, c in observed)
    for column, doc_text in stored:
        assert json.loads(doc_text)["detected_language"] == column
    assert (
        _scalar(db, "SELECT count(*) FROM transcript WHERE detected_language = 'HI-IN'")[0][0] == 0
    )
    twins = sum(1 for _w, c in observed if c == "hi-IN")
    assert twins == 4
    assert (
        _scalar(db, "SELECT count(*) FROM transcript WHERE detected_language = 'hi-IN'")[0][0]
        == twins
    )


@pytest.mark.parametrize("written", ["hi-in", "HI-IN", "Hi-In", "hi-deva-in", "HI-latn-IN"])
def test_language_tag_of_a_stored_bundle_transcript_is_canonical(
    db: Db, evaldb: ModuleType, written: str
) -> None:
    """The same through the repository: a run bundle whose transcript spells its observed tag in
    another case is stored with the canonical tag in the column and in the document, and loads
    back with it."""
    doc = renew_ids(run_bundle_doc(tier="T1", origin="simulated"), run=True)
    doc["transcripts"][0]["detected_language"] = written
    bundle = load_run_bundle_doc(doc)
    canonical = str(bundle.transcripts[0].detected_language)
    assert canonical == "-".join(
        p.capitalize() if len(p) == 4 else p.upper() if i else p.lower()
        for i, p in enumerate(written.split("-"))
    )
    store_bundle(db.engine, evaldb, bundle)
    row = _scalar(db, "SELECT detected_language, doc FROM transcript")[0]
    assert row[0] == canonical == json.loads(row[1])["detected_language"]
    loaded = evaldb.load_run_bundle(db.engine, bundle.id).bundle
    assert str(loaded.transcripts[0].detected_language) == canonical
