# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The counts-only measurement script over the offline synthetic base case (gate condition C4).

Dev-written test of ``scripts/measurements/noise_floor.py`` (not a qa spec test). It scores the
invented Marathi pair offline (``--stt fake:<dir>``), runs the script on that store and checks
the counts the data README documents (8 flags, 9 of 120 tokens) and that the output holds only
ASCII counts. No network, no private data.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from cli_batchf_support import (  # type: ignore[import-not-found,unused-ignore]
    data_support,
    run_cli,
    score_argv,
)

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "measurements" / "noise_floor.py"


def _script() -> ModuleType:
    name = "measurements_noise_floor"
    found = sys.modules.get(name)
    if found is not None:
        return found
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _store(root: Path) -> Path:
    support = data_support()
    inputs = root / "in"
    inputs.mkdir()
    wav = support.make_wav(support.recipe(), inputs / "synthetic_mr.wav")
    export = inputs / "export.txt"
    export.write_bytes(support.data_path("export_synthetic_mr.txt").read_bytes())
    replay = support.write_replay_dir("base", root / "replay")
    out = root / "out"
    run = run_cli(score_argv(wav, export, out, stt=f"fake:{replay}"), out_dir=out, wav=wav)
    assert run.code == 0, run.err
    return out


def _cells(output: str) -> dict[str, list[str]]:
    table: dict[str, list[str]] = {}
    for line in output.splitlines():
        if " | " in line:
            name, *cells = (part.strip() for part in line.split(" | "))
            table[name] = cells
    return table


def test_measurement_scripts_reproduce_the_synthetic_base_case(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _store(tmp_path)
    script = _script()

    assert script.main(["report", f"base={out}", "--pair", "base,base"]) == 0
    printed = capsys.readouterr().out
    printed.encode("ascii")  # counts and labels only: no Marathi text can be in it
    table = _cells(printed)
    assert table["export tokens"] == ["120"]
    assert table["STT tokens"] == ["120"]
    assert table["MER (S+D+I) of (H+S+D+I)"][0].startswith("13 of 122")
    assert table["spans"] == ["10"]
    assert table["flagged spans"] == ["8"]
    assert table["flagged export tokens"] == ["9"]
    assert table["export turns with a flag"] == ["7 of 22"]
    assert table["flagged zero-width (insertion) spans"] == ["1"]
    assert "jaccard base vs base: 9 of 9;" in printed

    assert script.main(["ablate", f"base={out}"]) == 0
    ablation = capsys.readouterr().out
    ablation.encode("ascii")
    assert "ON tokens 120/120 MER 13 of 122 flags 8 flagged tokens 9" in ablation
    assert "self-check ON = stored: ok" in ablation


def test_measurement_script_refuses_a_directory_without_a_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _script().main(["report", f"gone={tmp_path}"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(tmp_path) not in captured.err
