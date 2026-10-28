# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""BL-354 / audit A-F5 and BL-355 / A-F6, judge side: a judge key variable cannot route another
provider's key, and the judge allowlist is read through the shared private-file reader.

Contract: docs/phase4/api-surface-batch-g2.md sections 3.2 and 4.3. The judge allowlist names, per
endpoint, the variable that holds its key. The only check was that one variable is not shared by
two hosts inside the file. A line that names ``SARVAM_API_KEY`` for another host would send the
Sarvam key there. Now a variable that belongs to another provider is refused for every host but
its own (``RESERVED_KEY_ENVS``), when the file is parsed (so before any DNS lookup and before any
key is read). Nothing here resolves a name or opens a socket.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from evalproviders_p4j_support import jmod  # type: ignore[import-not-found,unused-ignore]

SARVAM_URL = "https://api.sarvam.ai/v2/chat/completions"
OTHER_URL = "https://api.openai.com/v1/chat/completions"
SARVAM = "SARVAM_API_KEY"


def hosts() -> Any:
    return jmod("judgehosts")


def parse(text: str) -> Any:
    return hosts().parse_judge_allowlist(text)


def refusal(text: str) -> str:
    with pytest.raises(hosts().JudgeAllowlistError) as caught:
        parse(text)
    assert str(caught.value) == caught.value.code
    assert caught.value.__cause__ is None
    return str(caught.value.code)


def test_reserved_key_envs_are_a_public_closed_mapping() -> None:
    """BL-354 / A-F5 (3.2): the variables that belong to one provider and the hosts they may be
    routed to."""
    assert dict(hosts().RESERVED_KEY_ENVS) == {SARVAM: ("api.sarvam.ai",)}
    assert "RESERVED_KEY_ENVS" in hosts().__all__


def test_the_sarvam_key_variable_is_allowed_for_the_sarvam_host_only() -> None:
    """BL-354 / A-F5 (3.2): the one line that is allowed, in any letter case of the host."""
    (entry,) = parse(f"{SARVAM_URL} profile=sarvam_v2/1 key_env={SARVAM}")
    assert (entry.host, entry.key_env) == ("api.sarvam.ai", SARVAM)
    (upper,) = parse(
        f"HTTPS://API.SARVAM.AI/v2/chat/completions profile=sarvam_v2/1 key_env={SARVAM}"
    )
    assert upper.host == "api.sarvam.ai"
    (with_port,) = parse(
        f"https://api.sarvam.ai:8443/v2/chat/completions profile=sarvam_v2/1 key_env={SARVAM}"
    )
    assert with_port.port == 8443  # the rule is on the host, as the audit states it


@pytest.mark.parametrize(
    "url",
    [
        OTHER_URL,
        "https://openrouter.ai/api/v1/chat/completions",
        "https://api.x.ai/v1/chat/completions",
        "https://judge.example.test/v1/chat/completions",
        "https://api.sarvam.ai.evil.example.test/v2/chat/completions",
        "https://evil-api.sarvam.ai/v2/chat/completions",
        "https://sarvam.ai/v2/chat/completions",
        "https://api.sarvam.ai./v2/chat/completions",
        "https://8.8.8.8/v1/chat/completions",
    ],
)
def test_the_sarvam_key_variable_is_refused_for_every_other_host(url: str) -> None:
    """BL-354 / A-F5 (3.2): ``allowlist_key_env_reserved``; the host must be exactly the Sarvam
    one (a suffix, a prefix, a trailing dot and an address do not count)."""
    assert refusal(f"{url} profile=openai/1 key_env={SARVAM}") == "allowlist_key_env_reserved"


def test_the_sarvam_key_variable_is_refused_also_with_the_sarvam_profile_on_another_host() -> None:
    """BL-354 / A-F5 (3.2): the profile does not decide, the host does."""
    line = f"https://judge.example.test/v2/chat/completions profile=sarvam_v2/1 key_env={SARVAM}"
    assert refusal(line) == "allowlist_key_env_reserved"


def test_one_reserved_line_refuses_the_whole_file_before_the_sharing_check() -> None:
    """BL-354 / A-F5 (3.2): the line rule comes first: a file that routes the variable to two hosts
    is refused as reserved (not as shared), and the first fault refuses the file."""
    both = (
        f"{SARVAM_URL} profile=sarvam_v2/1 key_env={SARVAM}\n"
        f"{OTHER_URL} profile=openai/1 key_env={SARVAM}"
    )
    assert refusal(both) == "allowlist_key_env_reserved"
    second = (
        f"{OTHER_URL} profile=openai/1 key_env=JUDGE_KEY_A\n"
        f"https://openrouter.ai/api/v1/chat/completions profile=openrouter/1 key_env={SARVAM}"
    )
    assert refusal(second) == "allowlist_key_env_reserved"


def test_other_variable_names_and_sharing_inside_the_file_are_unchanged() -> None:
    """BL-354 / A-F5: names that merely resemble the Sarvam variable are ordinary; the batch J1 rule
    that one variable serves one host still holds."""
    for name in ("SARVAM_API_KEY2", "MY_SARVAM_API_KEY", "SARVAM_KEY", "OPENAI_API_KEY"):
        (entry,) = parse(f"{OTHER_URL} profile=openai/1 key_env={name}")
        assert entry.key_env == name
    shared = (
        f"{OTHER_URL} profile=openai/1 key_env=JUDGE_KEY_A\n"
        "https://openrouter.ai/api/v1/chat/completions profile=openrouter/1 key_env=JUDGE_KEY_A"
    )
    assert refusal(shared) == "allowlist_key_env_shared"


def test_a_comment_that_names_the_variable_is_not_an_entry() -> None:
    """BL-354 / A-F5: the rule is on fields, never on comment text."""
    text = f"{OTHER_URL} profile=openai/1 key_env=JUDGE_KEY_A  # not key_env={SARVAM} here\n"
    assert parse(text)[0].key_env == "JUDGE_KEY_A"


def test_the_refusal_comes_before_any_environment_read_or_dns_lookup(tmp_path: Path) -> None:
    """BL-354 / A-F5 (3.2): a refusal at the file leaves the environment and the resolver
    untouched: the reader only parses."""
    path = tmp_path / "judge-allowlist"
    path.write_text(f"{OTHER_URL} profile=openai/1 key_env={SARVAM}\n", "utf-8")
    path.chmod(0o600)
    with pytest.raises(hosts().JudgeAllowlistError) as caught:
        hosts().read_judge_allowlist(path)
    assert caught.value.code == "allowlist_key_env_reserved"


# ------------------------------------------------------------------------------------ 4.3


def good(directory: Path, name: str = "judge-allowlist") -> Path:
    path = directory / name
    path.write_text(f"{OTHER_URL} profile=openai/1 key_env=JUDGE_KEY_A\n", "utf-8")
    path.chmod(0o600)
    return path


def code_of(path: Path) -> str:
    with pytest.raises(hosts().JudgeAllowlistError) as caught:
        hosts().read_judge_allowlist(path)
    return str(caught.value.code)


def test_the_judge_reader_is_built_on_the_shared_private_file_reader() -> None:
    """BL-355 / A-F6 (4.3): the judge reader calls ``tracer_evalcore.privatefile.read_private_file``
    (the helper the target reader calls too) and keeps its codes."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(hosts().read_judge_allowlist))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "read_private_file" in called


def test_the_judge_reader_keeps_its_codes_through_the_shared_reader(tmp_path: Path) -> None:
    """BL-355 / A-F6 (4.3): behaviour of the batch J1 reader is unchanged (its own tests pin the
    rest); here every code of the shared reader is mapped with the ``allowlist_`` prefix."""
    path = good(tmp_path)
    assert len(hosts().read_judge_allowlist(path)) == 1
    path.chmod(0o640)
    assert code_of(path) == "allowlist_mode_too_open"
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    assert code_of(link) == "allowlist_not_a_regular_file"
    hard = tmp_path / "hard"
    os.link(path, hard)
    assert code_of(path) == "allowlist_not_a_regular_file"
    hard.unlink()
    assert code_of(tmp_path / "missing") == "allowlist_unreadable"
    big = tmp_path / "big"
    big.write_text("# " + "x" * 65_536 + "\n", "utf-8")
    big.chmod(0o600)
    assert code_of(big) == "allowlist_too_large"
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    assert code_of(fifo) == "allowlist_not_a_regular_file"
