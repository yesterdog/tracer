# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Helpers shared by the Phase 2 batch C tests (repository, export, re-import, triggers).

Contract: docs/phase2/api-surface-batch-c.md. Imported with ``from evaldb_repo_support import ...``
(the tests directory has no ``__init__.py``; mypy cannot see sibling modules, hence the ``type:
ignore`` on those imports). Holds no test.

Contents: the Phase 1 bundle builders behind small functions; a clone helper that gives a bundle
document new IDs for chosen record kinds (rescoring, a second run, N18); a statement recorder and a
fault injector on an engine; whole-database dumps; the sized bundle of the statement-count and
huge-call tests.
"""

from __future__ import annotations

import contextlib
import copy
import importlib
import json
import re
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from decimal import Decimal
from types import ModuleType
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event

from evaldb_support import (  # type: ignore[import-not-found,unused-ignore]
    GOLDEN,
    load_entity,
    load_support_b13,
    verify_own_digest,
)

# the RunBundle list fields that hold records of one run, in RunBundle field order
RECORD_LISTS = (
    "artifacts",
    "transcripts",
    "turns",
    "exchanges",
    "tool_calls",
    "spans",
    "results",
    "usage_records",
    "cost_records",
    "cost_summaries",
)
# RunBundle field -> entity table
LIST_TABLE = {
    "artifacts": "artifact",
    "transcripts": "transcript",
    "turns": "turn",
    "exchanges": "exchange",
    "tool_calls": "tool_call_event",
    "spans": "stage_span",
    "results": "metric_result",
    "usage_records": "usage_record",
    "cost_records": "cost_record",
    "cost_summaries": "cost_summary",
}
# the run_bundle_member kinds, in member (RunBundle field) order, with the table and field
MEMBER_KINDS: tuple[tuple[str, str, str], ...] = (
    ("run", "run", "run"),
    ("snapshot", "config_snapshot", "snapshot"),
    ("pricing_catalog", "pricing_catalog", "pricing_catalogs"),
    ("test_profile", "test_profile", "test_profiles"),
    ("artifact", "artifact", "artifacts"),
    ("transcript", "transcript", "transcripts"),
    ("turn", "turn", "turns"),
    ("exchange", "exchange", "exchanges"),
    ("tool_call", "tool_call_event", "tool_calls"),
    ("span", "stage_span", "spans"),
    ("result", "metric_result", "results"),
    ("verdict", "run_verdict", "verdict"),
    ("usage_record", "usage_record", "usage_records"),
    ("cost_record", "cost_record", "cost_records"),
    ("cost_summary", "cost_summary", "cost_summaries"),
)
SHARED_RECORD_LISTS = tuple(f for f in RECORD_LISTS if f not in ("results",))

DEVANAGARI = "".join(map(chr, (0x928, 0x92E, 0x938, 0x94D, 0x924, 0x947)))  # a Devanagari greeting


def support(name: str) -> Any:
    """``tests.<name>`` of the evalschema package (``_support_b7`` and friends)."""
    load_support_b13()
    return importlib.import_module(f"tests.{name}")


# ------------------------------------------------------------------------------------ builders


def run_bundle_doc(**kwargs: Any) -> dict[str, Any]:
    """The RunBundle document of ``_support_b7.world(**kwargs)`` (a fresh run, new IDs)."""
    doc: dict[str, Any] = support("_support_b7").world(**kwargs).doc
    return doc


def load_run_bundle_doc(doc: Mapping[str, Any]) -> Any:
    return support("_support_b7").load_run(doc)


def make_bundle(**kwargs: Any) -> Any:
    kwargs.setdefault("tier", "T1")
    kwargs.setdefault("origin", "simulated")
    return load_run_bundle_doc(run_bundle_doc(**kwargs))


def project_doc(project_id: str) -> dict[str, Any]:
    doc = json.loads((GOLDEN / "Project" / "valid" / "minimal.json").read_text("utf-8"))
    assert isinstance(doc, dict)
    doc["id"] = project_id
    return doc


def make_project(project_id: Any) -> Any:
    return load_entity("Project", json.dumps(project_doc(str(project_id))))


def store_project_of(engine: sa.Engine, evaldb: ModuleType, bundle: Any) -> None:
    """Store the project a bundle's run names (the store never creates one)."""
    evaldb.store_project(engine, make_project(bundle.run.project_id))


def store_bundle(engine: sa.Engine, evaldb: ModuleType, bundle: Any) -> Any:
    store_project_of(engine, evaldb, bundle)
    return evaldb.store_run_bundle(engine, bundle)


def new_id(prefix: str) -> str:
    return str(support("_support_b2").new_id(prefix))


# ---------------------------------------------------------------------------- clone and edit


def _replace_strings(value: Any, mapping: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_replace_strings(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: _replace_strings(v, mapping) for k, v in value.items()}
    return value


def renew_ids(
    doc: Mapping[str, Any],
    *,
    lists: Iterable[str] = (),
    verdict: bool = False,
    run: bool = False,
    bundle: bool = True,
) -> dict[str, Any]:
    """A deep copy of a RunBundle document in which the IDs of the chosen records are new and every
    reference to them follows: the record lists named in ``lists`` (RunBundle field names), the
    verdict, the run (and with it the ``run_id`` of the usage records) and the bundle itself. The
    snapshot, the catalogs and the profiles keep their IDs (versioned definitions are shared)."""
    out = copy.deepcopy(dict(doc))
    old: list[str] = []
    if bundle:
        old.append(out["id"])
    if run:
        old.append(out["run"]["id"])
    if verdict and out.get("verdict") is not None:
        old.append(out["verdict"]["id"])
    for name in lists:
        old.extend(record["id"] for record in out[name])
    mapping = {value: new_id(value.split("_", 1)[0]) for value in old}
    renewed: dict[str, Any] = _replace_strings(out, mapping)
    return renewed


def with_marker_metadata(record: Mapping[str, Any], marker: str) -> dict[str, Any]:
    """The record document with metadata that carries ``marker`` (and the flags the model computes
    for it). Valid wherever the record was valid: ``metadata`` is outside every bundle rule."""
    from tracer_evalschema import base as ev_base

    metadata = {"marker": marker}
    flags = ev_base.compute_metadata_flags(metadata)
    return {
        **copy.deepcopy(dict(record)),
        "metadata": metadata,
        "metadata_flags": [f.model_dump(mode="json") for f in flags],
    }


def sized_bundle_doc(
    turns: int, tool_calls: int = 2, usages: int = 2, *, tier: str = "T1"
) -> dict[str, Any]:
    """A valid T1 RunBundle document with this many turns, tool calls and (agent) usage records
    that each have a reported cost record, with one summary over all of them. Both ends of the
    statement-count test and the huge-call test are built by this function, so they fill the same
    tables."""
    b3 = support("_support_b3")
    doc = run_bundle_doc(tier=tier, origin="simulated")
    tsc_id = doc["transcripts"][0]["id"]
    doc["turns"] = [
        b3.turn_doc(
            tsc_id,
            seq,
            role="caller" if seq % 2 == 0 else "agent",
            source="agent_stt" if seq % 2 == 0 else "agent_llm_output",
            exchange_index=seq // 2 % 2,
            text=f"turn {seq} {DEVANAGARI}",
        )
        for seq in range(turns)
    ]
    template = doc["tool_calls"][0]
    doc["tool_calls"] = [
        {**copy.deepcopy(template), "id": new_id("tcl"), "seq": seq} for seq in range(tool_calls)
    ]
    first_turn = doc["turns"][1]["id"] if turns > 1 else doc["turns"][0]["id"]
    refs = [
        {"kind": "turn", "ref_id": first_turn},
        {"kind": "exchange", "ref_id": doc["exchanges"][0]["id"]},
        {"kind": "tool_call", "ref_id": doc["tool_calls"][0]["id"]},
        {"kind": "artifact_range", "ref_id": doc["artifacts"][0]["id"], "start_ms": 1, "end_ms": 9},
        {"kind": "span", "ref_id": doc["spans"][0]["id"]},
    ]
    doc["results"][0]["evidence_refs"] = refs
    usage_template = doc["usage_records"][0]
    record_template = doc["cost_records"][0]
    new_usages = [{**copy.deepcopy(usage_template), "id": new_id("use")} for _ in range(usages)]
    costs = [
        {**copy.deepcopy(record_template), "id": new_id("cst"), "usage_record_ids": [u["id"]]}
        for u in new_usages
    ]
    agent_usage = [u for u in doc["usage_records"] if u["domain"] == "agent_under_test"]
    doc["usage_records"] = [*new_usages, *agent_usage]
    doc["cost_records"] = costs
    each = Decimal(record_template["amount"]["amount"])
    total = format(each * usages, "f")
    if "." in total:
        total = total.rstrip("0").rstrip(".")
    doc["cost_summaries"] = [
        {
            **doc["cost_summaries"][0],
            "total": {"amount": total, "currency": "USD"},
            "included": [c["id"] for c in costs],
        }
    ]
    return doc


# --------------------------------------------------------------------- engine instrumentation


class Boom(Exception):
    """Raised by a fault injector; never raised by the package."""


def insert_target(statement: str) -> str | None:
    """The table a statement inserts into, or None."""
    match = re.match(r'\s*INSERT\s+INTO\s+"?(\w+)"?', statement, re.IGNORECASE)
    return match.group(1) if match else None


class Statements:
    """Every statement an engine executes (``before_cursor_execute``), in order."""

    def __init__(self, engine: sa.Engine) -> None:
        self.engine = engine
        self.items: list[tuple[str, bool]] = []
        event.listen(engine, "before_cursor_execute", self._record)

    def _record(
        self, conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        self.items.append((statement, many))

    def close(self) -> None:
        event.remove(self.engine, "before_cursor_execute", self._record)

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def inserts(self) -> list[str]:
        return [t for s, _ in self.items if (t := insert_target(s)) is not None]

    @property
    def writes(self) -> list[str]:
        """The statements that change data (INSERT, UPDATE, DELETE)."""
        return [
            s for s, _ in self.items if re.match(r"\s*(INSERT|UPDATE|DELETE)\b", s, re.IGNORECASE)
        ]

    @property
    def selects(self) -> list[str]:
        return [s for s, _ in self.items if re.match(r"\s*SELECT\b", s, re.IGNORECASE)]


@contextlib.contextmanager
def recording(engine: sa.Engine) -> Iterator[Statements]:
    recorder = Statements(engine)
    try:
        yield recorder
    finally:
        recorder.close()


@contextlib.contextmanager
def fault_on_insert(engine: sa.Engine, table: str) -> Iterator[list[int]]:
    """Raise ``Boom`` from the first ``INSERT INTO <table>`` the engine executes. Yields a list
    that holds one item when the fault fired."""
    fired: list[int] = []

    def listener(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if insert_target(statement) == table and not fired:
            fired.append(1)
            raise Boom

    event.listen(engine, "before_cursor_execute", listener)
    try:
        yield fired
    finally:
        event.remove(engine, "before_cursor_execute", listener)


@contextlib.contextmanager
def pause_on_insert(
    engine: sa.Engine, table: str, reached: threading.Event, release: threading.Event
) -> Iterator[None]:
    """Block the executing thread at the first ``INSERT INTO <table>`` until ``release`` is set
    (``reached`` is set first), to hold a transaction open at a known point."""
    state = {"done": False}

    def listener(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if insert_target(statement) == table and not state["done"]:
            state["done"] = True
            reached.set()
            assert release.wait(timeout=60), "the test never released the paused writer"

    event.listen(engine, "before_cursor_execute", listener)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", listener)


# ------------------------------------------------------------------------------ database dumps


def table_names(metadata: sa.MetaData) -> list[str]:
    return sorted(metadata.tables)


def counts(engine: sa.Engine, metadata: sa.MetaData) -> dict[str, int]:
    """Row count of every table of the store."""
    result: dict[str, int] = {}
    with engine.connect() as conn:
        for name, table in sorted(metadata.tables.items()):
            result[name] = int(
                conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
            )
    return result


def nonempty(engine: sa.Engine, metadata: sa.MetaData) -> set[str]:
    return {name for name, n in counts(engine, metadata).items() if n}


def dump(engine: sa.Engine, metadata: sa.MetaData) -> dict[str, list[str]]:
    """Every row of every table as sorted ``repr`` strings: equal dumps are equal databases."""
    result: dict[str, list[str]] = {}
    with engine.connect() as conn:
        for name, table in sorted(metadata.tables.items()):
            rows = conn.execute(sa.select(table)).mappings().all()
            result[name] = sorted(repr(sorted(dict(row).items())) for row in rows)
    return result


def select_all(
    engine: sa.Engine, metadata: sa.MetaData, table: str, **where: Any
) -> list[dict[str, Any]]:
    t = metadata.tables[table]
    clause = sa.and_(sa.true(), *[t.c[k] == v for k, v in where.items()])
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(sa.select(t).where(clause)).mappings()]


def ids_of(models: Iterable[Any]) -> list[str]:
    return [str(m.id) for m in models]


def lists_of(bundle: Any) -> dict[str, list[str]]:
    """The wire IDs of every record list of a RunBundle, in order (the run, snapshot and verdict
    as one-item lists)."""
    result = {name: ids_of(getattr(bundle, name)) for name in RECORD_LISTS}
    result["pricing_catalogs"] = ids_of(bundle.pricing_catalogs)
    result["test_profiles"] = ids_of(bundle.test_profiles)
    result["run"] = [str(bundle.run.id)]
    result["snapshot"] = [str(bundle.snapshot.id)]
    result["verdict"] = [] if bundle.verdict is None else [str(bundle.verdict.id)]
    result["withheld"] = [f"{w.kind}:{w.record_id}" for w in bundle.withheld]
    return result


def table_set_of(bundle: Any) -> set[str]:
    """The tables a stored bundle fills, predicted from its shape alone (no tier input)."""
    tables = {"project", "run", "config_snapshot", "run_bundle", "run_bundle_member"}
    tables |= {"evaluator", "persona", "rubric", "metric_definition", "metric_binding"}
    tables |= {"snapshot_member"}
    for name in RECORD_LISTS:
        if getattr(bundle, name):
            tables.add(LIST_TABLE[name])
    if bundle.pricing_catalogs:
        tables.add("pricing_catalog")
    if bundle.test_profiles:
        tables.add("test_profile")
    if bundle.verdict is not None:
        tables.add("run_verdict")
    if bundle.withheld:
        tables.add("run_bundle_withheld")
    if bundle.snapshot.evaluator.test_profile is not None:
        tables.add("snapshot_profile_ref")
    refs = bool(bundle.run.external_refs) or any(s.span_ref is not None for s in bundle.spans)
    refs = refs or any(t.provider_call_ref is not None for t in bundle.transcripts)
    refs = refs or any(u.provider_call_ref is not None for u in bundle.usage_records)
    if refs:
        tables.add("external_ref_index")
    return tables


def counter_of(items: Sequence[Any]) -> Counter[Any]:
    return Counter(items)


def run_in_thread(function: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    """Run ``function`` in a thread; the list gets its result or its exception."""
    box: list[Any] = []

    def target() -> None:
        try:
            box.append(function())
        except BaseException as exc:
            box.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, box


def verify_bundle_digests(bundle: Any) -> None:
    """Every own digest a RunBundle holds verifies over its own digest domain: the snapshot, the
    definitions it embeds, the catalogs and the test profiles."""
    snapshot = bundle.snapshot
    verify_own_digest(snapshot)
    for definition in (
        snapshot.evaluator,
        snapshot.persona,
        snapshot.rubric,
        *snapshot.metric_definitions,
        *snapshot.metric_bindings,
        *bundle.pricing_catalogs,
        *bundle.test_profiles,
    ):
        verify_own_digest(definition)
