# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""BL-354 / audit A-F5, target side: ``auth_token_env`` cannot name the Sarvam key variable.

Contract: docs/phase4/api-surface-batch-g2.md section 3.1. The target allowlist lets the owner name
the variable that holds the agent's bearer token. A mistaken entry that names ``SARVAM_API_KEY``
would send the Sarvam key to the agent's host as ``Authorization: Bearer``. The file is refused as a
whole, with a stable code and no value, when it is parsed (so before any key is read). The check
against the key variables of the judge allowlist needs both files and is made by the command
(section 3.3, ``test_cli_audit_key_routing.py``). No test opens a socket.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from evalsim_support_imports import mod  # type: ignore[import-not-found,unused-ignore]

LINE = "ws://localhost/ws"


def al() -> Any:
    return mod("allowlist")


def refusal(text: str) -> str:
    with pytest.raises(al().AllowlistError) as caught:
        al().parse_allowlist(text)
    assert str(caught.value) == caught.value.code
    return str(caught.value.code)


def test_the_reserved_names_are_a_closed_public_tuple() -> None:
    """BL-354 / A-F5 (3.1): the names no ``auth_token_env`` may take."""
    assert al().RESERVED_AUTH_TOKEN_ENVS == ("SARVAM_API_KEY",)
    assert "RESERVED_AUTH_TOKEN_ENVS" in al().__all__


def test_the_sarvam_key_variable_is_refused_as_an_agent_token_variable() -> None:
    """BL-354 / A-F5 (3.1): ``allowlist_auth_env_reserved``, whatever else is on the line."""
    for line in (
        f"{LINE} auth_token_env=SARVAM_API_KEY",
        f"{LINE} origin=http://localhost:3000 auth_token_env=SARVAM_API_KEY",
        f"{LINE} auth_token_env=SARVAM_API_KEY bot_holds_telephony_credentials=false",
        "wss://agent.example.test/ws auth_token_env=SARVAM_API_KEY",
    ):
        assert refusal(line) == "allowlist_auth_env_reserved", line


def test_one_reserved_line_refuses_the_whole_file() -> None:
    """BL-354 / A-F5 (3.1): the first fault refuses the file, also when it is the second line."""
    text = f"{LINE}\nws://localhost:9000/ws auth_token_env=SARVAM_API_KEY\n"
    assert refusal(text) == "allowlist_auth_env_reserved"


def test_only_the_exact_name_is_reserved() -> None:
    """BL-354 / A-F5 (3.1): a longer or shorter name, and a comment, are not the Sarvam variable."""
    for name in ("SARVAM_API_KEY2", "MY_SARVAM_API_KEY", "SARVAM_API_KE", "SARVAM", "AGENT_BEARER"):
        (entry,) = al().parse_allowlist(f"{LINE} auth_token_env={name}")
        assert entry.auth_token_env == name
    assert (
        al()
        .parse_allowlist(f"{LINE}  # auth_token_env=SARVAM_API_KEY is not set here")[0]
        .auth_token_env
        is None
    )


def test_the_reserved_name_is_refused_when_the_file_is_read(tmp_path: Path) -> None:
    """BL-354 / A-F5 (3.1): through ``read_allowlist`` too, with the same code."""
    path = tmp_path / "allow.txt"
    path.write_text(f"{LINE} auth_token_env=SARVAM_API_KEY\n", "utf-8")
    path.chmod(0o600)
    with pytest.raises(al().AllowlistError) as caught:
        al().read_allowlist(path)
    assert caught.value.code == "allowlist_auth_env_reserved"
    assert "SARVAM" not in repr(caught.value).replace("allowlist_auth_env_reserved", "")
