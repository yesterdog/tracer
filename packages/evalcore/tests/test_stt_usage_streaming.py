# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch I, finding R1 of the P3-28 re-audit: the optional usage streaming boundary of evalcore.

Contract: docs/phase3/api-surface-batch-i.md section 1.1. ``tracer_evalcore.stt`` gains two names
and changes nothing else: ``UsageSink`` (a callable taking one ``UsageRecord``) and the runtime
checkable protocol ``UsageStreamingSttEngine`` (an ``SttEngine`` with a ``streams_usage`` flag and
``transcribe(plan, context, *, on_usage=None)``). ``SttEngine`` itself is unchanged, so every
engine written before batch I stays valid and is called as before. The module still imports no
network package.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from evalcore_batchd_support import FakeEngine  # type: ignore[import-not-found,unused-ignore]
from tracer_evalcore import stt


def test_the_module_exports_the_two_new_names_and_keeps_the_old_ones() -> None:
    for name in ("UsageSink", "UsageStreamingSttEngine"):
        assert name in stt.__all__, name
        assert hasattr(stt, name), name
    for name in ("SttEngine", "SttContext", "SttOutcome", "SttError", "SttResponseInvalid"):
        assert name in stt.__all__, name
    assert stt.__all__ == sorted(stt.__all__), "the list stays sorted"


def test_stt_engine_is_unchanged() -> None:
    parameters = list(inspect.signature(stt.SttEngine.transcribe).parameters)
    assert parameters == ["self", "plan", "context"]


def test_an_engine_of_the_old_protocol_is_an_stt_engine_and_not_a_streaming_one() -> None:
    engine = FakeEngine()
    assert isinstance(engine, stt.SttEngine)
    assert not isinstance(engine, stt.UsageStreamingSttEngine)


def test_an_engine_with_the_flag_and_the_keyword_is_a_streaming_engine() -> None:
    class Streaming:
        streams_usage = True

        def transcribe(self, plan: Any, context: Any, *, on_usage: Any = None) -> Any:
            raise NotImplementedError

    engine = Streaming()
    assert isinstance(engine, stt.SttEngine)
    assert isinstance(engine, stt.UsageStreamingSttEngine)


def test_the_streaming_protocol_needs_the_flag_not_only_the_method() -> None:
    class NoFlag:
        def transcribe(self, plan: Any, context: Any, *, on_usage: Any = None) -> Any:
            raise NotImplementedError

    assert isinstance(NoFlag(), stt.SttEngine)
    assert not isinstance(NoFlag(), stt.UsageStreamingSttEngine)


def test_the_streaming_transcribe_takes_on_usage_by_keyword_with_a_none_default() -> None:
    parameter = inspect.signature(stt.UsageStreamingSttEngine.transcribe).parameters["on_usage"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None


@pytest.mark.parametrize("name", ["httpx", "requests", "urllib3", "socket", "ssl", "aiohttp"])
def test_the_module_still_imports_no_network_package(name: str) -> None:
    tree = ast.parse(Path(stt.__file__).read_text("utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert name not in imported
