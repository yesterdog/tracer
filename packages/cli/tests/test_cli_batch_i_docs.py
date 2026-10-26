# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Batch I, the documents the re-audit found overstated (R3, R4) and the contract names (R1, R6).

Contract: docs/phase3/api-surface-batch-i.md section 7. The checks read files only; nothing is
imported from the product. A document may not say that a directory the command creates is
private as a whole (only the leaf is forced to 0700), and the documents that describe the
recovery must name the streaming interface and the Ctrl-C case they now rely on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _root()
CONCEPT = ROOT / "docs" / "concepts" / "recorded-call-scoring.md"
ADR = ROOT / "docs" / "adr" / "0024-provider-adapters-byok-sarvam-rest.md"
README = ROOT / "packages" / "cli" / "README.md"
OVERSTATEMENTS = (
    "A directory the command creates is readable by you only (mode 0700)",
    "a directory the command creates is 0700.",
)


def _text(path: Path) -> str:
    return " ".join(path.read_text("utf-8").split())


def _run_book(text: str) -> str:
    start = text.index("## Run book for a real send (P3-30)")
    end = text.index("## Ambiguities resolved", start)
    return text[start:end]


@pytest.mark.parametrize("path", [CONCEPT, ADR], ids=["concept page", "ADR 0024"])
def test_r3_the_documents_say_the_leaf_directory_is_0700_and_the_parents_follow_the_umask(
    path: Path,
) -> None:
    text = _text(path)
    assert "the leaf directory is 0700" in text
    assert "follow the umask" in text
    for old in OVERSTATEMENTS:
        assert old not in text, f"{path.name} still says: {old}"


def test_r3_the_concept_page_says_an_existing_out_earns_a_warning() -> None:
    text = _text(CONCEPT)
    assert "an existing `--out`" in text
    assert "group or other" in text
    assert "warning" in text


def test_r3_the_run_book_says_who_creates_the_out_directory_and_what_the_code_does() -> None:
    book = _run_book(_text(ADR))
    assert "the leaf directory is 0700" in book
    assert "follow the umask" in book
    assert "warning" in book
    assert "group or other" in book
    for old in OVERSTATEMENTS:
        assert old not in book


def test_r1_adr_0024_names_the_streaming_interface_and_the_ctrl_c_case() -> None:
    text = _text(ADR)
    for needle in ("on_usage", "streams_usage", "KeyboardInterrupt"):
        assert needle in text, needle
    rule16 = text[text.index("16. **The dry run, and the gates before a send.**") :]
    rule16 = rule16[: rule16.index("17. ")] if "17. " in rule16 else rule16
    assert "KeyboardInterrupt" in rule16
    assert "usage recovery: not written" in rule16


def test_r6_adr_0024_names_the_refusal_of_a_linked_store() -> None:
    text = _text(ADR)
    assert "store_file_is_a_link" in text
    assert "tracer.sqlite" in text


def test_r4_the_readme_exit_row_promises_the_recovery_only_where_the_code_gives_it() -> None:
    text = README.read_text("utf-8")
    rows = [line for line in text.splitlines() if re.match(r"\| 1 \|", line)]
    assert len(rows) == 1, rows
    row = rows[0]
    assert "usage-recovery-<UTC>.json" in row
    assert "when a chunk was answered" in row
    # a Ctrl-C is not an exit code; the row must not promise one
    assert "130" not in row
