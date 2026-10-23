# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Fail if a source file lacks the SPDX Apache-2.0 header."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SPDX = "# SPDX-License-Identifier: Apache-2.0"
SUFFIXES = {".py", ".yml", ".yaml", ".toml", ".sh"}
HEADER_WINDOW = 3


def candidate_files(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    names = [n for n in out.decode("utf-8").split("\0") if n]
    return [root / n for n in names if Path(n).suffix in SUFFIXES and (root / n).is_file()]


def has_header(path: Path) -> bool:
    lines = path.read_text(encoding="utf-8").splitlines()[:HEADER_WINDOW]
    return any(line.strip() == SPDX for line in lines)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    missing = [p for p in candidate_files(root) if not has_header(p)]
    for p in missing:
        print(f"missing SPDX header: {p.relative_to(root)}", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
