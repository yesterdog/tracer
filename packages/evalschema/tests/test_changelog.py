# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""ADR 0008 invariant I20 (A33): the package CHANGELOG has an entry for the current schema version.

``packages/evalschema/CHANGELOG.md`` is shipped in the sdist (A30). Its entry for the version in
``tracer_evalschema.SCHEMA_VERSION`` must exist, and while no release tag exists for that version
the entry must say that the version is unreleased (nothing is published, no tag). The checks run
on the file and, so that they cannot pass vacuously, on small bad samples.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

import pytest

from ._support import CURRENT_SCHEMA_VERSION, PACKAGE_DIR, ROOT, api

CHANGELOG = PACKAGE_DIR / "CHANGELOG.md"
GIT = shutil.which("git") or "/usr/bin/git"
HEADING = re.compile(r"^##[ \t]+(?P<title>.+?)[ \t]*$", re.MULTILINE)
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
UNRELEASED = re.compile(r"\bunreleased\b|\bnot released\b", re.IGNORECASE)


@dataclass(frozen=True)
class Entry:
    """A level-2 section of a changelog that concerns one version."""

    heading: str
    body: str


def sections(text: str) -> list[Entry]:
    """The level-2 sections of ``text``: heading text and the text up to the next one."""
    found = list(HEADING.finditer(text))
    return [
        Entry(
            match.group("title"),
            text[match.end() : found[i + 1].start() if i + 1 < len(found) else len(text)],
        )
        for i, match in enumerate(found)
    ]


def entry_for(text: str, version: str) -> Entry | None:
    """The section for ``version``: a heading that names it (``## 0.0.1``, ``## [0.0.1] - date``,
    ``## v0.0.1``), or, in a changelog that has only an ``Unreleased`` section, that section when
    its text names the version. ``0.0.10`` is not ``0.0.1``."""
    named = re.compile(rf"(?<![0-9.]){re.escape(version)}(?![0-9]|\.[0-9])")
    for entry in sections(text):
        if named.search(entry.heading):
            return entry
    for entry in sections(text):
        if entry.heading.strip().lower().strip("[]") == "unreleased" and named.search(entry.body):
            return entry
    return None


def release_tags(version: str) -> list[str]:
    """The git tags that name ``version`` (``0.0.1`` or ``v0.0.1``); empty outside a checkout or
    in a clone without tags (a shallow CI clone: the check then cannot see a tag)."""
    result = subprocess.run(  # noqa: S603 - fixed executable and arguments, no shell
        [GIT, "tag", "--list", version, f"v{version}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.split() if result.returncode == 0 else []


def problems(text: str, version: str, tags: list[str]) -> list[str]:
    """Why ``text`` is not an acceptable changelog for ``version`` given the release ``tags``."""
    entry = entry_for(text, version)
    if entry is None:
        return [f"no entry for version {version}"]
    said_unreleased = bool(UNRELEASED.search(entry.heading + "\n" + entry.body))
    found: list[str] = []
    if tags and said_unreleased:
        found.append(f"the entry says unreleased but the tag {tags[0]} exists")
    if not tags and not said_unreleased:
        found.append("no release tag exists, so the entry must say the version is unreleased")
    if tags and not ISO_DATE.search(entry.heading):
        found.append("a released version's heading carries its release date (YYYY-MM-DD)")
    if not entry.body.strip():
        found.append("the entry is empty")
    return found


# ------------------------------------------------------------------------------ the real file


def test_changelog_has_entry_for_schema_version() -> None:
    """I20: the CHANGELOG has an entry for ``SCHEMA_VERSION`` (0.0.1). Also: the constant, the
    test helper and the package metadata agree on the version, so the entry cannot be for another
    number than the one the schemas carry."""
    version = api("").SCHEMA_VERSION
    assert version == CURRENT_SCHEMA_VERSION
    text = CHANGELOG.read_text(encoding="utf-8")
    entry = entry_for(text, version)
    assert entry is not None, f"CHANGELOG.md has no entry for schema version {version}"
    assert entry.body.strip(), "the entry for the schema version is empty"


def test_changelog_states_the_schema_version_is_unreleased_until_a_tag_exists() -> None:
    """A33: with no release tag for the version, the entry says in words that it is unreleased (the
    heading or its first lines: not released, no tag, nothing published); once a tag exists the
    heading carries a date and no longer says unreleased. Consistent with the policy text
    (``SCHEMA_POLICY.md``), which says the same of the schema files."""
    version = api("").SCHEMA_VERSION
    text = CHANGELOG.read_text(encoding="utf-8")
    assert problems(text, version, release_tags(version)) == []
    entry = entry_for(text, version)
    assert entry is not None
    if not release_tags(version):
        assert UNRELEASED.search(entry.heading), "the heading says (unreleased)"
        head = "\n".join(entry.body.strip().splitlines()[:6]).lower()
        assert "no version tag exists" in head
        assert "nothing is published" in head


def test_changelog_opening_defines_the_change_classes() -> None:
    """ADR 0008 Decision 4: every entry classifies changes as additive or breaking and says that
    a breaking change carries a migration note; the opening says so."""
    opening = CHANGELOG.read_text(encoding="utf-8").split("\n## ", 1)[0].lower()
    assert "additive" in opening
    assert "breaking" in opening
    assert "migration note" in opening


# ------------------------------------------------------------------------ self-tests (not vacuous)

GOOD_UNRELEASED = (
    "# Changelog\n\n## 0.0.1 (unreleased)\n\n**Status: not released.** No tag.\n\nNew.\n"
)
GOOD_RELEASED = "# Changelog\n\n## 0.0.1 - 2026-11-01\n\nFirst version.\n"


@pytest.mark.parametrize(
    ("text", "tags", "expected"),
    [
        (GOOD_UNRELEASED, [], []),
        (GOOD_RELEASED, ["v0.0.1"], []),
        ("# Changelog\n\nNothing yet.\n", [], ["no entry for version 0.0.1"]),
        ("# Changelog\n\n## 0.0.2 (unreleased)\n\nNew.\n", [], ["no entry for version 0.0.1"]),
        ("# Changelog\n\n## 0.0.10 (unreleased)\n\nNew.\n", [], ["no entry for version 0.0.1"]),
        ("# Changelog\n\n## 10.0.1\n\nNew.\n", [], ["no entry for version 0.0.1"]),
        (
            "# Changelog\n\n## 0.0.1\n\nFirst version.\n",
            [],
            ["no release tag exists, so the entry must say the version is unreleased"],
        ),
        (
            GOOD_UNRELEASED,
            ["0.0.1"],
            [
                "the entry says unreleased but the tag 0.0.1 exists",
                "a released version's heading carries its release date (YYYY-MM-DD)",
            ],
        ),
        (
            "# Changelog\n\n## 0.0.1 (unreleased)\n",
            [],
            ["the entry is empty"],
        ),
    ],
    ids=[
        "unreleased-without-tag",
        "released-with-tag",
        "no-entry",
        "another-version",
        "prefix-of-a-longer-version",
        "suffix-of-a-longer-version",
        "released-claim-without-tag",
        "unreleased-claim-with-tag",
        "empty-entry",
    ],
)
def test_changelog_checker_flags_bad_samples(
    text: str, tags: list[str], expected: list[str]
) -> None:
    assert problems(text, "0.0.1", tags) == expected


def test_changelog_checker_accepts_the_keep_a_changelog_forms() -> None:
    """The version may be named in a bracketed heading or in an ``Unreleased`` section."""
    bracketed = "# Changelog\n\n## [0.0.1] - 2026-11-01\n\nFirst.\n"
    assert problems(bracketed, "0.0.1", ["v0.0.1"]) == []
    unreleased = "# Changelog\n\n## Unreleased\n\nSchema version 0.0.1: first version.\n"
    assert problems(unreleased, "0.0.1", []) == []
    elsewhere = "# Changelog\n\n## Unreleased\n\nSomething else.\n"
    assert problems(elsewhere, "0.0.1", []) == ["no entry for version 0.0.1"]
    assert entry_for(CHANGELOG.read_text(encoding="utf-8"), "9.9.9") is None
