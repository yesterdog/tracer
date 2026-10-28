# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Stand-in for a human labeller, for the offline quick start only.

``python -I fill_labels.py PACKET LABELS`` reads a blind packet that ``tracer labels packet`` wrote
and writes a labels file (``tracer.judge_labels/1``) in which every item has the verdict
``agrees`` except the last, which stays ``null`` (unlabelled). Nothing is listened to: the file
only shows the shape of the loop. A real labeller listens to the agent's audio, reads the
question and the expected facts, and writes ``agrees``, ``disagrees`` or ``cannot_tell`` per item.
Standard library only; no network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: fill_labels.py PACKET LABELS", file=sys.stderr)
        return 64
    packet = json.loads(Path(argv[1]).read_text("utf-8"))
    labels = packet["labels_template"]
    for label in labels["labels"][:-1]:
        label["verdict"] = "agrees"
    Path(argv[2]).write_text(json.dumps(labels, indent=2) + "\n", "utf-8")
    print(f"labels: {argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
