# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Gate P2-G finding m1: the bytes of every shipped Alembic revision file are pinned.

The storage policy says a shipped revision is never regenerated. Editing
``0001_initial_schema.py`` together with ``tables.py`` kept every other test green; this pin makes
that edit fail. The pin is ``tests/fixtures/revision_files_sha256.json`` (revision id -> SHA-256 of
the file bytes on disk, plus the file name). Helper: ``tests/evaldb_revision_pin.py``.

Rules (they are not a formality):

* An existing pin NEVER changes. A mistake in a shipped revision is fixed by a NEW revision (and, if
  data must move, a data migration under ADR 0021), not by editing the shipped file. A change of an
  existing pin needs a decision-log entry and a named spec change reviewed by ``staff-reviewer``;
  the helper refuses to do it.
* Adding the pin of a NEW revision: when a new file appears in
  ``src/tracer_evaldb/migrations/versions`` (module-level ``revision = "<id>"``), run
  ``uv run python packages/evaldb/tests/evaldb_revision_pin.py --write`` once the file is final
  (it adds only the missing pins and exits 1 without writing if an existing one differs), read the
  diff of the JSON (exactly one new line in each of ``files`` and ``sha256`` per new revision) and
  commit it together with the revision. Until the revision is committed it may still change;
  after it is committed (and certainly after any release) it is frozen.
* Every file of the versions directory except ``__init__.py`` needs a pin and every pin needs its
  file. Test-only revisions live in temporary directories (see ``test_evaldb_migrations.py``), never
  in this directory, so there is no exemption list.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import evaldb_revision_pin as pin  # type: ignore[import-not-found,unused-ignore]


def test_shipped_revision_files_are_pinned_by_sha256() -> None:
    problems = pin.problems(pin.VERSIONS, pin.read_pin())
    assert not problems, (
        "\n".join(problems)
        + "\nSee the docstring of tests/test_evaldb_revision_files.py before touching the pin."
    )


def _scratch_copy(tmp_path: Path) -> Path:
    target = tmp_path / "versions"
    shutil.copytree(pin.VERSIONS, target, ignore=shutil.ignore_patterns("__pycache__"))
    return target


def test_an_unchanged_copy_of_the_revisions_agrees_with_the_pin(tmp_path: Path) -> None:
    assert pin.problems(_scratch_copy(tmp_path), pin.read_pin()) == []


def test_a_mutated_copy_of_a_revision_file_fails_the_pin(tmp_path: Path) -> None:
    copy = _scratch_copy(tmp_path)
    first = copy / "0001_initial_schema.py"
    first.write_bytes(first.read_bytes() + b"\n")  # a single byte
    found = pin.problems(copy, pin.read_pin())
    assert len(found) == 1
    assert "0001" in found[0]
    assert "differs" in found[0]


def test_a_new_revision_file_without_a_pin_fails(tmp_path: Path) -> None:
    copy = _scratch_copy(tmp_path)
    (copy / "0003_new.py").write_text('revision = "0003"\ndown_revision = "0002"\n', "utf-8")
    found = pin.problems(copy, pin.read_pin())
    assert len(found) == 1
    assert "0003" in found[0]
    assert "no pin" in found[0]


def test_a_pinned_revision_file_that_is_missing_fails(tmp_path: Path) -> None:
    copy = _scratch_copy(tmp_path)
    (copy / "0002_access_pattern_indexes.py").unlink()
    found = pin.problems(copy, pin.read_pin())
    assert len(found) == 1
    assert "0002" in found[0]
    assert "missing" in found[0]


def test_a_deleted_pin_fails(tmp_path: Path) -> None:
    document = pin.read_pin()
    del document["sha256"]["0002"]
    found = pin.problems(_scratch_copy(tmp_path), document)
    assert len(found) == 1
    assert "0002" in found[0]
    assert "no pin" in found[0]


def test_the_helper_adds_a_missing_pin_and_refuses_to_change_an_existing_one(
    tmp_path: Path,
) -> None:
    copy = _scratch_copy(tmp_path)
    pin_file = tmp_path / "pin.json"
    shutil.copy(pin.PIN_FILE, pin_file)
    before = pin_file.read_bytes()
    assert pin.write_missing(copy, pin_file) == 0
    assert pin_file.read_bytes() == before
    (copy / "0003_new.py").write_text('revision = "0003"\n', "utf-8")
    assert pin.write_missing(copy, pin_file) == 0
    assert pin.problems(copy, pin.read_pin(pin_file)) == []
    assert {"0001", "0002", "0003"} == set(pin.read_pin(pin_file)["sha256"])
    pinned = pin_file.read_bytes()
    (copy / "0001_initial_schema.py").write_bytes(b"revision = '0001'\n")
    assert pin.write_missing(copy, pin_file) == 1
    assert pin_file.read_bytes() == pinned
