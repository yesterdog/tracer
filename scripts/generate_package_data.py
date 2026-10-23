# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Write the generated package data files of tracer-evalschema (never edit them by hand).

Generated today: ``data/bidi_markers.json`` from ``tracer_evalschema.text.marker_table_json()``
(ADR 0017 Decision 3a). ``--check`` exits 1 if a file differs from what would be written.

    uv run --locked python scripts/generate_package_data.py [--check]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from tracer_evalschema import text

DATA_DIR = Path(__file__).resolve().parent.parent / "packages/evalschema/src/tracer_evalschema/data"
GENERATED: dict[str, Callable[[], str]] = {"bidi_markers.json": text.marker_table_json}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report drift, write nothing")
    args = parser.parse_args(argv)
    stale = []
    for name, render in GENERATED.items():
        path = DATA_DIR / name
        wanted = render()
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == wanted:
            continue
        stale.append(name)
        if not args.check:
            path.write_text(wanted, encoding="utf-8", newline="\n")
    for name in stale:
        print(f"{'stale' if args.check else 'wrote'}: {name}", file=sys.stderr)
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    sys.exit(main())
