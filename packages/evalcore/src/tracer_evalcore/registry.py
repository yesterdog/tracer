# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The scorer registry (BL-144): which ``implementation_ref`` this package can run.

The scorer version is the distribution version of ``tracer-evalcore`` (ADR 0023 rule 3). A
definition has no scorer-version field, so only an unknown reference stops a run.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import tracer_evalcore
from tracer_evalcore.errors import InputRefusedError
from tracer_evalschema.metrics import MetricDefinition
from tracer_evalschema.results import ScorerInfo

__all__ = [
    "COUNT_KEY",
    "DEFAULT_REGISTRY",
    "MER_KEY",
    "SHARE_KEY",
    "SHIPPED_KEYS",
    "ScorerEntry",
    "ScorerRegistry",
    "UnknownScorerError",
    "check_runnable",
    "implementation_ref",
    "scorer_info",
]

MER_KEY = "stt_export_agreement_mer"
COUNT_KEY = "stt_export_flag_count"
SHARE_KEY = "stt_export_flagged_token_share"
SHIPPED_KEYS = (MER_KEY, COUNT_KEY, SHARE_KEY)


def implementation_ref(key: str) -> str:
    return f"builtin:{key}"


class UnknownScorerError(InputRefusedError):
    """A definition names a scorer that is not in the registry. The message holds no reference."""

    code = "unknown_scorer"


@dataclass(frozen=True)
class ScorerEntry:
    key: str
    implementation_ref: str
    version: str


class ScorerRegistry:
    """A fixed set of scorers by ``implementation_ref``."""

    def __init__(self, entries: Iterable[ScorerEntry]) -> None:
        self._entries = {entry.implementation_ref: entry for entry in entries}

    def get(self, ref: str) -> ScorerEntry:
        entry = self._entries.get(ref)
        if entry is None:
            raise UnknownScorerError("no scorer is registered for the implementation reference")
        return entry

    def __contains__(self, ref: object) -> bool:
        return ref in self._entries

    def refs(self) -> tuple[str, ...]:
        return tuple(self._entries)


DEFAULT_REGISTRY = ScorerRegistry(
    ScorerEntry(key, implementation_ref(key), tracer_evalcore.__version__) for key in SHIPPED_KEYS
)


def scorer_info(entry: ScorerEntry) -> ScorerInfo:
    return ScorerInfo(implementation_ref=entry.implementation_ref, version=entry.version)


def check_runnable(
    definitions: Sequence[MetricDefinition], registry: ScorerRegistry = DEFAULT_REGISTRY
) -> None:
    """Raise ``UnknownScorerError`` unless every definition that is not a judge has a scorer.

    The inert check of a recorded configuration is an ``llm_judge`` definition that names no
    scorer; it is never run.
    """
    for definition in definitions:
        if definition.kind == "llm_judge":
            continue
        ref = definition.implementation_ref
        if ref is None or ref not in registry:
            raise UnknownScorerError("a definition names a scorer that is not registered")
