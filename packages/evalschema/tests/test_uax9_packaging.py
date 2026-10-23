# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-04e: the test-only UAX #9 reference never ships in the wheel or the sdist."""

from __future__ import annotations

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[list[str], list[str]]:
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "hatchling", "build", "-d", str(out)],
        cwd=PACKAGE_DIR,
        check=True,
        capture_output=True,
    )
    wheels = sorted(out.glob("*.whl"))
    sdists = sorted(out.glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1
    with zipfile.ZipFile(wheels[0]) as zf:
        wheel_names = zf.namelist()
    with tarfile.open(sdists[0]) as tf:
        sdist_names = tf.getnames()
    return wheel_names, sdist_names


def test_uax9_not_in_wheel_or_sdist(built: tuple[list[str], list[str]]) -> None:
    wheel, sdist = built
    assert any(n.startswith("tracer_evalschema/") for n in wheel), "the wheel was built"
    assert any(n.endswith("/pyproject.toml") for n in sdist), "the sdist was built"
    for name in (*wheel, *sdist):
        assert "uax9" not in name.lower(), name
        assert "/tests/support/" not in name, name


def test_uax9_not_in_wheel_contents_or_dependencies(built: tuple[list[str], list[str]]) -> None:
    wheel, sdist = built
    assert not any(n.endswith("DerivedBidiClass.txt") for n in (*wheel, *sdist))
    assert not any(n.endswith("BidiBrackets.txt") for n in (*wheel, *sdist))
