# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-37 and P4-J30: the CHANGELOGs and the READMEs of the Phase 4 packages say what the packages
gained, and say it without a claim.

Not a SCOPE test name; it checks the parts of the two tasks that the named tests do not reach:

* the CHANGELOGs of ``tracer-evalsim``, ``tracer-cli``, ``tracer-evalcore`` and
  ``tracer-evalproviders`` keep their ``0.0.1 (unreleased)`` entry and "not released" status (the
  schema package's changelog test is ``packages/evalschema/tests/test_changelog.py``) and each names
  the Phase 4 features of its package (``REQUIRED``);
* ``packages/evalsim/README.md`` is no longer "scaffold only": it states the role of the package,
  its three dialects, the offline fake agent, the safety files, what it does not do and where the
  quick start and the concept pages are;
* the CLI README links both concept pages and the example inputs;
* no text of these five files holds a private path, a secret-shaped string or a language code
  outside the nine supported ones, and none claims anything the concept pages do not claim.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from evalcore_batchh_support import sections  # type: ignore[import-not-found,unused-ignore]
from evalcore_p4docs_support import (  # type: ignore[import-not-found,unused-ignore]
    CLAIM_WORDS,
    language_problems,
    private_and_secret_problems,
)

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "uv.lock").is_file())
PACKAGES = ROOT / "packages"
HEADING = re.compile(r"^##[ \t]+(?P<title>.+?)[ \t]*$", re.M)

#: phrases (regular expressions, case-insensitive) that each CHANGELOG must contain
REQUIRED: dict[str, tuple[str, ...]] = {
    "evalsim": (
        r"pipecat-protobuf/1",
        r"twilio-media-stream/1",
        r"smallwebrtc/1",
        r"fake agent",
        r"allowlist",
        r"kill-switch|kill switch",
        r"call\.lock|call lock",
        r"watchdog",
        r"paced sender|paced",
        r"tracer simulate",
        r"packages/evalsim/README\.md",
        r"docs/concepts/simulated-calls\.md",
    ),
    "cli": (
        r"tracer simulate",
        r"tracer judge",
        r"tracer labels",
        r"--agent fake:",
        r"--judge fake:|--judge.*fake:",
        r"exit code 5|5 .{0,40}call not established|call not established",
        r"authorisation reference.{0,120}sha256|sha256.{0,120}authorisation reference",
        r"BL-363",
        r"BL-350",
        r"Simulate quick start",
        r"Judge quick start",
        r"docs/concepts/simulated-calls\.md",
        r"docs/concepts/llm-judge\.md",
        r"docs/examples/simulated-mr",
    ),
    "evalcore": (
        r"tracer\.sim_profile/1",
        r"tracer\.sim_outcome/1",
        r"tracer\.timing/1",
        r"call state machine",
        r"turn-taking|turn taking",
        r"tracer\.golden_set/1",
        r"tracer\.judge_config/1",
        r"tracer\.judge/1",
        r"tracer\.judge_labels/1",
        r"answer window",
        r"uncalibrated",
        r"docs/concepts/llm-judge\.md",
        r"docs/concepts/simulated-calls\.md",
    ),
    "evalproviders": (
        r"text-to-speech|TTS",
        r"chat completions",
        r"content-addressed",
        r"anthropic_compat/1",
        r"local_loopback/1",
        r"allowlist",
        r"exchange files?",
        r"replay:",
        r"fake:",
        r"unverified",
        r"docs/security/P4-J-providers\.md",
        r"never follows redirects|follow_redirects=False|no redirects",
    ),
}
CHANGELOG_FILES = tuple(REQUIRED)
SIM_TITLE = "Simulate quick start (offline, no key, no network)"
JUDGE_TITLE = "Judge quick start (offline, no key, no network)"
EXIT_TITLE = "Exit codes of tracer simulate, tracer judge and tracer labels"


def _changelog(name: str) -> str:
    return (PACKAGES / name / "CHANGELOG.md").read_text("utf-8")


@pytest.mark.parametrize("name", CHANGELOG_FILES)
def test_changelog_keeps_its_unreleased_entry_and_names_the_phase4_features(name: str) -> None:
    text = _changelog(name)
    titles = [m.group("title") for m in HEADING.finditer(text)]
    assert "0.0.1 (unreleased)" in titles, titles
    entry = text[text.index("## 0.0.1 (unreleased)") :]
    assert re.search(r"Status: not released", entry), "the status line must stay"
    assert re.search(r"No version tag exists and nothing is published to PyPI", entry)
    missing = missing_phrases(text, REQUIRED[name])
    assert missing == [], missing


@pytest.mark.parametrize("name", CHANGELOG_FILES)
def test_changelog_has_no_private_path_secret_or_foreign_language_code(name: str) -> None:
    text = _changelog(name)
    assert private_and_secret_problems(text) == []
    assert language_problems(text) == []


def test_changelog_requirement_table_is_well_formed() -> None:
    for patterns in REQUIRED.values():
        for pattern in patterns:
            re.compile(pattern)
        assert len(set(patterns)) == len(patterns)


def missing_phrases(text: str, patterns: tuple[str, ...]) -> list[str]:
    return [p for p in patterns if not re.search(p, text, re.I)]


def test_the_changelog_check_has_teeth() -> None:
    """Mutants: a changelog from which a feature phrase is removed is refused by the same rule."""
    for name, patterns in REQUIRED.items():
        text = _changelog(name)
        for pattern in patterns:
            stripped = re.sub(pattern, "", text, flags=re.I)
            assert pattern in missing_phrases(stripped, patterns), (name, pattern)


# ------------------------------------------------------------------------------ the READMEs

EVALSIM_README = PACKAGES / "evalsim" / "README.md"
CLI_README = PACKAGES / "cli" / "README.md"

EVALSIM_REQUIRED = (
    r"^# tracer-evalsim\n",
    r"\bsimulated caller\b",
    r"pipecat-protobuf/1",
    r"twilio-media-stream/1",
    r"smallwebrtc/1",
    r"fake agent|--agent fake:",
    r"kill-switch|kill switch",
    r"call\.lock",
    r"allowlist",
    r"\bnot (?:a|an) (?:tool|agent|evaluator)|does not (?:evaluate|score|judge)",
    r"packages/cli/README\.md|\.\./cli/README\.md",
    r"docs/concepts/simulated-calls\.md|\.\./\.\./docs/concepts/simulated-calls\.md",
    r"docs/security/P4-02-licenses\.md",
    r"websockets>=15\.0,<18",
    r"aiortc>=1\.15\.0,<2",
)


def test_evalsim_readme_is_not_a_scaffold_any_more() -> None:
    text = EVALSIM_README.read_text("utf-8")
    assert not re.search(r"scaffold only", text, re.I)
    missing = [p for p in EVALSIM_REQUIRED if not re.search(p, text, re.I | re.M)]
    assert missing == [], missing
    assert private_and_secret_problems(text) == []
    assert language_problems(text) == []
    for target in re.findall(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)\)", text):
        if not re.match(r"[a-z]+:", target):
            assert (EVALSIM_README.parent / target.split("#")[0]).resolve().exists(), target


def test_evalsim_readme_makes_no_claim_word() -> None:
    text = EVALSIM_README.read_text("utf-8")
    assert CLAIM_WORDS.findall(text) == []


def test_cli_readme_links_the_concept_pages_and_the_examples() -> None:
    text = CLI_README.read_text("utf-8")
    for target in (
        "../../docs/concepts/simulated-calls.md",
        "../../docs/concepts/llm-judge.md",
        "../../docs/examples/simulated-mr/README.md",
        "../evalsim/README.md",
    ):
        assert f"]({target}" in text, target
    assert private_and_secret_problems(text) == []
    assert language_problems(text) == []


def test_cli_readme_new_sections_make_no_claim_word() -> None:
    body = sections(CLI_README.read_text("utf-8"))
    for title in (SIM_TITLE, JUDGE_TITLE, EXIT_TITLE):
        assert title in body, title
        text = re.sub(r"```.*?```", "", body[title], flags=re.S)
        # the exit code lines quote the code's own help text ("speech-to-text failed"); they are
        # checked against the code by test_the_readme_exit_list_of_the_new_commands_...
        text = "\n".join(line for line in text.splitlines() if not re.match(r"- `\d+`: ", line))
        assert CLAIM_WORDS.findall(text) == [], title
