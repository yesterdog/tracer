# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Compare the vendored Unicode 16.0.0 Bidi_Class data with an interpreter's ``unicodedata``.

The interpreter may ship an older or a newer Unicode than the vendored data. Which differences are
acceptable depends on that, and the rule lives here so that it can be tested with injected
mismatches. ``verdict`` never relaxes anything for the equal-version case.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import cache
from typing import NamedTuple

from . import ucd

LINE = re.compile(r"([0-9A-F]{4,6})(?:\.\.([0-9A-F]{4,6}))?\s*;\s*\w+\s*#\s*([A-Z][a-z&])(?!\w)")


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


@cache
def assigned_in_data() -> frozenset[int]:
    """Code points whose general category in the vendored 16.0.0 file is not Cn.

    DerivedBidiClass.txt lists every assigned code point explicitly with its general category in
    the trailing comment; unassigned ones are covered by ``@missing`` lines or carry ``Cn``.
    """
    out: set[int] = set()
    text = (ucd.DATA_DIR / "DerivedBidiClass.txt").read_text(encoding="utf-8")
    for line in text.splitlines():
        match = LINE.match(line)
        if match and match.group(3) != "Cn":
            lo = int(match.group(1), 16)
            hi = int(match.group(2) or match.group(1), 16)
            out.update(range(lo, hi + 1))
    return frozenset(out)


class Differences(NamedTuple):
    """Both-assigned code points whose class differs, split by whether 16.0.0 assigns them."""

    changed: frozenset[int]  # assigned in the 16.0.0 data and in the interpreter
    newly_assigned: frozenset[
        int
    ]  # unassigned (Cn) in the 16.0.0 data, assigned in the interpreter


def differences(
    ours: Callable[[int], str],
    theirs_class: Callable[[int], str],
    theirs_category: Callable[[int], str],
) -> Differences:
    assigned = assigned_in_data()
    changed: set[int] = set()
    newly: set[int] = set()
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        theirs = theirs_class(cp)
        if not theirs or theirs_category(cp) == "Cn" or ours(cp) == theirs:
            continue
        (changed if cp in assigned else newly).add(cp)
    return Differences(frozenset(changed), frozenset(newly))


def verdict(
    interpreter_version: str,
    found: Differences,
    *,
    changed_in_16_0: frozenset[int],
    changed_in_newer: frozenset[int],
) -> list[int]:
    """Code points whose difference is not acceptable (empty means the data agrees).

    * equal version: nothing is acceptable, full strength.
    * older interpreter: only the reviewed ``changed_in_16_0`` list.
    * newer interpreter: code points not assigned in 16.0.0 (newly assigned there) are not
      comparable, and a class change of an assigned code point is acceptable only if it is in the
      reviewed ``changed_in_newer`` list.
    """
    here, there = version_tuple(ucd.UNICODE_VERSION), version_tuple(interpreter_version)
    everything = found.changed | found.newly_assigned
    if there == here:
        return sorted(everything)
    if there < here:
        return sorted(everything - changed_in_16_0)
    return sorted(found.changed - changed_in_newer)
