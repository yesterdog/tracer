# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Shared helpers of the Phase 3 batch D tests of ``tracer-cli`` (the store, P3-21).

Contract: docs/phase3/api-surface-batch-d.md section 11. The store tests need a scored run, and
the fake engine and the synthetic call live with the evalcore tests
(``packages/evalcore/tests/evalcore_batchd_support.py``); that file is loaded here by its path
(the evalschema precedent for the F8 mapper), so the two directories share one fake and cannot
drift. Nothing of ``tracer_cli`` is imported at module level: before the module exists every
test fails on its own with ``ModuleNotFoundError`` and the directory still collects.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

TESTS = Path(__file__).resolve().parent
ROOT = next(p for p in TESTS.parents if (p / "uv.lock").is_file())
EVALCORE_SUPPORT = ROOT / "packages" / "evalcore" / "tests" / "evalcore_batchd_support.py"
CLI_SRC = ROOT / "packages" / "cli" / "src" / "tracer_cli"
POSTGRES_URL_ENV = "TRACER_TEST_POSTGRES_URL"


def store_mod() -> ModuleType:
    return importlib.import_module("tracer_cli.store")


def evalcore_support() -> ModuleType:
    """The helpers of the evalcore batch D tests, loaded once by file path."""
    name = "evalcore_batchd_support"
    found = sys.modules.get(name)
    if found is not None:
        return found
    spec = importlib.util.spec_from_file_location(name, EVALCORE_SUPPORT)
    assert spec is not None, EVALCORE_SUPPORT
    assert spec.loader is not None, EVALCORE_SUPPORT
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Scored:
    """One run of the pipeline and the project and agent target it names."""

    def __init__(self, run: Any, project: Any, agent_target: Any) -> None:
        self.run = run
        self.bundle = run.bundle
        self.project = project
        self.agent_target = agent_target


def score(
    tmp_path: Path,
    *,
    config: Any = None,
    project: Any = None,
    agent_target: Any = None,
    **options: Any,
) -> Scored:
    support = evalcore_support()
    definitions = importlib.import_module("tracer_evalcore.definitions")
    project = project or definitions.recorded_project(
        name="store test", created_at=support.CREATED_AT
    )
    agent_target = agent_target or definitions.recorded_agent_target(
        name="store test agent", created_at=support.CREATED_AT
    )
    run = support.run_pipeline(
        tmp_path, config=config, project=project, agent_target=agent_target, **options
    )
    return Scored(run, project, agent_target)
