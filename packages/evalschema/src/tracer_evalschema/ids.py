# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Typed identifiers, the frozen prefix registry and the UUIDv7 generator (ADR 0001).

Wire form: ``<prefix>_<uuid>``, a registered lowercase prefix and the canonical lowercase UUID
text, version 7, RFC 9562 variant. Each entity kind has its own ID class (``RunId``,
``EvaluatorId``, ...), so an ID of one kind is rejected in a field of another kind by pydantic
and by the JSON Schema ``pattern``. ``TypedId`` itself, used as an annotation, accepts an ID of
any registered kind (for generic references such as ``VersionedRef.id``).
"""

from __future__ import annotations

import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, ClassVar, Self, TypeVar

from pydantic import BaseModel, ConfigDict, GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

from tracer_evalschema._hiding import values_hidden
from tracer_evalschema.errors import ConfusablePrefixError
from tracer_evalschema.text import VerbatimText

# ------------------------------------------------------------------------------ the registry

ID_PREFIXES: Mapping[str, str] = MappingProxyType(
    {
        "prj": "Project",
        "agt": "AgentTarget",
        "per": "Persona",
        "tpf": "TestProfile",
        "evl": "Evaluator",
        "met": "MetricDefinition",
        "mbd": "MetricBinding",
        "rub": "Rubric",
        "snap": "ConfigSnapshot",
        "run": "Run",
        "art": "Artifact",
        "tsc": "Transcript",
        "turn": "Turn",
        "exc": "Exchange",
        "tcl": "ToolCallEvent",
        "spn": "StageSpan",
        "res": "MetricResult",
        "vrd": "RunVerdict",
        "dts": "Dataset",
        "dti": "DatasetItem",
        "pcat": "PricingCatalog",
        "use": "UsageRecord",
        "cst": "CostRecord",
        "csum": "CostSummary",
        "rbnd": "RunBundle",
        "exp": "ExportBundle",
    }
)
"""Prefix -> entity class name: the frozen table of ADR 0001 Decision 4 (read-only). The prefix
``pce`` was withdrawn in revision 15 (N47): ``PriceEntry`` is a value object of its catalog version,
named by ``entry_key`` (N61); ``pce`` is not reserved (detail D13 as corrected)."""

PREFIX_SYNTAX = re.compile("[a-z]{2,6}")
_UUID7_TEXT = "[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"


def wire_pattern(prefixes: str) -> str:
    """JSON Schema pattern of the wire form; ``prefixes`` is one prefix or an alternation."""
    return f"^{prefixes}_{_UUID7_TEXT}$"


# ---------------------------------------------------------------------------- UUIDv7 generator

_TAIL_BITS = 74  # rand_a (12 bits) and rand_b (62 bits), used together as one counter
_TAIL_LIMIT = 1 << _TAIL_BITS
_MAX_MS = (1 << 48) - 1


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


class Uuid7Generator:
    """Monotonic UUIDv7 source.

    Layout: RFC 9562 section 5.7 (48-bit Unix milliseconds, version 7, variant ``10``).
    Monotonicity: section 6.2, method 2 ("monotonic random"). The 74 bits after the version and
    variant are one counter: a fresh millisecond seeds it with 73 random bits (the top bit is
    zero, which leaves room to count), and every further value in the same millisecond adds a
    random increment of 1 to 2**32. A clock that stands still or goes backwards reuses the last
    millisecond, and if the counter space runs out the millisecond is moved forward by one, so
    the values of one generator are strictly increasing. Thread-safe.
    """

    def __init__(self, clock_ms: Callable[[], int] | None = None) -> None:
        self._clock_ms = clock_ms or _wall_clock_ms
        self._lock = threading.Lock()
        self._last_ms = -1
        self._tail = 0

    def next(self) -> uuid.UUID:
        with self._lock:
            now = self._clock_ms()
            if now > self._last_ms:
                self._last_ms, self._tail = now, secrets.randbits(_TAIL_BITS - 1)
            else:
                self._tail += 1 + secrets.randbits(32)
                if self._tail >= _TAIL_LIMIT:
                    self._last_ms, self._tail = self._last_ms + 1, secrets.randbits(_TAIL_BITS - 1)
            ms, tail = self._last_ms, self._tail
        if not 0 <= ms <= _MAX_MS:
            raise ValueError(f"timestamp {ms} ms does not fit the 48 bits of a UUIDv7")
        rand_a, rand_b = tail >> 62, tail & ((1 << 62) - 1)
        return uuid.UUID(int=(ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b)


_SHARED_GENERATOR = Uuid7Generator()


def uuid7() -> uuid.UUID:
    """A fresh UUIDv7 from the process-wide generator (real clock)."""
    return _SHARED_GENERATOR.next()


# ------------------------------------------------------------------------------- typed IDs


def _is_uuid7(value: uuid.UUID) -> bool:
    return value.version == 7 and value.variant == uuid.RFC_4122


class TypedId:
    """An identifier of one entity kind: a UUIDv7 behind a registered prefix.

    Not a ``str`` and not a ``uuid.UUID``: the UUID is the attribute ``.uuid`` and ``str()``
    gives the wire form. Two IDs are equal only if they are of the same class and have the same
    UUID, and an ID never equals its wire string. Subclasses set ``prefix``.
    """

    __slots__ = ("_uuid",)
    prefix: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        prefix = cls.__dict__.get("prefix")
        if not isinstance(prefix, str) or PREFIX_SYNTAX.fullmatch(prefix) is None:
            raise TypeError(f"{cls.__name__}.prefix must be 2-6 lowercase letters: {prefix!r}")

    def __init__(self, value: uuid.UUID) -> None:
        if type(self) is TypedId:
            raise TypeError("TypedId is abstract; use the ID class of a kind (RunId, ...)")
        if type(value) is not uuid.UUID or not _is_uuid7(value):
            raise ValueError(f"{type(self).__name__} needs a version 7 uuid.UUID, got {value!r}")
        self._uuid = value

    @property
    def uuid(self) -> uuid.UUID:
        return self._uuid

    @classmethod
    def new(cls) -> Self:
        """A fresh ID of this kind."""
        return cls(uuid7())

    @classmethod
    def parse(cls, wire: str) -> Self:
        """Parse the wire form of this kind (``ValueError`` if it is not exactly that form)."""
        if type(wire) is not str or _WIRE_REGEX[cls.prefix].fullmatch(wire) is None:
            raise ValueError(f"{wire!r} is not a {cls.__name__} ({wire_pattern(cls.prefix)})")
        return cls(uuid.UUID(wire.partition("_")[2]))

    def __str__(self) -> str:
        return f"{self.prefix}_{self._uuid}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}('{self}')"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TypedId) or type(other) is not type(self):
            return NotImplemented
        return self._uuid == other._uuid

    def __hash__(self) -> int:
        return hash((self.prefix, self._uuid))

    # -- pydantic integration ---------------------------------------------------------------

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler) -> CoreSchema:
        kinds: tuple[type[TypedId], ...] = (
            (cls,) if cls is not TypedId else tuple(_ID_TYPES.values())
        )
        from_wire = core_schema.no_info_after_validator_function(
            cls._from_wire, core_schema.str_schema(strict=True)
        )
        return core_schema.json_or_python_schema(
            json_schema=from_wire,
            python_schema=core_schema.union_schema(
                [*(core_schema.is_instance_schema(kind) for kind in kinds), from_wire],
                mode="left_to_right",
            ),
            serialization=core_schema.plain_serializer_function_ser_schema(str, when_used="json"),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "string", "pattern": cls._schema_pattern()}

    @classmethod
    def _schema_pattern(cls) -> str:
        if cls is TypedId:
            return wire_pattern("(?:" + "|".join(_ID_TYPES) + ")")
        return wire_pattern(cls.prefix)

    @classmethod
    def _from_wire(cls, wire: str) -> TypedId:
        prefix = wire.partition("_")[0]
        kind = _ID_TYPES.get(prefix) if cls is TypedId else cls
        if kind is None or kind.prefix != prefix or _WIRE_REGEX[prefix].fullmatch(wire) is None:
            if values_hidden():  # ADR 0007 Decision 8 (c): the rule and the form, not the value
                raise PydanticCustomError(
                    "typed_id",
                    "not a valid ID of this kind (expected {pattern})",
                    {"pattern": cls._schema_pattern()},
                )
            raise PydanticCustomError(
                "typed_id",
                "{value} is not a valid ID of this kind (expected {pattern})",
                {"value": repr(wire), "pattern": cls._schema_pattern()},
            )
        return kind(uuid.UUID(wire.partition("_")[2]))


class ProjectId(TypedId):
    prefix = "prj"


class AgentTargetId(TypedId):
    prefix = "agt"


class PersonaId(TypedId):
    prefix = "per"


class TestProfileId(TypedId):
    prefix = "tpf"


class EvaluatorId(TypedId):
    prefix = "evl"


class MetricDefinitionId(TypedId):
    prefix = "met"


class MetricBindingId(TypedId):
    prefix = "mbd"


class RubricId(TypedId):
    prefix = "rub"


class ConfigSnapshotId(TypedId):
    prefix = "snap"


class RunId(TypedId):
    prefix = "run"


class ArtifactId(TypedId):
    prefix = "art"


class TranscriptId(TypedId):
    prefix = "tsc"


class TurnId(TypedId):
    prefix = "turn"


class ExchangeId(TypedId):
    prefix = "exc"


class ToolCallEventId(TypedId):
    prefix = "tcl"


class StageSpanId(TypedId):
    prefix = "spn"


class MetricResultId(TypedId):
    prefix = "res"


class RunVerdictId(TypedId):
    prefix = "vrd"


class DatasetId(TypedId):
    prefix = "dts"


class DatasetItemId(TypedId):
    prefix = "dti"


class PricingCatalogId(TypedId):
    prefix = "pcat"


class UsageRecordId(TypedId):
    prefix = "use"


class CostRecordId(TypedId):
    prefix = "cst"


class CostSummaryId(TypedId):
    prefix = "csum"


class RunBundleId(TypedId):
    prefix = "rbnd"


class ExportBundleId(TypedId):
    prefix = "exp"


_ID_TYPES: Mapping[str, type[TypedId]] = MappingProxyType(
    {cls.prefix: cls for cls in TypedId.__subclasses__()}
)
_WIRE_REGEX: Mapping[str, re.Pattern[str]] = MappingProxyType(
    {prefix: re.compile(wire_pattern(prefix)[1:-1]) for prefix in _ID_TYPES}
)

if {prefix: cls.__name__ for prefix, cls in _ID_TYPES.items()} != {
    prefix: f"{entity}Id" for prefix, entity in ID_PREFIXES.items()
}:
    raise RuntimeError("the ID classes do not match ID_PREFIXES")  # pragma: no cover


def id_type(prefix: str) -> type[TypedId]:
    """The ID class of a registered prefix (``KeyError`` for an unregistered one)."""
    try:
        return _ID_TYPES[prefix]
    except KeyError:
        raise KeyError(f"unregistered ID prefix {prefix!r}") from None


# ------------------------------------------------------------------------- new prefixes (P-03)


def _levenshtein(left: str, right: str) -> int:
    row = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        diagonal, row[0] = row[0], i
        for j, right_char in enumerate(right, 1):
            diagonal, row[j] = (
                row[j],
                min(row[j] + 1, row[j - 1] + 1, diagonal + (left_char != right_char)),
            )
    return row[-1]


def validate_new_prefix(prefix: str, existing: Mapping[str, str] | None = None) -> None:
    """Check a prefix proposed for a new entity kind (ADR 0001 Decision 4, P-03).

    It must be 2-6 lowercase ASCII letters (``ValueError`` otherwise), not registered, at
    Levenshtein distance >= 2 from every registered prefix and not an anagram of one
    (``ConfusablePrefixError`` otherwise). ``existing`` defaults to ``ID_PREFIXES``.
    """
    if not isinstance(prefix, str) or PREFIX_SYNTAX.fullmatch(prefix) is None:
        raise ValueError(f"an ID prefix is 2-6 lowercase ASCII letters: {prefix!r}")
    registry = ID_PREFIXES if existing is None else existing
    if prefix in registry:
        raise ConfusablePrefixError(f"prefix {prefix!r} is already registered ({registry[prefix]})")
    for other in registry:
        if _levenshtein(prefix, other) == 1:
            raise ConfusablePrefixError(f"prefix {prefix!r} is one edit away from {other!r}")
        if sorted(prefix) == sorted(other):
            raise ConfusablePrefixError(f"prefix {prefix!r} is an anagram of {other!r}")


# ---------------------------------------------------------------------- external identifiers

IdT = TypeVar("IdT", bound=TypedId)


class ExternalRef(BaseModel):
    """An identifier assigned by another system, kept as data (never as our ID).

    All three parts are stored exactly as given (ADR 0001 Decision 7).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    system: VerbatimText
    kind: VerbatimText
    value: VerbatimText


def from_external(
    id_type: type[IdT], *, system: str, kind: str, value: str
) -> tuple[IdT, ExternalRef]:
    """Mint a fresh ID of ``id_type`` for an externally identified object.

    Returns the new ID and the ``ExternalRef`` the caller records on the owning entity. Pure: no
    lookup and no storage. De-duplicating a re-import is the importer's job, done before this
    call (P-02).
    """
    return id_type.new(), ExternalRef(system=system, kind=kind, value=value)
