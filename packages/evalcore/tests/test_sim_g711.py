# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-06 (G.711 part): ``test_g711_mulaw_alaw_decode_matches_itu_table`` (ADR 0027 rule 4, ADR 0024
amendment A-rule on telephony input; contract docs/phase4/api-surface-batch-a.md section 4.1).

PROVENANCE, stated plainly: the published ITU-T G.711 tables were NOT fetched (no network in this
task). The expected values come from two independent derivations written in the support module
(the segment formula of the standard for mu-law, and for A-law with its even-bit inversion), from
a short list of anchors the standard is known for (the two zeros of mu-law, the absence of a zero
in A-law, the full-scale values 32124 and 32256, the 8-step segment 0), and from a SHA-256 pin of
the whole table. If a reader checks the pin against the ITU document, this test becomes
verified; until then it is "derived twice, pinned". The 16-bit scaling is the conventional one
(mu-law 14-bit value times 4, A-law 13-bit value times 8).
"""

from __future__ import annotations

import hashlib
import itertools
import struct

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from evalcore_batcha_support import (  # type: ignore[import-not-found,unused-ignore]
    g711_mod,
    ref_alaw,
    ref_mulaw,
)

#: code -> decoded value, for the mu-law values the standard is known for
MULAW_ANCHORS = {
    0xFF: 0,  # positive zero
    0x7F: 0,  # negative zero
    0xFE: 8,
    0xFD: 16,
    0xFC: 24,
    0x7E: -8,
    0x80: 32124,  # positive full scale
    0x00: -32124,  # negative full scale
    0x81: 31100,
}
#: code -> decoded value, for A-law (no zero exists: the smallest magnitude is 8)
ALAW_ANCHORS = {
    0xD5: 8,
    0x55: -8,
    0xD4: 24,
    0xD7: 40,
    0xC5: 264,  # first value of segment 1
    0xAA: 32256,  # positive full scale
    0x2A: -32256,  # negative full scale
}
#: SHA-256 of ``",".join(str(v) for v in table)`` over the 256 entries, pinned from the two
#: derivations above (see the provenance note)
MULAW_SHA256 = "ad1a1412eb936fef4c750979bbd0187bb6ff8fe28e1816485ee8ff1df9a1308a"
ALAW_SHA256 = "7c9db214731218aee11c90c2ceaa17d381f2e229ed30424d228d996503ed5e64"

# Deterministic hypothesis profile (as Phase 1 to 3): derandomised, no deadline.
HSETTINGS = settings(
    max_examples=120,
    deadline=None,
    derandomize=True,
    phases=[Phase.explicit, Phase.generate],
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)


def _digest(values: tuple[int, ...] | list[int]) -> str:
    return hashlib.sha256(",".join(str(v) for v in values).encode("ascii")).hexdigest()


def test_g711_mulaw_alaw_decode_matches_itu_table() -> None:
    g = g711_mod()
    mu = list(g.MULAW_DECODE_TABLE)
    al = list(g.ALAW_DECODE_TABLE)
    assert len(mu) == len(al) == 256
    # the two derivations agree with the shipped tables, entry by entry
    assert mu == [ref_mulaw(c) for c in range(256)]
    assert al == [ref_alaw(c) for c in range(256)]
    # the anchors
    for code, value in MULAW_ANCHORS.items():
        assert mu[code] == value, hex(code)
    for code, value in ALAW_ANCHORS.items():
        assert al[code] == value, hex(code)
    # the pin
    assert _digest(mu) == MULAW_SHA256
    assert _digest(al) == ALAW_SHA256
    # the decoders produce the table, two little-endian bytes per input byte
    everything = bytes(range(256))
    assert g.decode_mulaw(everything) == struct.pack("<256h", *mu)
    assert g.decode_alaw(everything) == struct.pack("<256h", *al)
    for code in (0, 1, 127, 128, 254, 255):
        assert struct.unpack("<h", g.decode_mulaw(bytes([code])))[0] == mu[code]
        assert struct.unpack("<h", g.decode_alaw(bytes([code])))[0] == al[code]
    assert g.decode_mulaw(b"") == b""
    assert g.decode_alaw(b"") == b""
    assert g.decode_mulaw(bytearray(b"\xfe\x00")) == struct.pack("<2h", 8, -32124)
    # the types are bytes: nothing else is decoded
    for bad in ("ab", [1, 2], 5, None, 1.5):
        with pytest.raises(TypeError):
            g.decode_mulaw(bad)
        with pytest.raises(TypeError):
            g.decode_alaw(bad)


def test_g711_tables_have_the_structure_of_the_standard() -> None:
    """Properties of the standard that hold for a correct table whatever its derivation: the sign
    bit is the top bit and flips the sign; there are eight segments of sixteen steps, the step
    doubling from segment to segment; mu-law has two zeros and A-law none; everything fits in
    16 bits and the full scales are 32124 and 32256."""
    g = g711_mod()
    mu = g.MULAW_DECODE_TABLE
    al = g.ALAW_DECODE_TABLE
    for code in range(256):
        assert mu[code ^ 0x80] == -mu[code], hex(code)
        assert al[code ^ 0x80] == -al[code], hex(code)
        assert -32768 <= mu[code] <= 32767
        assert -32768 <= al[code] <= 32767
    assert sorted(c for c in range(256) if mu[c] == 0) == [0x7F, 0xFF]
    assert [c for c in range(256) if al[c] == 0] == []
    assert max(mu) == 32124
    assert min(mu) == -32124
    assert max(al) == 32256
    assert min(al) == -32256
    # positive mu-law magnitudes: code 0xFF is the smallest, 0x80 the largest, strictly increasing
    positive_mu = [mu[c] for c in range(0xFF, 0x7F, -1)]
    assert positive_mu == sorted(positive_mu)
    assert len(set(positive_mu)) == 128
    for segment in range(8):
        values = positive_mu[16 * segment : 16 * segment + 16]
        steps = {b - a for a, b in itertools.pairwise(values)}
        assert steps == {8 << segment}, (segment, steps)
    # A-law: magnitudes by code ^ 0x55 (sign bit set = positive), same segment doubling after 1
    positive_al = [al[c ^ 0x55] for c in range(0x80, 0x100)]
    assert positive_al == sorted(positive_al)
    assert len(set(positive_al)) == 128
    for segment in range(8):
        values = positive_al[16 * segment : 16 * segment + 16]
        steps = {b - a for a, b in itertools.pairwise(values)}
        assert steps == {16 << max(segment - 1, 0)}, (segment, steps)


@given(data=st.binary(max_size=200))
@HSETTINGS
def test_g711_decode_is_bytewise_and_length_doubling(data: bytes) -> None:
    """A frame decodes byte by byte (no state between bytes, no resampling, no smoothing)."""
    g = g711_mod()
    pairs = ((g.decode_mulaw, g.MULAW_DECODE_TABLE), (g.decode_alaw, g.ALAW_DECODE_TABLE))
    for decode, table in pairs:
        out = decode(data)
        assert len(out) == 2 * len(data)
        assert list(struct.unpack(f"<{len(data)}h", out)) == [table[b] for b in data]
