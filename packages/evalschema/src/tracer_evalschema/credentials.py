# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The rule that flags secret-looking strings (ADR 0007 Decision 5a).

The rule decides which documents load (``metadata_flags`` are verified on load), so it is part of
the schema version: changing it is a schema change. It uses pattern and integer logic only, so
any language computes the same result. The credential patterns are our definition; whether they
match each vendor's current key format is not verified.
"""

from __future__ import annotations

import re

__all__ = [
    "CREDENTIAL_PATTERNS",
    "HIGH_ENTROPY_TOKEN",
    "KNOWN_CREDENTIAL_PREFIX",
    "URL_CREDENTIAL_PARAMETER_NAMES",
    "contains_credential_pattern",
    "credential_reason",
    "is_credential_parameter_name",
]

KNOWN_CREDENTIAL_PREFIX = "known_credential_prefix"
HIGH_ENTROPY_TOKEN = "high_entropy_token"  # noqa: S105 (a reason code, not a secret)

# One of these patterns, starting at the beginning of the string or after a character outside
# [A-Za-z0-9] (so "_" and "=" are boundaries, a letter or digit is not).
CREDENTIAL_PATTERNS: tuple[str, ...] = (
    r"sk-[A-Za-z0-9_-]{20,}",
    r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}",
    r"(?:AKIA|ASIA)[0-9A-Z]{16}",
    r"AIza[0-9A-Za-z_-]{35}",
    r"gh[pousr]_[A-Za-z0-9]{36,}",
    r"github_pat_[A-Za-z0-9_]{22,}",
    r"glpat-[A-Za-z0-9_-]{20,}",
    r"xox[abpors]-[A-Za-z0-9-]{10,}",
    r"hf_[A-Za-z0-9]{30,}",
    r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
)
_KNOWN_CREDENTIAL = re.compile(
    "(?<![A-Za-z0-9])(?:" + "|".join(CREDENTIAL_PATTERNS) + ")", re.ASCII
)

# A maximal run of these characters is a token candidate.
_TOKEN_RUN = re.compile(r"[A-Za-z0-9+/=_-]+", re.ASCII)
_MIN_TOKEN_LENGTH = 32
_MIN_DISTINCT_CHARACTERS = 16


def _is_high_entropy(run: str) -> bool:
    return (
        len(run) >= _MIN_TOKEN_LENGTH
        and len(set(run)) >= _MIN_DISTINCT_CHARACTERS
        and any("A" <= c <= "Z" for c in run)
        and any("a" <= c <= "z" for c in run)
        and any("0" <= c <= "9" for c in run)
    )


def contains_credential_pattern(text: str) -> bool:
    """True iff ``text`` contains a match of ``CREDENTIAL_PATTERNS`` at a boundary.

    This is the ``known_credential_prefix`` reason alone; the URL rules (ADR 0007 Decision 6b)
    and the ``config_ref`` check (Decision 1) use it, and never the high-entropy reason.
    """
    return _KNOWN_CREDENTIAL.search(text) is not None


def credential_reason(text: str) -> str | None:
    """Why ``text`` looks like a secret, or ``None``.

    ``known_credential_prefix`` if it contains a match of ``CREDENTIAL_PATTERNS`` at a boundary;
    otherwise ``high_entropy_token`` if a maximal run of ``[A-Za-z0-9+/=_-]`` is >= 32
    characters long, holds an upper-case letter, a lower-case letter and a digit, and has >= 16
    distinct characters. A known prefix wins when both match.
    """
    if contains_credential_pattern(text):
        return KNOWN_CREDENTIAL_PREFIX
    if any(_is_high_entropy(run.group()) for run in _TOKEN_RUN.finditer(text)):
        return HIGH_ENTROPY_TOKEN
    return None


# ------------------------------------------------------- credential parameter names in URLs

URL_CREDENTIAL_PARAMETER_NAMES: frozenset[str] = frozenset(
    {
        # P-20
        "token",
        "key",
        "api_key",
        "sig",
        "signature",
        "x-amz-signature",
        "x-amz-credential",
        # the Decision 3 denylist (P-60)
        "apikey",
        "access_token",
        "auth_token",
        "refresh_token",
        "client_secret",
        "password",
        "passwd",
        "secret",
        "bearer",
        "authorization",
        "credential",
        "credentials",
        "private_key",
        # cloud presign names (P-60)
        "x-amz-security-token",
        "x-goog-signature",
        "x-goog-credential",
        # API-key header names also sent as query parameters (N15)
        "x-api-key",
        "x-goog-api-key",
    }
)
"""The credential parameter names of ADR 0007 Decision 6b (25 names, stored case-folded)."""


def _comparable_name(name: str) -> str:
    return name.casefold().replace("-", "_")


_COMPARABLE_CREDENTIAL_NAMES = frozenset(
    _comparable_name(n) for n in URL_CREDENTIAL_PARAMETER_NAMES
)
if len(_COMPARABLE_CREDENTIAL_NAMES) != len(URL_CREDENTIAL_PARAMETER_NAMES):
    raise RuntimeError("two URL credential names are equal under '-' == '_'")  # pragma: no cover


def is_credential_parameter_name(decoded_name: str) -> bool:
    """True iff a percent-decoded parameter name is one of ``URL_CREDENTIAL_PARAMETER_NAMES``.

    The name is case-folded with ``str.casefold()`` and every ``-`` becomes ``_`` (one for one),
    the same as the set entries, and the two are compared as whole strings (ADR 0007 Decision 6b).
    """
    return _comparable_name(decoded_name) in _COMPARABLE_CREDENTIAL_NAMES
