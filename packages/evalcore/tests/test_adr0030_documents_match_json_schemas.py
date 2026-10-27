# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""``test_adr0030_documents_match_json_schemas`` (Phase 4 batch J, task P4-J05).

ADR 0030 gives five JSON Schema blocks (draft 2020-12): ``tracer.golden_set/1``,
``tracer.judge_profile/1``, ``tracer.judge/1``, ``tracer.judge_exchange/1`` and
``tracer.judge_labels/1``. This test reads them from the ADR and checks, as the test of ADR 0027
does for its three: each block is a valid draft 2020-12 schema; the promises the ADR makes about
all of them (title and ``$schema`` pinned, every object closed, every integer bounded by
``2**53 - 1``, ``required`` inside ``properties``, no ``const``); every member is listed with the
rule it comes from (no member without a source rule); the schemas ``tracer-evalcore`` ships are
equal to the blocks; the enumerations of the code agree with the blocks; documents built by the
product validate under ``jsonschema``, and a list of structural faults is refused by
``jsonschema`` and by the product's own validator alike; and the checks have teeth.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import pytest
from jsonschema import (  # type: ignore[import-untyped,unused-ignore]
    Draft202012Validator,
    SchemaError,
)

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]
from evalcore_p4j_support import (  # type: ignore[import-not-found,unused-ignore]
    build_judged,
    golden_fixture,
    golden_text,
    judge_setup,
    mod,
    verdict_json,
)

MAX_INT = 2**53 - 1
SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
ADR30 = sup.ADR_DIR / "0030-llm-judge-uncalibrated-signal.md"
HEADINGS = {
    "tracer.golden_set/1": "## The `tracer.golden_set/1` format",
    "tracer.judge_profile/1": "## The `tracer.judge_profile/1` format",
    "tracer.judge/1": "## The `tracer.judge/1` format",
    "tracer.judge_exchange/1": "## The `tracer.judge_exchange/1` format",
    "tracer.judge_labels/1": "## The `tracer.judge_labels/1` format",
}


# ---------------------------------------------------------------------------- the ADR text


def extract_schemas(text: str) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for title, heading in HEADINGS.items():
        section = sup.heading_section(text, heading)
        blocks = re.findall(r"```json\n(.*?)\n```", section, re.DOTALL)
        assert len(blocks) == 1, f"{title}: {len(blocks)} json blocks, expected 1"
        try:
            schema = json.loads(blocks[0])
        except json.JSONDecodeError as error:
            raise AssertionError(f"{title}: the block is not JSON: {error}") from error
        assert isinstance(schema, dict), title
        schemas[title] = schema
    return schemas


def _walk(node: Any, path: str = "$") -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        found.append((path, node))
        for key, value in node.items():
            found += _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _walk(value, f"{path}[{index}]")
    return found


def _types(node: dict[str, Any]) -> list[str]:
    raw = node.get("type")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, str)]
    return []


def schema_problems(title: str, schema: dict[str, Any]) -> list[str]:
    """Every defect of one schema against the promises of ADR 0030; empty means none."""
    problems: list[str] = []
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        return [f"{title}: not a valid draft 2020-12 schema: {error.message}"]
    if schema.get("title") != title:
        problems.append(f"{title}: title is {schema.get('title')!r}")
    if schema.get("$schema") != SCHEMA_URI:
        problems.append(f"{title}: $schema is {schema.get('$schema')!r}")
    if schema.get("type") != "object":
        problems.append(f"{title}: the document is not an object")
    for path, node in _walk(schema):
        if "const" in node:
            problems.append(f"{title} {path}: const (single-member sets are enum, ADR 0003 D6)")
        types = _types(node)
        if "object" in types:
            if node.get("additionalProperties") is not False:
                problems.append(f"{title} {path}: object not closed (additionalProperties)")
            props = node.get("properties", {})
            missing = set(node.get("required", [])) - set(props)
            if missing:
                problems.append(
                    f"{title} {path}: required keys {sorted(missing)} not in properties"
                )
            if "required" not in node and props:
                problems.append(f"{title} {path}: properties without required")
        if "integer" in types and "enum" not in node:
            bound = node.get("maximum")
            if not isinstance(bound, int) or bound > MAX_INT:
                problems.append(f"{title} {path}: integer without maximum <= 2**53 - 1")
            if not isinstance(node.get("minimum"), int):
                problems.append(f"{title} {path}: integer without minimum")
        if "string" in types and "pattern" in node:
            try:
                re.compile(node["pattern"])
            except re.error:
                problems.append(f"{title} {path}: pattern does not compile")
    return problems


def member_problems(title: str, schema: dict[str, Any], section: str) -> list[str]:
    """Every top-level member needs a table row that cites the rule it comes from."""
    problems: list[str] = []
    for member in schema.get("properties", {}):
        rows = [ln for ln in section.splitlines() if ln.startswith(f"| `{member}` |")]
        if len(rows) != 1:
            problems.append(f"{title}: member {member} has {len(rows)} table rows, expected 1")
        elif not re.search(r"rules? \d+", rows[0]):
            problems.append(f"{title}: member {member} names no source rule")
    return problems


def all_problems(text: str) -> list[str]:
    try:
        schemas = extract_schemas(text)
    except AssertionError as error:
        return [str(error)]
    problems: list[str] = []
    for title, schema in schemas.items():
        problems += schema_problems(title, schema)
        section = sup.heading_section(text, HEADINGS[title])
        problems += member_problems(title, schema, section)
    return problems


def adr_text() -> str:
    text: str = ADR30.read_text("utf-8")
    return text


# ----------------------------------------------------------------------------- the checks


def test_adr0030_documents_match_json_schemas() -> None:
    """The five blocks hold the promises, list their sources, equal the shipped constants and
    agree with the code's enumerations, caps and documents."""
    text = adr_text()
    problems = all_problems(text)
    assert problems == [], "\n".join(problems)
    schemas = extract_schemas(text)
    assert list(schemas) == list(HEADINGS)

    golden = mod("golden")
    records = mod("judgerecords")
    labels = mod("judgelabels")
    output = mod("judgeoutput")
    prompt = mod("judgeprompt")
    shipped = {
        "tracer.golden_set/1": golden.GOLDEN_SET_JSON_SCHEMA,
        "tracer.judge_profile/1": records.JUDGE_PROFILE_JSON_SCHEMA,
        "tracer.judge/1": records.JUDGE_JSON_SCHEMA,
        "tracer.judge_exchange/1": records.JUDGE_EXCHANGE_JSON_SCHEMA,
        "tracer.judge_labels/1": labels.JUDGE_LABELS_JSON_SCHEMA,
    }
    for title, constant in shipped.items():
        assert dict(constant) == schemas[title], f"{title}: the shipped schema differs from the ADR"
    assert golden.GOLDEN_SET_FORMAT == "tracer.golden_set/1"
    assert records.JUDGE_PROFILE_FORMAT == "tracer.judge_profile/1"
    assert records.JUDGE_FORMAT == "tracer.judge/1"
    assert records.JUDGE_EXCHANGE_FORMAT == "tracer.judge_exchange/1"
    assert labels.JUDGE_LABELS_FORMAT == "tracer.judge_labels/1"

    # enumerations and caps that the code and the blocks both state
    g = schemas["tracer.golden_set/1"]["properties"]
    branches = g["items"]["items"]["oneOf"]
    assert {b["properties"]["match_mode"]["enum"][0] for b in branches} == set(golden.MATCH_MODES)
    assert g["items"]["maxItems"] == golden.MAX_GOLDEN_ITEMS == 100
    facts = next(b for b in branches if "expected_facts" in b["properties"])["properties"]
    assert facts["expected_facts"]["maxItems"] == golden.MAX_FACTS_PER_ITEM == 10
    assert facts["expected_facts"]["items"]["maxLength"] == golden.MAX_FACT_CHARS == 300
    answer = next(b for b in branches if "expected_answer" in b["properties"])["properties"]
    assert answer["expected_answer"]["maxLength"] == golden.MAX_EXPECTED_ANSWER_CHARS == 1000
    assert facts["question"]["maxLength"] == mod("simtypes").MAX_LINE_CHARS == 2500
    assert g["language"]["enum"] == ["mr"]
    j = schemas["tracer.judge/1"]["properties"]
    assert [v for v in j["verdict"]["enum"] if v is not None] == list(output.JUDGE_VERDICTS)
    assert [v for v in j["reason_code"]["enum"] if v is not None] == list(output.REASON_CODES)
    assert j["facts"]["items"]["properties"]["status"]["enum"] == list(output.FACT_STATUSES)
    assert j["calibration"]["enum"] == [records.CALIBRATION] == ["uncalibrated"]
    assert j["confidence"]["enum"] == [records.CONFIDENCE] == ["none_claimed"]
    p = schemas["tracer.judge_profile/1"]["properties"]
    assert p["profile"]["enum"] == [*mod_profiles().PROFILE_NAMES, "fake/1"]
    assert list(p["template_sha256"]["properties"]) == list(prompt.TEMPLATE_IDS)
    assert p["calibration"]["enum"] == ["uncalibrated"]
    lab = schemas["tracer.judge_labels/1"]["properties"]["labels"]["items"]["properties"]
    assert [v for v in lab["verdict"]["enum"] if v is not None] == list(output.JUDGE_VERDICTS)
    assert lab["basis"]["enum"] == list(labels.LABEL_BASES) == ["audio", "stt_text", "both"]
    ex = schemas["tracer.judge_exchange/1"]["properties"]
    assert ex["response_body"]["maxLength"] == 65536
    assert ex["messages"]["items"]["properties"]["role"]["enum"] == ["system", "user"]

    # every member of every document is named by a rule of the ADR text (the text of the rule)
    for title, schema in schemas.items():
        assert member_problems(title, schema, sup.heading_section(text, HEADINGS[title])) == []


def mod_profiles() -> Any:
    import importlib

    return importlib.import_module("tracer_evalproviders.judgeprofiles")


def valid_documents() -> dict[str, Any]:
    """One valid document of each kind, built by the product."""
    records = mod("judgerecords")
    labels = mod("judgelabels")
    golden = mod("golden").parse_golden_set(golden_text())
    setup = judge_setup(golden)
    call = build_judged()
    judge_doc = records.build_judge_document(
        verdict="agrees",
        reason_code="all_facts_stated",
        facts=((0, "stated"), (1, "stated")),
        window_turn_seqs=(0, 1),
        excluded_turns=0,
        attempts=1,
        regenerated=False,
        exchange_sha256="a" * 64,
    )
    exchange = records.build_exchange_document(
        messages=[
            {"role": "system", "content": "system text"},
            {"role": "user", "content": "user text"},
        ],
        response_body='{"id":"x"}',
        request_id="req-1",
        status=200,
    )
    bundle_id = str(call.bundle.id)
    label_doc = {
        "format": "tracer.judge_labels/1",
        "bundle_id": bundle_id,
        "labeller": "labeller_one",
        "labels": [
            {"item_id": "mr_q00", "verdict": "agrees", "basis": "audio"},
            {"item_id": "mr_q01", "verdict": None, "basis": "both"},
        ],
    }
    del labels
    return {
        "tracer.golden_set/1": json.loads(golden_text()),
        "tracer.judge_profile/1": copy.deepcopy(setup.profile_document),
        "tracer.judge/1": judge_doc,
        "tracer.judge_exchange/1": exchange,
        "tracer.judge_labels/1": label_doc,
    }


def product_accepts(title: str, document: dict[str, Any]) -> bool:
    records = mod("judgerecords")
    labels = mod("judgelabels")
    golden = mod("golden")
    try:
        if title == "tracer.golden_set/1":
            golden.parse_golden_set(json.dumps(document, ensure_ascii=False))
        elif title == "tracer.judge_profile/1":
            records.validate_judge_profile_document(document)
        elif title == "tracer.judge/1":
            records.validate_judge_document(document)
        elif title == "tracer.judge_exchange/1":
            records.validate_judge_exchange_document(document)
        else:
            labels.validate_judge_labels(document)
    except ValueError:
        return False
    return True


def set_path(document: Any, path: list[Any], value: Any) -> None:
    for step in path[:-1]:
        document = document[step]
    document[path[-1]] = value


def drop_path(document: Any, path: list[Any]) -> None:
    for step in path[:-1]:
        document = document[step]
    del document[path[-1]]


#: (kind, label, how to break a valid document); every one must be refused by both validators
FAULTS: list[tuple[str, str, Any]] = [
    ("tracer.golden_set/1", "unknown member", lambda d: d.update(extra=1)),
    (
        "tracer.golden_set/1",
        "format changed",
        lambda d: set_path(d, ["format"], "tracer.golden_set/2"),
    ),
    ("tracer.golden_set/1", "no items", lambda d: set_path(d, ["items"], [])),
    ("tracer.golden_set/1", "language other than mr", lambda d: set_path(d, ["language"], "hi")),
    ("tracer.golden_set/1", "item id upper case", lambda d: set_path(d, ["items", 0, "id"], "Q1")),
    ("tracer.golden_set/1", "item unknown member", lambda d: set_path(d, ["items", 0, "x"], 1)),
    (
        "tracer.golden_set/1",
        "item turn negative",
        lambda d: set_path(d, ["items", 0, "scripted_turn_index"], -1),
    ),
    (
        "tracer.golden_set/1",
        "item turn a bool",
        lambda d: set_path(d, ["items", 0, "scripted_turn_index"], True),
    ),
    (
        "tracer.golden_set/1",
        "item turn beyond 2**53-1",
        lambda d: set_path(d, ["items", 0, "scripted_turn_index"], 2**53),
    ),
    (
        "tracer.golden_set/1",
        "mode unknown",
        lambda d: set_path(d, ["items", 0, "match_mode"], "rating"),
    ),
    (
        "tracer.golden_set/1",
        "facts and answer together",
        lambda d: set_path(d, ["items", 0, "expected_answer"], "x"),
    ),
    ("tracer.golden_set/1", "no facts", lambda d: set_path(d, ["items", 0, "expected_facts"], [])),
    (
        "tracer.golden_set/1",
        "eleven facts",
        lambda d: set_path(d, ["items", 0, "expected_facts"], ["\u0905"] * 11),
    ),
    (
        "tracer.golden_set/1",
        "fact too long",
        lambda d: set_path(d, ["items", 0, "expected_facts"], ["\u0905" * 301]),
    ),
    (
        "tracer.golden_set/1",
        "native review not a bool",
        lambda d: set_path(d, ["items", 0, "needs_native_review"], "yes"),
    ),
    (
        "tracer.golden_set/1",
        "native review missing",
        lambda d: drop_path(d, ["items", 0, "needs_native_review"]),
    ),
    ("tracer.golden_set/1", "key missing", lambda d: drop_path(d, ["key"])),
    ("tracer.judge_profile/1", "unknown member", lambda d: d.update(extra=1)),
    ("tracer.judge_profile/1", "profile unknown", lambda d: set_path(d, ["profile"], "generic/1")),
    (
        "tracer.judge_profile/1",
        "profile without version",
        lambda d: set_path(d, ["profile"], "openai"),
    ),
    (
        "tracer.judge_profile/1",
        "calibration other",
        lambda d: set_path(d, ["calibration"], "calibrated"),
    ),
    ("tracer.judge_profile/1", "calibration missing", lambda d: drop_path(d, ["calibration"])),
    ("tracer.judge_profile/1", "model with a space", lambda d: set_path(d, ["model"], "a b")),
    (
        "tracer.judge_profile/1",
        "host with a path",
        lambda d: set_path(d, ["host"], "example.com/x"),
    ),
    ("tracer.judge_profile/1", "path without slash", lambda d: set_path(d, ["path"], "v1/chat")),
    (
        "tracer.judge_profile/1",
        "path with a query",
        lambda d: set_path(d, ["path"], "/v1/chat?x=1"),
    ),
    ("tracer.judge_profile/1", "key_env lower case", lambda d: set_path(d, ["key_env"], "my_key")),
    (
        "tracer.judge_profile/1",
        "key_env a value",
        lambda d: set_path(d, ["key_env"], "sk-" + "x" * 30),
    ),
    (
        "tracer.judge_profile/1",
        "params unknown member",
        lambda d: set_path(d, ["params", "tools"], []),
    ),
    (
        "tracer.judge_profile/1",
        "params max_tokens zero",
        lambda d: set_path(d, ["params", "max_tokens"], 0),
    ),
    (
        "tracer.judge_profile/1",
        "params temperature high",
        lambda d: set_path(d, ["params", "temperature"], 3),
    ),
    (
        "tracer.judge_profile/1",
        "params seed guarantee",
        lambda d: set_path(d, ["params", "seed_guarantee"], "strong"),
    ),
    (
        "tracer.judge_profile/1",
        "caps zero",
        lambda d: set_path(d, ["caps", "max_judge_requests"], 0),
    ),
    ("tracer.judge_profile/1", "caps unknown", lambda d: set_path(d, ["caps", "x"], 1)),
    (
        "tracer.judge_profile/1",
        "golden sha short",
        lambda d: set_path(d, ["golden_set", "sha256"], "abc"),
    ),
    (
        "tracer.judge_profile/1",
        "template sha upper",
        lambda d: set_path(d, ["template_sha256", "all_facts"], "A" * 64),
    ),
    (
        "tracer.judge_profile/1",
        "template mode missing",
        lambda d: drop_path(d, ["template_sha256", "equivalent_answer"]),
    ),
    ("tracer.judge/1", "unknown member", lambda d: d.update(reason="free text")),
    ("tracer.judge/1", "confidence a number", lambda d: set_path(d, ["confidence"], 0.9)),
    ("tracer.judge/1", "confidence missing", lambda d: drop_path(d, ["confidence"])),
    ("tracer.judge/1", "calibrated", lambda d: set_path(d, ["calibration"], "calibrated")),
    ("tracer.judge/1", "verdict unknown", lambda d: set_path(d, ["verdict"], "maybe")),
    ("tracer.judge/1", "reason unknown", lambda d: set_path(d, ["reason_code"], "because")),
    ("tracer.judge/1", "fact status unknown", lambda d: set_path(d, ["facts", 0, "status"], "yes")),
    ("tracer.judge/1", "fact extra member", lambda d: set_path(d, ["facts", 0, "note"], "x")),
    ("tracer.judge/1", "attempts three", lambda d: set_path(d, ["attempts"], 3)),
    ("tracer.judge/1", "attempts a bool", lambda d: set_path(d, ["attempts"], True)),
    ("tracer.judge/1", "regenerated a string", lambda d: set_path(d, ["regenerated"], "no")),
    ("tracer.judge/1", "negative excluded", lambda d: set_path(d, ["excluded_turns"], -1)),
    ("tracer.judge/1", "exchange sha short", lambda d: set_path(d, ["exchange_sha256"], "abc")),
    ("tracer.judge/1", "turn seq a string", lambda d: set_path(d, ["window_turn_seqs", 0], "0")),
    ("tracer.judge_exchange/1", "unknown member", lambda d: d.update(attempt=1)),
    (
        "tracer.judge_exchange/1",
        "one message",
        lambda d: set_path(d, ["messages"], d["messages"][:1]),
    ),
    (
        "tracer.judge_exchange/1",
        "role assistant",
        lambda d: set_path(d, ["messages", 1, "role"], "assistant"),
    ),
    (
        "tracer.judge_exchange/1",
        "empty content",
        lambda d: set_path(d, ["messages", 0, "content"], ""),
    ),
    ("tracer.judge_exchange/1", "status 99", lambda d: set_path(d, ["status"], 99)),
    ("tracer.judge_exchange/1", "status 600", lambda d: set_path(d, ["status"], 600)),
    ("tracer.judge_exchange/1", "status a string", lambda d: set_path(d, ["status"], "200")),
    (
        "tracer.judge_exchange/1",
        "request id with space",
        lambda d: set_path(d, ["request_id"], "a b"),
    ),
    (
        "tracer.judge_exchange/1",
        "body too long",
        lambda d: set_path(d, ["response_body"], "x" * 65537),
    ),
    ("tracer.judge_exchange/1", "body missing", lambda d: drop_path(d, ["response_body"])),
    ("tracer.judge_labels/1", "unknown member", lambda d: d.update(judge_verdict="agrees")),
    (
        "tracer.judge_labels/1",
        "format changed",
        lambda d: set_path(d, ["format"], "tracer.judge_labels/2"),
    ),
    (
        "tracer.judge_labels/1",
        "bundle id wrong prefix",
        lambda d: set_path(d, ["bundle_id"], "run_" + d["bundle_id"][5:]),
    ),
    (
        "tracer.judge_labels/1",
        "labeller upper case",
        lambda d: set_path(d, ["labeller"], "Labeller"),
    ),
    ("tracer.judge_labels/1", "no labels", lambda d: set_path(d, ["labels"], [])),
    (
        "tracer.judge_labels/1",
        "verdict unknown",
        lambda d: set_path(d, ["labels", 0, "verdict"], "yes"),
    ),
    (
        "tracer.judge_labels/1",
        "basis unknown",
        lambda d: set_path(d, ["labels", 0, "basis"], "ear"),
    ),
    (
        "tracer.judge_labels/1",
        "label extra member",
        lambda d: set_path(d, ["labels", 0, "note"], "x"),
    ),
    (
        "tracer.judge_labels/1",
        "label item id missing",
        lambda d: drop_path(d, ["labels", 0, "item_id"]),
    ),
]


def test_adr0030_documents_match_json_schemas_documents_and_faults() -> None:
    """Valid documents built by the product pass ``jsonschema`` and the product validator; each
    structural fault is refused by both (cross-field rules are the validator's alone)."""
    text = adr_text()
    schemas = extract_schemas(text)
    documents = valid_documents()
    for title, document in documents.items():
        Draft202012Validator(schemas[title]).validate(document)
        assert product_accepts(title, document), title
    seen: set[str] = set()
    for title, label, break_it in FAULTS:
        broken = copy.deepcopy(documents[title])
        break_it(broken)
        seen.add(title)
        assert not Draft202012Validator(schemas[title]).is_valid(broken), (title, label)
        assert not product_accepts(title, broken), (title, label)
    assert seen == set(HEADINGS)
    # a document that is valid under jsonschema is valid for the product too (the cross-field
    # rules that the schema cannot state are the product's: tested with the records)
    judge_doc = copy.deepcopy(documents["tracer.judge/1"])
    records = mod("judgerecords")
    for change in (
        {"verdict": None},  # a verdict-less document with a reason
        {"regenerated": True},  # one attempt but regenerated
        {"attempts": 0},  # no attempt but an exchange
        {"attempts": 2},  # two attempts but not regenerated
        {"reason_code": None},  # a verdict without a reason
    ):
        crossed = {**judge_doc, **change}
        assert Draft202012Validator(schemas["tracer.judge/1"]).is_valid(crossed), change
        with pytest.raises(records.JudgeDocumentError) as caught:
            records.validate_judge_document(crossed)
        assert str(caught.value) == caught.value.code
    # a refusal names a place, never a value
    broken = copy.deepcopy(documents["tracer.judge_profile/1"])
    canary = "CANARY-" + "zq7fc3d1"
    broken["model"] = canary + " bad"
    with pytest.raises(records.JudgeDocumentError) as caught:
        records.validate_judge_profile_document(broken)
    assert canary not in str(caught.value) + repr(caught.value)
    # the fixture's own verdict text is a valid reply of the output validator
    assert mod("judgeoutput").parse_judge_output(
        verdict_json("agrees", "all_facts_stated", ["stated"]), match_mode="all_facts", fact_count=1
    )
    assert golden_fixture()["golden_set"]["format"] == "tracer.golden_set/1"


# ------------------------------------------------------------------------------- teeth


def _broken(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new, 1)


def test_adr0030_documents_match_json_schemas_checks_have_teeth() -> None:
    """Broken copies of the ADR text are refused by the same function that passes the real one."""
    text = adr_text()
    assert all_problems(text) == []
    block = re.search(r"## The `tracer.judge/1` format.*?```json\n(.*?)\n```", text, re.DOTALL)
    assert block is not None
    schema_text = block.group(1)
    mutants = {
        "not JSON": text.replace(schema_text, schema_text[:-5], 1),
        "invalid schema": _broken(text, '"type": "object"', '"type": "obejct"'),
        "object not closed": _broken(
            text, '"additionalProperties": false', '"additionalProperties": true'
        ),
        "integer bound too large": _broken(
            text, '"maximum": 9007199254740991', '"maximum": 9007199254740993'
        ),
        "title changed": _broken(
            text, '"title": "tracer.judge_exchange/1"', '"title": "tracer.judge_exchange/2"'
        ),
        "$schema changed": _broken(
            text,
            '"$schema": "https://json-schema.org/draft/2020-12/schema"',
            '"$schema": "https://json-schema.org/draft-07/schema"',
        ),
        "a block missing": text.replace(
            "## The `tracer.judge_labels/1` format", "## The labels format", 1
        ),
        "pattern broken": _broken(
            text, '"pattern": "^[0-9a-f]{64}$"', '"pattern": "^[0-9a-f{64}$"'
        ),
        "a member without a source row": re.sub(
            r"^\| `calibration` \|.*\n", "", text, count=1, flags=re.MULTILINE
        ),
        "a source row without a rule": re.sub(
            r"^(\| `exchange_sha256` \|)[^\n]*$", r"\1 nothing |", text, count=1, flags=re.MULTILINE
        ),
    }
    for label, mutant in mutants.items():
        assert mutant != text, label
        assert all_problems(mutant), f"the check accepted the mutant {label!r}"


def test_adr0030_documents_match_json_schemas_validator_reads_the_bounds() -> None:
    """The hand validator of the shipped documents reads the keywords the five schemas use and
    agrees with ``jsonschema`` on them (a bool is neither an integer nor a number; a pattern's
    ``$`` is the end of the text, which the Python ``re`` of ``jsonschema`` is not for a final
    line feed)."""
    schemalite = mod("_schemalite")
    check = schemalite.first_violation
    text = {"type": "string", "minLength": 2, "maxLength": 3}
    assert [check(text, v) for v in ("a", "ab", "abc", "abcd")] == ["/", None, None, "/"]
    array = {"type": "array", "minItems": 1, "maxItems": 2}
    assert [check(array, v) for v in ([], [1], [1, 2], [1, 2, 3])] == ["/", None, None, "/"]
    number = {"type": "number", "minimum": 0, "maximum": 2}
    assert [check(number, v) for v in (0, 0.5, 2, 2.5, -0.5, True, "1", None)] == [
        None,
        None,
        None,
        "/",
        "/",
        "/",
        "/",
        "/",
    ]
    integer = {"type": "integer", "minimum": 0, "maximum": 9}
    assert [check(integer, v) for v in (0, 9, 10, -1, 1.0, True, False)] == [
        None,
        None,
        "/",
        "/",
        "/",
        "/",
        "/",
    ]
    pattern = {"type": "string", "pattern": "^[a-z]+$"}
    assert [check(pattern, v) for v in ("ab", "ab\n", "a b", "")] == [None, "/", "/", "/"]
    both = {"type": ["integer", "null"], "minimum": 1}
    assert [check(both, v) for v in (1, None, 0, "x")] == [None, None, "/", "/"]
    nested = {
        "type": "object",
        "additionalProperties": False,
        "required": ["a"],
        "properties": {"a": {"type": "array", "items": {"type": "integer", "maximum": 3}}},
    }
    assert check(nested, {"a": [1, 2]}) is None
    assert check(nested, {"a": [1, 5]}) == "/a/1"
    assert check(nested, {"a": [], "b": 1}) == "/b"
    assert check(nested, {}) == "/a"
