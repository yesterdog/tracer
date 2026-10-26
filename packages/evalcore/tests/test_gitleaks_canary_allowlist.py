# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Gate condition C12(a): the canary allowlist of ``.gitleaks.toml`` is anchored to the canary
token shape. The test canary of the key-handling tests is a made-up string
``CANARYKEY-<6 to 8 lowercase letters or digits>-<6 lowercase letters or digits>``; the evidence
logs echo it inside a quoted string (``PI_KEY': 'CANARYKEY-...'``). The allowlist regex may accept
only a match that ends with that quoted token, so that a real secret that happens to carry the
prefix (``xCANARYKEY-...`` or a long tail) is still reported. Contract:
docs/phase3/api-surface-batch-j.md section 4.1.

gitleaks compares the regex with the text of the rule match (``regexTarget = "match"``) using Go
RE2: the regex below uses no look-around and no back-reference, so Python ``re`` reads it the same
way (the RE2 end-of-text anchor is spelled differently in Python; the test translates it).
The pinned binary check of the lead (scan of the tracked tree and of the history) is recorded in
the contract; this file needs no binary.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
CONFIG = ROOT / ".gitleaks.toml"
CANARY_SHAPE = re.compile(r"CANARYKEY-[a-z0-9]{6,8}-[a-z0-9]{6}")
#: The reviewed line (contract section 4.1), as the TOML value between ''' and '''.
REVIEWED_REGEX = r"""['"]CANARYKEY-[a-z0-9]{6,8}-[a-z0-9]{6}['"]\z"""
EVIDENCE_PATHS = r"^docs/phase3/evidence/(green|red)/[^/]+/[^/]+\.(log|txt)$"


def _made_up(first: str, second: str) -> str:
    """A canary-shaped string built from parts, so that this file holds no secret-looking text."""
    return "-".join(["CANARYKEY", first, second])


#: Strings of the shape of a real credential, built from parts for the same reason.
DECOY_A = "".join(["AK", "IA", "0123456789", "ABCDEF"])
DECOY_B = "_".join(["sk", "live", "".join(["0123456789", "abcdefghij"])])
RE2_UNSUPPORTED = re.compile(r"\(\?[=!<]|\\[1-9]|\(\?P=|\(\?\(")


def _canary_allowlist() -> dict[str, Any]:
    data = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    found = [a for a in data["allowlists"] if "canary" in str(a.get("description", "")).lower()]
    assert len(found) == 1, f"{len(found)} canary allowlists in .gitleaks.toml"
    allowlist: dict[str, Any] = found[0]
    return allowlist


def _regexes() -> list[str]:
    regexes = _canary_allowlist()["regexes"]
    assert isinstance(regexes, list)
    assert len(regexes) == 1
    return [str(r) for r in regexes]


def _const(node: ast.expr) -> Any:
    """The value of a constant expression made of strings, lists and ``"-".join([...])`` calls."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List | ast.Tuple):
        return [_const(e) for e in node.elts]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and isinstance(node.func.value, ast.Constant)
        and len(node.args) == 1
    ):
        return str(node.func.value.value).join(_const(node.args[0]))
    raise AssertionError(f"not a constant expression: {ast.unparse(node)}")


def _canaries() -> list[str]:
    """The canary constants of the tests: the value of each ``KEY_CANARY`` / ``CANARY_KEY``."""
    found: list[str] = []
    for path in sorted((ROOT / "packages").glob("*/tests/*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(t, ast.Name) and t.id in ("KEY_CANARY", "CANARY_KEY")
                for t in node.targets
            ):
                continue
            value = _const(node.value)
            assert isinstance(value, str)
            found.append(value)
    return found


def regex_problems(regex: str, canaries: list[str]) -> list[str]:
    """Why this allowlist regex is not anchored to the quoted canary shape."""
    problems: list[str] = []
    if RE2_UNSUPPORTED.search(regex):
        problems.append("uses a construct that Go RE2 does not support")
    pattern = re.compile(regex.replace(r"\z", r"\Z"))  # Go RE2 \z is Python \Z
    for canary in canaries:
        for wrapped in (
            f"PI_KEY': '{canary}'",
            f'api_key = "{canary}"',
            f"'{canary}'",
        ):
            if not pattern.search(wrapped):
                problems.append(f"does not accept {wrapped!r}")
    token = canaries[0]
    for bad in (
        f"PI_KEY': 'x{token}'",
        f"PI_KEY': '{token}-extra-secret-looking-tail-0123456789abcdef'",
        "PI_KEY': 'xCANARYKEY-aaaa'",
        "PI_KEY': 'CANARYKEY-aaaa-extra-secret-looking-tail-0123456789abcdef'",
        "PI_KEY': 'CANARYKEY-aaaa'",
        f"PI_KEY': {token}",
        f"PI_KEY': '{token}",
        f"PI_KEY': {token}'",
        f"PI_KEY': '{token}'; {DECOY_A}",
        f"PI_KEY': '{token}' + '{DECOY_B}'",
        f"PI_KEY': '{token.upper()}'",
        f"PI_KEY': '{token[:-1]}'",
        f"PI_KEY': '{token}0'",
        "PI_KEY': 'CANARYKEY-'",
        f"PI_KEY': '{token.rsplit('-', 1)[0]}-'",
        f"PI_KEY': '{DECOY_B}'",
    ):
        if pattern.search(bad):
            problems.append(f"accepts {bad!r}")
    return problems


def test_gitleaks_canary_allowlist_is_anchored_to_the_canary_shape() -> None:
    canaries = _canaries()
    assert len(canaries) >= 3, canaries  # batch D, F and the providers support module
    for canary in canaries:
        assert CANARY_SHAPE.fullmatch(canary), "the shape this test pins is the canary's shape"
    (regex,) = _regexes()
    assert regex_problems(regex, canaries) == []


def test_gitleaks_canary_allowlist_regex_is_the_reviewed_line() -> None:
    (regex,) = _regexes()
    assert regex == REVIEWED_REGEX
    text = CONFIG.read_text(encoding="utf-8")
    assert f"regexes = ['''{REVIEWED_REGEX}''']" in text
    assert "CANARYKEY-[a-z0-9-]+" not in text, "the unanchored pattern is gone"


def test_gitleaks_canary_allowlist_keeps_its_scope() -> None:
    allow = _canary_allowlist()
    assert allow["targetRules"] == ["generic-api-key"]
    assert allow["condition"] == "AND"
    assert allow["regexTarget"] == "match"
    assert allow["paths"] == [EVIDENCE_PATHS]
    assert sorted(allow) == [
        "condition", "description", "paths", "regexTarget", "regexes", "targetRules",
    ]  # fmt: skip
    # the file is still the default rule set plus allowlists, nothing else turned off
    data = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert data["extend"] == {"useDefault": True}
    assert set(data) == {"title", "extend", "allowlists"}
    assert len(data["allowlists"]) == 4


def test_gitleaks_canary_regex_check_has_teeth() -> None:
    canaries = [_made_up("zq7fc3d1", "9ae2b8"), _made_up("f17ce2", "0b93d4")]
    assert regex_problems(REVIEWED_REGEX, canaries) == []
    # the regex of the gate finding (unanchored) is refused, for both of its faults
    problems = regex_problems(r"CANARYKEY-[a-z0-9-]+", canaries)
    assert any("accepts" in p and "xCANARYKEY" in p for p in problems), problems
    assert any("extra-secret-looking-tail" in p for p in problems), problems
    # a quote before the token only, or after it only, or an open tail, is refused
    assert regex_problems(r"""['"]CANARYKEY-[a-z0-9-]+""", canaries)
    assert regex_problems(r"""CANARYKEY-[a-z0-9]{6,8}-[a-z0-9]{6}['"]""", canaries)
    assert regex_problems(r"""['"]CANARYKEY-[a-z0-9]{6,8}-[a-z0-9]{6}['"]""", canaries)
    assert regex_problems(r"""['"]CANARYKEY-[a-z0-9-]+['"]\z""", canaries)
    # a regex that refuses the real canary is refused too (it would stop the evidence scan)
    assert any(
        "does not accept" in p for p in regex_problems(r"""'CANARYKEY-zzzzzz-zzzzzz'\z""", canaries)
    )
    # look-around is not RE2
    assert any("RE2" in p for p in regex_problems(REVIEWED_REGEX + r"(?!x)", canaries))
    # the uppercase and shorter shapes are not the canary
    assert not CANARY_SHAPE.fullmatch("CANARYKEY-AAAAAA-bbbbbb")
    assert not CANARY_SHAPE.fullmatch("CANARYKEY-aaaa-bbbbbb")
    assert not CANARY_SHAPE.fullmatch("CANARYKEY-aaaaaa-bbbbbbb")
