# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-J03: the provider dossier ``docs/security/P4-J-providers.md`` (ADR 0030 rules 13 and 24;
contract docs/phase4/api-surface-batch-j1.md section 8).

One row per fact a profile depends on, each with its source URL and retrieval date or marked
``not verified``; the terms and data-use page of each provider is an owner gate, never an
assumption; the status of each profile in the dossier is the status the code ships; and no
provider SDK is a dependency (``httpx`` is reused).
"""

from __future__ import annotations

import importlib
import re
import tomllib
from pathlib import Path

import evalcore_phase4_adr_support as sup  # type: ignore[import-not-found,unused-ignore]

DOSSIER = sup.ROOT / "docs" / "security" / "P4-J-providers.md"
PROFILES = (
    "anthropic_compat/1",
    "gemini_compat/1",
    "xai/1",
    "openrouter/1",
    "sarvam_v2/1",
    "openai/1",
    "local_loopback/1",
)
PROVIDERS = ("anthropic", "gemini", "xai", "openrouter", "sarvam", "openai")
#: words that the ADR 0030 profile table puts on each profile; the dossier must carry them
REQUIRED_WORDS = {
    "anthropic_compat/1": (
        "response_format",
        "temperature",
        "anthropic-workspace-id",
        "Always empty",
    ),
    "gemini_compat/1": ("silently ignored", "still in beta", "parse"),
    "xai/1": ("json_schema", "Responses API"),
    "openrouter/1": ("allow_fallbacks", "data_collection", "Cost in credits", "strict"),
    "sarvam_v2/1": ("api-subscription-key", "reasoning_effort", "413", "model_overloaded", "FJ9"),
    "openai/1": ("max_completion_tokens", "Determinism is not guaranteed", "403"),
    "local_loopback/1": ("local server",),
}
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
URL = re.compile(r"https://[^\s;|]+")
SOURCES = re.compile(r"https://[^\s;|]+(; https://[^\s;|]+)*")


def _section(name: str) -> str:
    section: str = sup.heading_section(DOSSIER.read_text("utf-8"), name)
    return section


def _rows(section: str) -> list[list[str]]:
    rows = []
    for line in section.splitlines():
        if line.startswith("|") and not line.startswith("|---"):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    return rows[1:]  # the header row


def fact_rows() -> list[list[str]]:
    return _rows(_section("## Facts"))


def test_judge_provider_dossier_cites_url_and_date_per_fact() -> None:
    """Every fact row has a source URL and a retrieval date, or says ``not verified`` with neither;
    every profile has rows; the terms of each provider are an owner gate; no SDK dependency."""
    assert DOSSIER.is_file()
    text = DOSSIER.read_text("utf-8")
    assert text.startswith("<!-- SPDX-License-Identifier: Apache-2.0 -->")
    rows = fact_rows()
    assert len(rows) >= 25
    seen: dict[str, list[list[str]]] = {name: [] for name in PROFILES}
    for row in rows:
        assert len(row) == 5, row
        profile, fact, source, retrieved, verification = row
        assert profile.strip("`") in PROFILES, row
        assert len(fact) > 10, row
        assert verification in ("verified", "not verified"), row
        if verification == "verified":
            assert SOURCES.fullmatch(source), f"no source URL: {row}"
            assert DATE.fullmatch(retrieved), f"no retrieval date: {row}"
            assert retrieved >= "2026-10-28", row
        else:
            assert (source, retrieved) == ("none", "none"), f"an assumed source: {row}"
            assert not URL.search(fact + source), row
        seen[profile.strip("`")].append(row)
    for name, found in seen.items():
        assert found, f"profile {name} has no fact row"
        joined = " ".join(" ".join(r) for r in found)
        for word in REQUIRED_WORDS[name]:
            assert word in joined, (name, word)
    # the ADR marks these as not verified: they stay so
    not_verified = " ".join(" ".join(r) for r in rows if r[4] == "not verified")
    for fact in (
        "Whether `seed` is honoured",
        "usage fields",
        "error codes",
        "requirement is not verified",
        "was not read",
    ):
        assert fact in not_verified, fact

    # the terms and data-use page of each provider is an owner gate, never an assumption
    terms = _rows(_section("## Terms and data use (owner gate)"))
    by_provider = {row[0]: row for row in terms}
    assert tuple(by_provider) == PROVIDERS
    for provider, row in by_provider.items():
        assert len(row) == 3, row
        assert "not fetched" in row[1], provider
        assert "owner gate" in row[2], provider
        assert "O-J2" in row[2], provider
        assert not URL.search(" ".join(row)), f"{provider}: an assumed terms page"
    assert "BL-188" in by_provider["sarvam"][2]
    assert "10.5(a)" in by_provider["sarvam"][1]
    assert "BL-266" in text

    # no new runtime dependency: httpx is reused and no provider SDK is a dependency
    assert "httpx" in text
    sdks = {"openai", "anthropic", "google-genai", "google-generativeai", "xai-sdk", "litellm"}
    for pyproject in sorted((sup.ROOT / "packages").glob("*/pyproject.toml")):
        document = tomllib.loads(pyproject.read_text("utf-8"))
        names = {
            re.split(r"[<>=!~\[ ;]", dependency, maxsplit=1)[0].lower()
            for dependency in document["project"].get("dependencies", [])
        }
        assert not names & sdks, (pyproject.parent.name, names & sdks)
    lock = (sup.ROOT / "uv.lock").read_text("utf-8")
    for sdk in sorted(sdks):
        assert f'name = "{sdk}"' not in lock, sdk


def test_judge_profile_status_matches_dossier() -> None:
    """The status of each profile in the dossier is the status the code ships; a profile is
    ``verified`` only if every one of its fact rows is; Gemini and xAI are ``unverified`` (ADR 0030
    profile table); the loopback profile is not a real provider and is never refused for that."""
    profiles = importlib.import_module("tracer_evalproviders.judgeprofiles")
    table = _rows(_section("## Profile status"))
    status = {row[0].strip("`"): row[1] for row in table}
    assert tuple(status) == PROFILES
    assert set(status.values()) <= {"verified", "unverified"}
    assert dict(profiles.PROFILE_STATUS) == status, "the shipped status is the dossier's"
    assert tuple(profiles.PROFILE_NAMES) == PROFILES
    assert status["gemini_compat/1"] == "unverified"
    assert status["xai/1"] == "unverified"
    rows = fact_rows()
    for name in PROFILES:
        verifications = [r[4] for r in rows if r[0].strip("`") == name]
        expected = (
            "verified" if verifications and set(verifications) == {"verified"} else "unverified"
        )
        assert status[name] == expected, (
            f"{name}: status {status[name]} but its rows say {expected}"
        )
    # every profile cites the dossier it is backed by
    for name in PROFILES:
        profile = profiles.get_profile(name)
        assert profile.dossier == "docs/security/P4-J-providers.md", name
        assert profile.status == status[name]
    assert profiles.get_profile("local_loopback/1").real_provider is False
    assert all(
        profiles.get_profile(name).real_provider is True
        for name in PROFILES
        if name != "local_loopback/1"
    )
    assert Path(sup.ROOT / profiles.get_profile("xai/1").dossier).is_file()
