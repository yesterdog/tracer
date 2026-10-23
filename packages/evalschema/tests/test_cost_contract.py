# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-23, P1-24, P1-25 (contract): no float in the cost path; every money amount in the JSON
Schema is a string with the decimal pattern and every currency is the ``SupportedCurrency`` enum
(gate item 15, ADR 0014 I21, ADR 0002 Decision 2).

Assumed modules (docs/phase1/api-surface.md section 23): ``tracer_evalschema.usage``,
``tracer_evalschema.pricing`` and ``tracer_evalschema.cost``. Revision 15, N58 and detail D12: the
float scan reads these three source files and ``primitives`` (which defines ``DecimalString`` and
``Money``), and every ``tracer_evalschema`` module that the three import directly must be either
scanned or named in the explicit ``NO_AMOUNT_MODULES`` list below, so a new helper that does
amount arithmetic cannot escape the scan. Names are SCOPE section 5 names unless the docstring
says "Proposed name".
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ._support import api, data_json, resolve_schema
from ._support_b2 import registered_class, schema_of, walk_nodes
from ._support_b4 import (
    catalog_of,
    compute,
    cost_class,
    entry_doc,
    make_catalog,
    make_usage,
    pricing_class,
    qty,
    record_reported_doc,
    schema_core,
    summary_doc,
    usage_class,
    usage_doc,
    usd,
)

COST_PATH_MODULES = ("usage", "pricing", "cost")
# D12: modules scanned for floats, and modules the cost path imports that hold no amount (ids,
# the entity and hiding bases, errors, text types, digests and URL rules, the validation helpers).
# A module imported by usage, pricing or cost that is in neither list fails the test until it is
# classified.
SCANNED_MODULES = ("usage", "pricing", "cost", "primitives")
NO_AMOUNT_MODULES = (
    "_constraints",
    "_hiding",
    "_validation",
    "artifact",
    "base",
    "credentials",
    "digest",
    "errors",
    "events",
    "ids",
    "ijson",
    "snapshot",
    "text",
    "url_rules",
)
COST_ENTITIES = (
    ("usage", "UsageRecord"),
    ("pricing", "PricingCatalog"),
    ("cost", "CostRecord"),
    ("cost", "CostSummary"),
)
COST_VALUE_OBJECTS = (
    "UsageQuantity",
    "BillingRules",
    "PriceEntry",
    "PriceProvenance",
    "AppliesWhenCondition",
    "PricingSnapshot",
    "CostRounding",
    "ReportedCostRaw",
    "CostExclusion",
)
# property names that hold a money amount (the raw reported text ``amount_raw`` is not one)
MONEY_PROPERTIES = ("amount", "price", "minimum", "total")


def _float_uses(path: Path) -> list[str]:
    """Where the source of ``path`` mentions the type ``float`` or writes a float literal."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "float":
            found.append(f"line {node.lineno}: name float")
        elif isinstance(node, ast.Constant) and isinstance(node.value, float):
            found.append(f"line {node.lineno}: float literal {node.value!r}")
    return found


def _float_documents(bad: Any) -> list[tuple[Any, dict[str, Any]]]:
    """Documents that put ``bad`` (a float) where an amount, a quantity or a count belongs."""
    money = {"amount": bad, "currency": "USD"}
    return [
        (pricing_class("PriceEntry"), entry_doc(price=money)),
        (pricing_class("PriceEntry"), entry_doc(unit_quantity=bad)),
        (registered_class("BillingRules"), {"increment": bad, "minimum": "none"}),
        (registered_class("BillingRules"), {"increment": "none", "minimum": money}),
        (cost_class("CostRecord"), record_reported_doc(amount=money)),
        (cost_class("CostSummary"), {**summary_doc(), "total": money}),
        (usage_class("UsageRecord"), usage_doc(quantities=[qty("audio_input_seconds", bad)])),
        (usage_class("UsageRecord"), usage_doc(quantities=[qty("input_tokens", bad)])),
    ]


def _direct_imports(path: Path) -> set[str]:
    """The ``tracer_evalschema`` submodules that the source of ``path`` imports directly."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:  # from . import x / from .x import y
                found.add(module.split(".")[0] if module else "")
                found.update(a.name for a in node.names if not module)
            elif module == "tracer_evalschema":
                found.update(a.name for a in node.names)
            elif module.startswith("tracer_evalschema."):
                found.add(module.split(".")[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("tracer_evalschema."):
                    found.add(alias.name.split(".")[1])
    found.discard("")
    return found


def _models() -> list[Any]:
    models = [getattr(api(module), name) for module, name in COST_ENTITIES]
    registered = {m.__name__: m for m in api("registry").model_classes()}
    for name in COST_VALUE_OBJECTS:
        assert name in registered, f"{name} is not implemented yet"
        models.append(registered[name])
    return models


def test_no_float_in_cost_path() -> None:
    """ADR 0014 I21, A-11, 15a; revision 15, N58 and D12: no float anywhere in the cost path. (1)
    The source of the usage, pricing, cost and primitives modules never names ``float`` or writes
    a float literal, and every module that usage, pricing or cost import directly is either
    scanned or named in ``NO_AMOUNT_MODULES``. (2) No generated
    schema of these models has a ``number`` type (counts are integers, amounts and seconds are
    strings). (3) A Python float or a JSON number is rejected wherever an amount, a price, a
    minimum, a unit quantity, a quantity or a total is expected, and the amounts that
    ``compute_cost`` returns are strings."""
    assert not set(SCANNED_MODULES) & set(NO_AMOUNT_MODULES)
    for module in (*SCANNED_MODULES, *NO_AMOUNT_MODULES):
        assert Path(inspect.getsourcefile(api(module)) or "").is_file(), module
    for module in SCANNED_MODULES:
        path = Path(inspect.getsourcefile(api(module)) or "")
        assert path.is_file(), module
        assert _float_uses(path) == [], f"{module}: {_float_uses(path)}"
    for module in COST_PATH_MODULES:
        path = Path(inspect.getsourcefile(api(module)) or "")
        unclassified = sorted(_direct_imports(path) - set(SCANNED_MODULES) - set(NO_AMOUNT_MODULES))
        assert not unclassified, (
            f"{module} imports {unclassified}: scan them for floats or add them to "
            "NO_AMOUNT_MODULES"
        )
    for model in _models():
        for mode in ("validation", "serialization"):
            for node in walk_nodes(schema_of(model, mode)):
                assert node.get("type") != "number", f"{model.__name__} ({mode}): {node}"
    bad_amounts: tuple[Any, ...] = (0.15, 1.0, 0.0, float("nan"), float("inf"))
    for bad in bad_amounts:
        for model, doc in _float_documents(bad):
            with pytest.raises(ValidationError):
                model.model_validate(doc)
    record = compute([make_usage(quantities=[qty("input_tokens", 1_234_567)])], make_catalog())
    assert type(record.amount.amount) is str
    wire = record.model_dump(mode="json")
    assert wire["amount"]["amount"] == "0.18518505"
    assert wire["amount"] == {"amount": "0.18518505", "currency": "USD"}
    again = compute(
        [make_usage(quantities=[qty("input_tokens", 3)])], catalog_of({"price": usd("0.1")})
    )
    assert again.amount.amount == "0.0000003"
    assert type(again.amount.amount) is str


def _money_nodes(root: dict[str, Any], model_name: str) -> list[tuple[str, dict[str, Any]]]:
    """Every (property name, resolved schema) of a money-valued property anywhere in the schema
    of one model, including nested definitions."""
    found: list[tuple[str, dict[str, Any]]] = []
    for node in walk_nodes(root):
        for name, sub in node.get("properties", {}).items():
            if name in MONEY_PROPERTIES:
                found.append((f"{model_name}.{name}", sub))
    return found


def test_every_money_amount_schema_is_string_pattern() -> None:
    """Gate item 15, ADR 0014 I21: in the JSON Schema of every cost, pricing and usage model, each
    money-valued property (``amount``, ``price``, ``minimum``, ``total``) is, after following
    references and optional wrappers, the ``Money`` definition or the literal "none"; the
    ``Money`` definition's ``amount`` is exactly ``{"type": "string", "pattern": <decimal
    pattern>}``; its ``currency`` is exactly the ``enum`` of the codes in
    ``supported_currencies.json`` (``["USD"]`` in 0.0.1), never ``const``; no money-valued
    property is typed ``number`` or ``integer``. Seconds of usage are canonical decimal strings
    and counts of tokens are integers."""
    pattern = api("primitives").DECIMAL_PATTERN
    codes = data_json("supported_currencies.json")["currencies"]
    assert codes == ["USD"]
    seen: set[str] = set()
    for model in _models():
        schema = schema_of(model)
        for label, sub in _money_nodes(schema, model.__name__):
            seen.add(label.split(".", 1)[1])
            resolved = resolve_schema(schema, sub)
            texts = _leaf_types(schema, resolved)
            assert "number" not in texts, (label, texts)
            assert "integer" not in texts, (label, texts)
        defs = schema.get("$defs", {})
        if "Money" in defs:
            money = defs["Money"]
            assert schema_core(money["properties"]["amount"]) == {
                "type": "string",
                "pattern": pattern,
            }, model
            assert schema_core(money["properties"]["currency"]) == {
                "type": "string",
                "enum": codes,
            }, model
            assert set(money["required"]) == {"amount", "currency"}
    assert {"amount", "price", "minimum", "total"} <= seen, seen
    for unit_model in (registered_class("UsageQuantity"),):
        count = schema_of(unit_model)["properties"]["count"]
        kinds = {leaf.get("type") for leaf in _alternatives(count)}
        assert kinds == {"integer", "string"}, count
        strings = [leaf for leaf in _alternatives(count) if leaf.get("type") == "string"]
        assert strings, count
        assert all(leaf.get("pattern") == pattern for leaf in strings), count
    with pytest.raises(ValidationError):
        pricing_class("PriceEntry").model_validate(
            entry_doc(price={"amount": "1", "currency": "usd"})
        )


def _alternatives(node: dict[str, Any]) -> list[dict[str, Any]]:
    """The leaf alternatives of a property schema (anyOf, oneOf; a plain schema is its own)."""
    options = node.get("anyOf") or node.get("oneOf")
    if options is None:
        return [node]
    leaves: list[dict[str, Any]] = []
    for option in options:
        leaves.extend(_alternatives(option))
    return leaves


def _leaf_types(root: dict[str, Any], node: Any) -> set[str]:
    """Every ``type`` reachable through references, anyOf, oneOf and allOf, without descending
    into object properties."""
    node = resolve_schema(root, node)
    found: set[str] = set()
    if isinstance(node, dict):
        kind = node.get("type")
        if isinstance(kind, str):
            found.add(kind)
        for key in ("anyOf", "oneOf", "allOf"):
            for sub in node.get(key, []):
                found |= _leaf_types(root, sub)
    return found
