# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Scaffold check so pytest collects; the real suite is written by qa."""

import tracer_evalschema


def test_package_importable() -> None:
    assert tracer_evalschema.__version__ == "0.0.1"
