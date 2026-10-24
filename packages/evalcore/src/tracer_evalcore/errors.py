# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Error bases of the core. An input that is refused is a ValueError: the caller can fix it."""

from __future__ import annotations

__all__ = ["EvalcoreError", "InputRefusedError"]


class EvalcoreError(Exception):
    """Base of every error raised by tracer-evalcore."""


class InputRefusedError(EvalcoreError, ValueError):
    """An input was refused. Messages carry sizes, codes and pointers, never the input itself."""
