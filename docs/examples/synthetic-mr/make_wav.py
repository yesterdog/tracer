# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Write the synthetic recording of ``wav_recipe.json`` (standard library only).

Mono, 16-bit little-endian PCM. A tone segment holds a sine wave, a silence segment holds exact
zeros. Sample ``n`` of a tone is ``round(amplitude * sin(2 * pi * frequency_hz * n / rate))`` with
``n`` the index in the whole file. Only the exact zeros decide where the chunker cuts.

Usage: ``python make_wav.py OUT.wav [--recipe wav_recipe.json]``
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import wave
from array import array
from pathlib import Path
from typing import Any

DEFAULT_RECIPE = Path(__file__).resolve().parent / "wav_recipe.json"


def make_wav(recipe: dict[str, Any], path: Path) -> Path:
    """Write the recording described by ``recipe`` to ``path`` and return ``path``."""
    total: int = recipe["total_frames"]
    rate: int = recipe["sample_rate_hz"]
    pcm = array("h", bytes(2 * total))
    for segment in recipe["segments"]:
        if segment["kind"] != "tone":
            continue
        frequency: float = segment["frequency_hz"]
        amplitude: float = segment["amplitude"]
        for n in range(segment["start_frame"], segment["end_frame"]):
            pcm[n] = round(amplitude * math.sin(2 * math.pi * frequency * n / rate))
    if sys.byteorder == "big":
        pcm.byteswap()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(recipe["channels"])
        handle.setsampwidth(recipe["sample_width_bytes"])
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic Marathi recording.")
    parser.add_argument("out", type=Path, help="the WAV file to write")
    parser.add_argument("--recipe", type=Path, default=DEFAULT_RECIPE, help="the recipe JSON file")
    args = parser.parse_args(argv)
    recipe = json.loads(args.recipe.read_text(encoding="utf-8"))
    print(make_wav(recipe, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
