# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The store of a scored run: a SQLite file in the output directory, through tracer-evaldb (P3-21).

This is the only module of Phase 3 that imports ``tracer_evaldb``. The engine is returned as an
opaque value (``Any``): ``tracer_cli`` imports no database library, so a caller passes the value
back and never looks inside it. The store holds the call text, so the file is created readable by
its owner only (evaldb) and a new output directory is private too.
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import tracer_evaldb
from tracer_evalschema.agent import AgentTarget
from tracer_evalschema.bundle import RunBundle
from tracer_evalschema.ids import RunBundleId
from tracer_evalschema.project import Project

__all__ = [
    "STORE_FILE_NAME",
    "StoredRun",
    "load_run",
    "open_sqlite_store",
    "store_rescoring",
    "store_run",
]

STORE_FILE_NAME = "tracer.sqlite"


@dataclass(frozen=True)
class StoredRun:
    """What ``store_run`` did: the bundle and, per table, the rows it added."""

    bundle_id: RunBundleId
    created: bool
    inserted: dict[str, int]


def open_sqlite_store(out_dir: str | os.PathLike[str]) -> Any:
    """Create ``out_dir`` if needed, open its SQLite store and migrate it to the current head."""
    directory = Path(out_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.path.islink(directory / STORE_FILE_NAME):
        raise OSError(errno.ELOOP, "the store file is a link")
    path = (directory / STORE_FILE_NAME).resolve()
    if "?" in str(path):
        raise ValueError("the output directory path must not contain a question mark")
    engine = tracer_evaldb.create_store_engine(f"sqlite:///{path}")
    try:
        tracer_evaldb.migrate.upgrade(engine)
        tracer_evaldb.open_store(engine)
    except BaseException:
        engine.dispose()
        raise
    return engine


def store_run(
    engine: Any, *, project: Project, agent_target: AgentTarget, bundle: RunBundle
) -> StoredRun:
    """Store the project, the agent target, the snapshot and the bundle, in that order.

    Each write is idempotent for equal content, so storing the same bundle again changes nothing.
    """
    tracer_evaldb.store_project(engine, project)
    tracer_evaldb.store_definition(engine, agent_target)
    tracer_evaldb.store_snapshot(engine, bundle.snapshot)
    result = tracer_evaldb.store_run_bundle(engine, bundle)
    return StoredRun(cast(RunBundleId, bundle.id), result.created, dict(result.inserted))


def load_run(engine: Any, bundle_id: RunBundleId) -> RunBundle:
    """The stored bundle, byte for byte as it was stored."""
    loaded = tracer_evaldb.load_run_bundle(engine, bundle_id)
    if loaded.bundle is None:
        raise RuntimeError("the store returned an incomplete run bundle")
    return loaded.bundle


def store_rescoring(engine: Any, bundle: RunBundle) -> StoredRun:
    """Store a rescoring bundle of a run that is already stored (ADR 0022 rule 5): the project,
    the target and the snapshot are in the store, and every shared record is stored once."""
    result = tracer_evaldb.store_run_bundle(engine, bundle)
    return StoredRun(cast(RunBundleId, bundle.id), result.created, dict(result.inserted))
