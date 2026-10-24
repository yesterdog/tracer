# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared pieces of the real-sample converters (docs/phase1/api-surface-mappings.md section 1).

``Citation``, ``TraceEntry``, ``GapRow``, ``TableRow`` and ``MappingResult`` are the pinned data
shapes. ``Recorder`` collects what a converter produces (records, trace entries, warnings, gap
rows, consumed pointers, external references) and ``Draft`` builds one record: a field and its
trace entries are committed together, so a field that the schema refuses is dropped with a
warning, never guessed, and its trace entry disappears with it. Nothing here reads the clock or
the environment and nothing is written to disk.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from tracer_evalschema import SCHEMA_VERSION
from tracer_evalschema.artifact import Artifact, ChannelMap
from tracer_evalschema.cost import CostRecord
from tracer_evalschema.events import IngestWarning, StageSpan, ToolCallEvent
from tracer_evalschema.ids import ExternalRef, id_type
from tracer_evalschema.text import replace_lone_surrogates
from tracer_evalschema.transcript import Exchange, Transcript, Turn
from tracer_evalschema.usage import UsageRecord

__all__ = [
    "Citation",
    "Draft",
    "GapRow",
    "Leg",
    "MappingResult",
    "Recorder",
    "TableRow",
    "TraceEntry",
    "as_sequence",
    "chain",
    "floor_ms",
    "is_int",
    "pointer",
    "raw_text",
    "scalar_leaves",
    "source_token_safe",
    "unix_nano_to_utc_us",
]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Citation:
    url: str | None
    retrieved_at: str | None
    statement: str


@dataclass(frozen=True)
class TraceEntry:
    record: str
    field: str
    sources: tuple[str, ...]
    derivation: str | None
    citation: Citation | None = None


@dataclass(frozen=True)
class GapRow:
    fixture: str
    source_key: str
    classification: str
    kind: str
    reason: str
    owner: str


@dataclass(frozen=True)
class TableRow:
    target: str
    kind: str
    source: str | None
    derivation: str | None
    citation: Citation | None
    note: str


@dataclass(frozen=True)
class MappingResult:
    fixture: str
    records: tuple[Any, ...] = ()
    channel_maps: tuple[ChannelMap, ...] = ()
    external_refs: tuple[ExternalRef, ...] = ()
    external_ref_sources: tuple[str, ...] = ()
    warnings: tuple[IngestWarning, ...] = ()
    gaps: tuple[GapRow, ...] = ()
    consumed: tuple[str, ...] = ()
    trace: tuple[TraceEntry, ...] = ()

    def _of(self, kind: type[Any]) -> tuple[Any, ...]:
        return tuple(r for r in self.records if isinstance(r, kind))

    @property
    def transcripts(self) -> tuple[Transcript, ...]:
        return self._of(Transcript)

    @property
    def turns(self) -> tuple[Turn, ...]:
        return self._of(Turn)

    @property
    def exchanges(self) -> tuple[Exchange, ...]:
        return self._of(Exchange)

    @property
    def stage_spans(self) -> tuple[StageSpan, ...]:
        return self._of(StageSpan)

    @property
    def tool_calls(self) -> tuple[ToolCallEvent, ...]:
        return self._of(ToolCallEvent)

    @property
    def usage_records(self) -> tuple[UsageRecord, ...]:
        return self._of(UsageRecord)

    @property
    def artifacts(self) -> tuple[Artifact, ...]:
        return self._of(Artifact)

    @property
    def cost_records(self) -> tuple[CostRecord, ...]:
        return self._of(CostRecord)


# ------------------------------------------------------------------------------------ helpers


def format_utc_instant(moment: datetime) -> str:
    """The RFC 3339 text of ``moment`` as written (no zone conversion), year zero padded.

    The C library year directive is platform dependent below year 1000, so fields are padded here.
    """
    return (
        f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}"
        f"T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}.{moment.microsecond:06d}Z"
    )


def is_int(value: Any) -> bool:
    return type(value) is int


def _token(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def pointer(*tokens: str | int) -> str:
    """An RFC 6901 pointer from tokens (keys are escaped)."""
    return "".join("/" + _token(str(t)) for t in tokens)


def source_token_safe(pointer_text: str) -> str:
    """A pointer of the record side as a part of a source pointer: '#' separates the parts of a
    chain, so a '#' inside a key is written ``%23`` and a literal '%' is written ``%25``."""
    return pointer_text.replace("%", "%25").replace("#", "%23")


def chain(*parts: str) -> str:
    """A source pointer that continues inside a JSON text: ``ptr#ptr2#ptr3``. The parts after
    the first are encoded with ``source_token_safe``; the first is already a source pointer."""
    return "#".join((parts[0], *(source_token_safe(p) for p in parts[1:]))) if parts else ""


def scalar_leaves(value: Any) -> Iterator[tuple[str, Any]]:
    """Scalar leaves of a JSON value with their pointers (null is a leaf, empty containers have
    none). Iterative, so a deep value cannot exhaust the interpreter stack."""
    stack: list[tuple[str, Any]] = [("", value)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, dict):
            stack.extend((f"{path}/{_token(str(k))}", v) for k, v in reversed(list(node.items())))
        elif isinstance(node, list):
            stack.extend((f"{path}/{i}", v) for i, v in reversed(list(enumerate(node))))
        else:
            yield path, node


def raw_text(value: Any) -> str:
    """The source value as sent, as text: a string as is, any other scalar as JSON. A lone
    surrogate is shown as U+FFFD (N82), because it cannot be stored."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return replace_lone_surrogates(text)


def floor_ms(seconds: Any) -> tuple[int, bool] | None:
    """Whole milliseconds of a non-negative finite number of seconds, truncated toward zero from
    the printed number, with whether digits were lost. ``None`` if it is not such a number."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    number = float(seconds)
    if not math.isfinite(number) or number < 0:
        return None
    scaled = Decimal(repr(number)) * 1000
    whole = int(scaled.to_integral_value(rounding=ROUND_FLOOR))
    return whole, scaled != whole


def unix_nano_to_utc_us(nanoseconds: Any) -> tuple[int, str, bool] | None:
    """(nanoseconds, UTC text with six fractional digits, whether digits were lost) for a
    non-negative integer count of nanoseconds (a digit string or an int), truncated toward the
    earlier microsecond; ``None`` if the value is not one or is out of range."""
    if isinstance(nanoseconds, str):
        if not (nanoseconds.isascii() and nanoseconds.isdigit()):
            return None
        count = int(nanoseconds)
    elif type(nanoseconds) is int and nanoseconds >= 0:
        count = nanoseconds
    else:
        return None
    try:
        moment = _EPOCH + timedelta(microseconds=count // 1000)
        text = format_utc_instant(moment)
    except (OverflowError, ValueError):
        return None
    return count, text, count % 1000 != 0


# ------------------------------------------------------------------------------------ drafts


@dataclass(frozen=True)
class Leg:
    """One trace entry in waiting: the pointer into the record's JSON dump, the source pointers
    and the derivation name (``None`` for a verbatim copy)."""

    pointer: str
    sources: tuple[str, ...] = ()
    derivation: str | None = None
    citation: Citation | None = None


class Draft:
    """A record in preparation. ``set`` records a field with the legs that explain it."""

    def __init__(
        self, cls: type[Any], fixed: Mapping[str, Any] | None = None, *, entity: bool = True
    ) -> None:
        self.cls = cls
        self.fixed: dict[str, Any] = dict(fixed or {})
        self.entity = entity
        self.values: dict[str, Any] = {}
        self.legs: dict[str, list[Leg]] = {}

    def set(self, name: str, value: Any, *legs: Leg) -> None:
        self.values[name] = value
        self.legs[name] = list(legs)

    def has(self, name: str) -> bool:
        return name in self.values


def _failing_fields(exc: ValueError) -> list[str] | None:
    errors = getattr(exc, "errors", None)
    if errors is None:
        return None
    try:
        located = errors(include_input=False, include_url=False)
    except TypeError:
        return None
    names: list[str] = []
    for item in located:
        loc = item.get("loc") or ()
        if not loc:
            return None
        names.append(str(loc[0]))
    return names


class Recorder:
    """What one converter run collects."""

    def __init__(self, fixture: str, created_at: str, privacy: Mapping[str, Any] | None) -> None:
        self.fixture = fixture
        self.created_at = created_at
        self.privacy = dict(privacy) if privacy is not None else None
        self.records: list[Any] = []
        self.channel_maps: list[ChannelMap] = []
        self.external_refs: list[ExternalRef] = []
        self.external_ref_sources: list[str] = []
        self.warnings: list[IngestWarning] = []
        self.gaps: list[GapRow] = []
        self.consumed: list[str] = []
        self.trace: list[TraceEntry] = []
        self._seen_warnings: set[tuple[str, str | None, str | None]] = set()
        self._seen_gaps: set[str] = set()
        self._seen_consumed: set[str] = set()

    # -- diagnostics ----------------------------------------------------------------------

    def warn(
        self, code: str, message: str, *, path: str | None = None, raw: str | None = None
    ) -> None:
        """An ingest warning. ``raw`` is the source value as sent; the same warning (code, path,
        raw) is recorded once."""
        shown_path = None if path is None else replace_lone_surrogates(path)
        shown_raw = None if raw is None else replace_lone_surrogates(raw)
        key = (code, shown_path, shown_raw)
        if key in self._seen_warnings:
            return
        self._seen_warnings.add(key)
        self.warnings.append(
            IngestWarning(code=code, path=shown_path, raw=shown_raw, message=message)
        )

    def gap(self, row: GapRow) -> None:
        if row.source_key in self._seen_gaps:
            return
        self._seen_gaps.add(row.source_key)
        self.gaps.append(row)

    def consume(self, *pointers: str) -> None:
        """Source pointers read as decision input and not copied."""
        for item in pointers:
            if item not in self._seen_consumed:
                self._seen_consumed.add(item)
                self.consumed.append(item)

    def external_ref(self, system: str, kind: str, value: str, source: str) -> bool:
        if replace_lone_surrogates(value) != value:
            self.warn(
                "text_not_valid_unicode",
                "an external identifier holds a lone surrogate and is not stored",
                path=source,
            )
            return False
        self.external_refs.append(ExternalRef(system=system, kind=kind, value=value))
        self.external_ref_sources.append(source)
        return True

    def text(self, value: str, source: str) -> tuple[str, str | None]:
        """(the text to store, the derivation if it differs from the source). A lone surrogate
        cannot be stored as UTF-8: it becomes U+FFFD and ``text_not_valid_unicode`` says where
        (N82)."""
        replaced = replace_lone_surrogates(value)
        if replaced == value:
            return value, None
        self.warn(
            "text_not_valid_unicode",
            "the text held a lone surrogate, stored with U+FFFD in its place",
            path=source,
            raw=replaced,
        )
        return replaced, "lone_surrogate_replaced"

    # -- records --------------------------------------------------------------------------

    def new_id(self, cls: type[Any]) -> Any:
        return id_type(cls.id_prefix).new()

    def commit(
        self, draft: Draft, *, entity_id: Any = None, where: str | None = None
    ) -> Any | None:
        """Build the record. A field the schema refuses and that is optional is dropped and
        warned; a record that cannot be built without it is dropped and warned. Returns the
        record or ``None``."""
        cls = draft.cls
        values = dict(draft.values)
        optional = {name for name, info in cls.model_fields.items() if not info.is_required()}
        base: dict[str, Any] = dict(draft.fixed)
        if draft.entity:
            base.update(
                id=entity_id if entity_id is not None else self.new_id(cls),
                schema_version=SCHEMA_VERSION,
                created_at=self.created_at,
            )
        caller = set(draft.fixed) | {"created_at"}
        built: Any = None
        for _ in range(len(values) + 1):
            try:
                built = cls(**base, **values)
            except ValueError as exc:
                bad = _failing_fields(exc)
                if set(bad or []) & caller:
                    raise  # the caller's own argument (uri, privacy, run_id): not content
                dropped = [n for n in (bad or []) if n in values and n in optional]
                if not dropped or len(dropped) != len(set(bad or [])):
                    self.warn(
                        "malformed_entry",
                        f"a {cls.__name__} could not be built from the source and was not stored",
                        path=where,
                    )
                    return None
                for name in dropped:
                    del values[name]
                    self.warn(
                        "malformed_entry",
                        f"{cls.__name__}.{name} is not valid for the schema and was not stored",
                        path=where,
                    )
                continue
            break
        if built is None:
            return None
        key = f"channel_map/{built.artifact_id}" if isinstance(built, ChannelMap) else str(built.id)
        for name, legs in draft.legs.items():
            if name not in values:
                continue
            for leg in legs:
                self.trace.append(
                    TraceEntry(key, leg.pointer, leg.sources, leg.derivation, leg.citation)
                )
        if isinstance(built, ChannelMap):
            self.channel_maps.append(built)
        else:
            self.records.append(built)
        return built

    def result(self) -> MappingResult:
        return MappingResult(
            fixture=self.fixture,
            records=tuple(self.records),
            channel_maps=tuple(self.channel_maps),
            external_refs=tuple(self.external_refs),
            external_ref_sources=tuple(self.external_ref_sources),
            warnings=tuple(self.warnings),
            gaps=tuple(self.gaps),
            consumed=tuple(self.consumed),
            trace=tuple(self.trace),
        )


def as_sequence(value: Any) -> Sequence[Any]:
    """``value`` if it is a list, else an empty list (tolerant reading of external documents)."""
    return value if isinstance(value, list) else []
