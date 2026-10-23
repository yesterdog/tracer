# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Fail if a private fixture or a local env file is tracked, staged or in history."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PRIVATE_PREFIX = "packages/evalschema/tests/fixtures/private/"


def _is_env_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (name == ".env" or name.startswith(".env.")) and name != ".env.example"


def _git(root: Path, *args: str) -> list[str]:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True)
    if proc.returncode != 0:
        return []
    return [n for n in proc.stdout.decode("utf-8").split("\0") if n]


def offending_paths(root: Path) -> list[str]:
    found: set[str] = set()
    # The index holds both tracked and staged paths.
    found.update(_git(root, "ls-files", "-z", "--cached"))
    found.update(_git(root, "diff", "-z", "--cached", "--name-only"))
    found.update(_git(root, "ls-tree", "-r", "-z", "--name-only", "HEAD"))
    found.update(_git(root, "log", "--all", "-z", "--format=", "--name-only"))
    return sorted(n for n in found if n.startswith(PRIVATE_PREFIX) or _is_env_file(n))


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    bad = offending_paths(root)
    for n in bad:
        print(f"private fixture or env file tracked, staged or in history: {n}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
