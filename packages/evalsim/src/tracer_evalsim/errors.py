# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Error base of the simulator package."""

from __future__ import annotations

from tracer_evalcore.errors import EvalcoreError

__all__ = ["EvalsimError"]


class EvalsimError(EvalcoreError):
    """Base of every error raised by tracer-evalsim."""
