# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Entry point of the ``tracer`` console script (scaffold: help only)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="tracer",
        description="Tracer: score a recorded call by the agreement of two transcripts.",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help(sys.stderr)
    return 2
