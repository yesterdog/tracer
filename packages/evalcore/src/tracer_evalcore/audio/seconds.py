# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Exact durations of audio (frames divided by a sample rate) as decimals, or nothing."""

from __future__ import annotations

from decimal import Decimal
from math import gcd

__all__ = ["MAX_FRACTIONAL_DIGITS", "exact_seconds"]

MAX_FRACTIONAL_DIGITS = 18
"""The schema's decimal string carries at most this many fractional digits."""


def _count_factor(value: int, factor: int) -> tuple[int, int]:
    count = 0
    while value % factor == 0:
        value //= factor
        count += 1
    return count, value


def exact_seconds(frames: int, rate: int) -> Decimal | None:
    """``frames / rate`` as a Decimal that equals the quotient exactly, or ``None``.

    ``None`` when the quotient has no finite decimal expansion (the reduced denominator has a
    prime factor other than 2 and 5) or needs more than 18 fractional digits. The result is never
    rounded and never passes through a float.
    """
    if type(frames) is not int:
        raise TypeError("frames must be an int")
    if type(rate) is not int:
        raise TypeError("rate must be an int")
    if frames < 0:
        raise ValueError("frames must be zero or more")
    if rate <= 0:
        raise ValueError("rate must be above zero")
    divisor = gcd(frames, rate)
    numerator, denominator = frames // divisor, rate // divisor
    twos, rest = _count_factor(denominator, 2)
    fives, rest = _count_factor(rest, 5)
    if rest != 1:
        return None
    digits = max(twos, fives)
    if digits > MAX_FRACTIONAL_DIGITS:
        return None
    scaled = numerator * 10**digits // denominator
    return Decimal(f"{scaled}E-{digits}")
