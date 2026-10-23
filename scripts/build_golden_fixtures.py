# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Generate the golden and frozen fixtures of the evalschema package (P1-16).

The fixtures are built from the models and the test helpers by pure functions of the code: every
random source is replaced by a seeded one (``_support_b9.scope``), so two runs give the same bytes.
The committed files must equal this script's output; ``test_golden_fixtures.py`` checks that, so a
change of a model or a helper shows up as a diff of the fixtures.

    uv run python scripts/build_golden_fixtures.py --check     # default: report drift, exit 1
    uv run python scripts/build_golden_fixtures.py --write     # rewrite the committed files
    uv run python scripts/build_golden_fixtures.py --out DIR   # write a copy under DIR

Files written: ``golden/<Class>/valid|invalid/*.json`` and ``frozen/0.0.1/*.json`` under the
fixtures directory. ``README.md`` files and ``frozen/0.0.1/schema.lock`` are never touched.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parent.parent
PACKAGE: Final = ROOT / "packages" / "evalschema"
FIXTURES: Final = PACKAGE / "tests" / "fixtures"

for entry in (PACKAGE / "src", PACKAGE):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))


def render() -> dict[str, bytes]:
    """Relative path (below the fixtures directory) -> bytes of every generated file."""
    support: Any = importlib.import_module("tests._support_b9")
    rendered: dict[str, bytes] = dict(support.render_all().files)
    return rendered


def existing(root: Path) -> set[str]:
    """Generated files below ``root`` (never README.md or schema.lock)."""
    found: set[str] = set()
    for top in ("golden", "frozen"):
        directory = root / top
        if directory.is_dir():
            for path in directory.rglob("*.json"):
                found.add(path.relative_to(root).as_posix())
    return found


def compare(root: Path, files: dict[str, bytes]) -> list[str]:
    """Problems between the files below ``root`` and the generated ones (empty when equal)."""
    problems: list[str] = []
    on_disk = existing(root)
    for name in sorted(files):
        path = root / name
        if name not in on_disk:
            problems.append(f"[missing] {name}")
        elif path.read_bytes() != files[name]:
            problems.append(f"[changed] {name}")
    for name in sorted(on_disk - set(files)):
        problems.append(f"[stale] {name}")
    return problems


def write(root: Path, files: dict[str, bytes]) -> None:
    for name in sorted(existing(root) - set(files)):
        (root / name).unlink()
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_bytes() != data:
            path.write_bytes(data)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report drift (default)")
    mode.add_argument("--write", action="store_true", help="rewrite the committed fixtures")
    mode.add_argument("--out", type=Path, help="write a copy of the fixtures under this directory")
    args = parser.parse_args(argv)
    files = render()
    if args.out is not None:
        write(args.out, files)
        print(f"wrote {len(files)} files under {args.out}")
        return 0
    if args.write:
        write(FIXTURES, files)
        print(f"wrote {len(files)} files under {FIXTURES.relative_to(ROOT)}")
        return 0
    problems = compare(FIXTURES, files)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(
            f"{len(problems)} problems; run with --write after reviewing the change",
            file=sys.stderr,
        )
        return 1
    print(f"{len(files)} files equal the generator output")
    return 0


if __name__ == "__main__":
    sys.exit(main())
