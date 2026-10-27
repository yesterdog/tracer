# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-02: licence policy for tracer-evalsim, its optional ``webrtc`` extra and the tool-only
``tools/samplebot`` closure (contract docs/phase4/api-surface-batch-0b.md section 4; review
docs/security/P4-02-licenses.md).

The policy block format follows ``[runtime.evaldb]``: ``closure`` (sorted, workspace packages left
out) and ``requires_dist``; the extra's added packages are the sub-table
``[runtime.evalsim.extras_webrtc]`` (key ``closure``). ``protobuf`` is not used (lead decision
E-b): it is in no closure, override or lock entry. certifi stays the only runtime exception; the
FFmpeg stack inside the ``av`` wheel is recorded in ``[[bundled_notices]]`` (the checker cannot
attach an exception to a package whose metadata says BSD-3-Clause).
"""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import importlib.util
import json
import re
import subprocess
import sys
import textwrap
import tomllib
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "uv.lock").is_file():
            return parent
    raise RuntimeError("no uv.lock above the tests")


ROOT = _repo_root()
POLICY = ROOT / "docs" / "security" / "license-policy.toml"
REVIEW = ROOT / "docs" / "security" / "P4-02-licenses.md"
SCRIPT = ROOT / "scripts" / "check_licenses.py"
EVALSIM = ROOT / "packages" / "evalsim"
SAMPLEBOT = ROOT / "tools" / "samplebot"
GPL_FAMILY = re.compile(r"(?i)\b(?:a|l)?gpl\b|general public licen[cs]e")

BASE = ["annotated-types", "pydantic", "pydantic-core", "rfc8785", "typing-extensions"]
EVALSIM_CLOSURE = sorted([*BASE, "typing-inspection", "websockets"])
WEBRTC_CLOSURE = sorted(
    [
        "aioice",
        "aiortc",
        "av",
        "cffi",
        "cryptography",
        "dnspython",
        "google-crc32c",
        "ifaddr",
        "pycparser",
        "pyee",
        "pylibsrtp",
        "pyopenssl",
    ]
)
REQUIRES = [
    "tracer-evalschema==0.0.1",
    "tracer-evalcore==0.0.1",
    "websockets>=15.0,<18",
    "aiortc>=1.15.0,<2; extra == 'webrtc'",
]
#: Phase 3 blocks: untouched by Phase 4 (the command line does not depend on tracer-evalsim yet).
PHASE3_REQUIRES = {
    "evalcore": ["tracer-evalschema==0.0.1"],
    "evalproviders": ["tracer-evalschema==0.0.1", "tracer-evalcore==0.0.1", "httpx>=0.28.1,<1"],
    "cli": [
        "tracer-evalschema==0.0.1",
        "tracer-evalcore==0.0.1",
        "tracer-evalproviders==0.0.1",
        "tracer-evaldb==0.0.1",
        "tracer-evalsim==0.0.1",  # Phase 4 batch E spec change S-E2: `tracer simulate`
    ],
}
#: The packages the sample bots bring that are copyleft or hold copyleft binaries (tool-only).
TOOL_ONLY = {"num2words", "soxr", "soundfile", "opencv-python-headless", "certifi"}
TOOL_ONLY_LOCKED_OUT = TOOL_ONLY - {"certifi"}  # certifi is also httpx's (existing exception)
SECTION8_NAMES = (
    "num2words",
    "soxr",
    "soundfile",
    "onnxruntime",
    "numba",
    "sentencex",
    "loudness",
    "opencv-python-headless",
)


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _lock() -> dict[str, dict[str, Any]]:
    return {canonicalize_name(p["name"]): p for p in _toml(ROOT / "uv.lock")["package"]}


def _reachable(root: str, extra: str | None = None) -> set[str]:
    packages = _lock()
    seen: set[str] = set()
    todo = [root]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo += [canonicalize_name(d["name"]) for d in packages[name].get("dependencies", [])]
        if name == root and extra is not None:
            optional = packages[name].get("optional-dependencies", {}).get(extra, [])
            todo += [canonicalize_name(d["name"]) for d in optional]
    seen.discard(root)
    return seen


def _workspace() -> set[str]:
    return {canonicalize_name(n) for n, p in _lock().items() if "editable" in p["source"]}


def _key(text: str) -> tuple[str, str, Any]:
    req = Requirement(text)
    return (canonicalize_name(req.name), str(req.marker or ""), req.specifier)


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _licence_files(name: str) -> dict[str, bytes]:
    dist = md.distribution(name)
    found: dict[str, bytes] = {}
    for f in dist.files or []:
        low = f.name.lower()
        if ".dist-info" in str(f) and low.startswith(("licen", "copying", "notice")):
            found[str(f)] = f.read_binary()
    return found


# --------------------------------------------------------------------------- the SCOPE tests


def test_license_policy_lists_phase4_closures() -> None:
    policy = _toml(POLICY)
    runtime = policy["runtime"]
    assert "evalsim" in runtime, "the policy has no [runtime.evalsim] block"
    block = runtime["evalsim"]
    assert set(block) == {"closure", "requires_dist", "extras_webrtc"}
    assert block["closure"] == EVALSIM_CLOSURE
    assert block["closure"] == sorted(block["closure"]), "the block is kept sorted"
    workspace = _workspace()
    base = _reachable("tracer-evalsim") - workspace
    assert base == set(block["closure"]), "the block agrees with the lock"
    assert sorted(_key(d) for d in block["requires_dist"]) == sorted(_key(d) for d in REQUIRES)

    extra = block["extras_webrtc"]
    assert set(extra) == {"closure"}
    assert extra["closure"] == WEBRTC_CLOSURE
    assert extra["closure"] == sorted(extra["closure"])
    assert _reachable("tracer-evalsim", "webrtc") - workspace - base == set(extra["closure"])
    assert not set(extra["closure"]) & set(block["closure"]), "the extra lists only what it adds"

    # protobuf is not used: no closure, no override, no lock entry, no word anywhere in the data
    assert "protobuf" not in json.dumps(policy).lower()
    assert "protobuf" not in _lock()
    # the Phase 3 and earlier blocks are untouched, and nothing there reaches websockets
    for key, requires in PHASE3_REQUIRES.items():
        assert sorted(_key(d) for d in runtime[key]["requires_dist"]) == sorted(
            _key(d) for d in requires
        ), key
        # Phase 4 batch E spec change S-E2: the command line now carries websockets
        assert ("websockets" in runtime[key]["closure"]) == (key == "cli"), key
    assert set(runtime["evaldb"]) == {"closure", "extras_postgres", "requires_dist"}
    assert runtime["requires_dist"] == ["pydantic>=2.11,<3", "rfc8785>=0.1.4,<0.2"]
    assert set(runtime["closure"]) <= set(block["closure"])

    # the one runtime exception is still certifi, and av cannot carry one (the checker consults
    # exceptions only for packages whose metadata is not allowed): it is recorded as a bundled
    # notice with the lead's decision (owner question O6)
    exceptions = {e["name"] for e in policy["exceptions"]}
    assert exceptions == {"hypothesis", "pathspec", "certifi"}
    notices = {n["name"]: n for n in policy["bundled_notices"]}
    assert set(notices) == {"av", "pylibsrtp"}
    for name, notice in notices.items():
        assert notice["version"] == _lock()[name]["version"] == md.version(name), name
        assert notice["recheck"].strip(), name
        assert notice["evidence" if name == "av" else "bundled"].strip(), name
    status = notices["av"]["status"].lower()
    assert status.startswith("named exception"), status
    for stale in ("proposed", "lead to log", "nothing is applied"):
        assert stale not in status, f"the av status still says {stale!r}"
    assert "docs/security/P4-02-licenses.md" in notices["av"]["status"]
    for word in ("GPL", "FFmpeg", "libx264"):
        assert word in notices["av"]["bundled"], word
    # the stale proposal wording is gone from the applied file
    text = POLICY.read_text(encoding="utf-8")
    for stale in ("PROPOSED", "NOTHING IS APPLIED", "ignored by the current loader"):
        assert stale not in text, stale

    # pyopenssl: free-text licence metadata, cleared by a pinned override read from its file
    overrides = {o["name"]: o for o in policy["overrides"]}
    assert "pyopenssl" in overrides
    override = overrides["pyopenssl"]
    assert override["version"] == _lock()["pyopenssl"]["version"] == md.version("pyopenssl")
    assert override["spdx"] == "Apache-2.0"
    assert override["scope"] == "runtime"
    assert override["license_file"] == "licenses/LICENSE"
    assert re.fullmatch(r"[0-9a-f]{64}", override["license_file_sha256"])
    files = _licence_files("pyopenssl")
    (body,) = [b for n, b in files.items() if n.endswith("licenses/LICENSE")]
    assert override["license_file_sha256"] == hashlib.sha256(body).hexdigest()
    assert "Apache License" in body.decode("utf-8", errors="replace")
    assert override["reason"].strip()
    # no override was added for a package the metadata already clears
    assert overrides.keys().isdisjoint({"websockets", "aiortc", "av", "aioice", "pylibsrtp"})

    # pipecat-ai is recorded as rejected for runtime, with the reason and the way out
    rejected = {r["name"]: r for r in policy["rejected"]}
    assert {"sarvamai", "requests", "psycopg", "psycopg-binary", "pipecat-ai"} <= set(rejected)
    pipecat = rejected["pipecat-ai"]
    assert pipecat["spdx"] == "BSD-2-Clause"
    for needle in ("num2words", "soxr", "LGPL", "tools/samplebot", "uv.lock", "1.14.0"):
        assert needle in pipecat["reason"], needle
    assert "pipecat-ai" not in _lock()
    # the allowlist did not grow a copyleft term
    assert not any(GPL_FAMILY.search(s) for s in policy["allowlist"]["spdx"])
    assert "MPL-2.0" not in policy["allowlist"]["spdx"]

    # every licence is read from a file: all packages of the closures (the extra included) carry
    # one in their installed distribution, dnspython excepted (finding L-3 of the review), and
    # none declares a GPL-family licence in its metadata
    missing = []
    for name in [*block["closure"], *extra["closure"]]:
        if not _licence_files(name):
            missing.append(name)
        meta = md.metadata(name)
        fields = [
            *meta.get_all("License-Expression", []),
            *meta.get_all("License", []),
            *meta.get_all("Classifier", []),
        ]
        assert not any(GPL_FAMILY.search(f) for f in fields), f"{name}: {fields}"
    assert set(missing) <= {"dnspython"}, missing


def test_samplebot_closure_reviewed_and_tool_only() -> None:
    review = REVIEW.read_text(encoding="utf-8")
    assert "## 8. tools/samplebot closure (TOOL-ONLY)" in review
    section = review[review.index("## 8. tools/samplebot closure") :]
    section = section[: section.index("\n## 9.")]
    for name in SECTION8_NAMES:
        assert name in section, name
    assert "outside every tracer wheel" in section
    for word in ("soxr", "1.0.0"):
        assert word in review, word

    policy = _toml(POLICY)
    assert "samplebot" in policy.get("tools", {}), "the policy has no [tools.samplebot] table"
    tool = policy["tools"]["samplebot"]
    assert set(tool) == {"pin", "reviewed", "copyleft_tool_only", "not_fully_reviewed"}
    assert tool["pin"] == "pipecat-ai==1.14.0"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", tool["reviewed"])
    entries = tool["copyleft_tool_only"]
    parsed: dict[str, tuple[str, str]] = {}
    for entry in entries:
        match = re.fullmatch(r"([A-Za-z0-9._-]+) ([0-9][0-9A-Za-z.]*) \((.+)\)", entry)
        assert match, entry
        parsed[str(canonicalize_name(match.group(1)))] = (match.group(2), match.group(3))
    assert set(parsed) == TOOL_ONLY
    assert parsed["soxr"][0] == "1.0.0"
    assert "LGPL-2.1-or-later" in parsed["soxr"][1], "decision D-d: soxr is another LGPL item"
    assert "LGPL" in parsed["num2words"][1]
    assert "libsndfile" in parsed["soundfile"][1]
    assert tool["not_fully_reviewed"], "what was not read in full stays on the record"

    # tool-only: none of it is in uv.lock (certifi is the existing exception through httpx), in a
    # tracer package's dependencies, in a runtime closure, an override or an exception
    locked = _lock()
    assert TOOL_ONLY_LOCKED_OUT.isdisjoint(locked), sorted(TOOL_ONLY_LOCKED_OUT & set(locked))
    declared = " ".join(
        (ROOT / "packages" / key / "pyproject.toml").read_text(encoding="utf-8")
        for key in ("evalschema", "evaldb", "evalcore", "evalproviders", "cli", "evalsim")
    )
    for name in TOOL_ONLY_LOCKED_OUT:
        assert name not in declared, name
    runtime_names: set[str] = set()
    for value in policy["runtime"].values():
        if isinstance(value, dict):
            for part in value.values():
                if isinstance(part, dict):
                    runtime_names |= {canonicalize_name(n) for n in part.get("closure", [])}
                elif isinstance(part, list):
                    runtime_names |= {canonicalize_name(str(n)) for n in part}
    runtime_names |= {canonicalize_name(n) for n in policy["runtime"]["closure"]}
    assert TOOL_ONLY_LOCKED_OUT.isdisjoint(runtime_names)
    named = {canonicalize_name(e["name"]) for e in [*policy["overrides"], *policy["exceptions"]]}
    assert TOOL_ONLY_LOCKED_OUT.isdisjoint(named)
    for word in ("opencv-python-headless", "FFmpeg"):
        assert word in review, word

    # the checker reads the root lock only: the tool's own lock is never an input of the gate
    script = SCRIPT.read_text(encoding="utf-8")
    assert "samplebot" not in script
    # the tool project says so itself
    readme = (SAMPLEBOT / "README.md").read_text(encoding="utf-8")
    assert "P4-02-licenses.md" in readme
    assert "LGPL" in readme
    # once the tool has its own lock, it holds the versions that were reviewed
    own = SAMPLEBOT / "uv.lock"
    if own.exists():
        own_pkgs = {str(canonicalize_name(p["name"])): p for p in _toml(own)["package"]}
        for name in ("num2words", "soxr", "soundfile"):
            assert name in own_pkgs, f"{name} left the Pipecat closure: redo the review"
            assert own_pkgs[name]["version"] == parsed[name][0], name


# ------------------------------------------------- added by qa (not in the SCOPE table)


def test_check_licenses_covers_evalsim_and_its_extra(tmp_path: Path) -> None:
    """The script scans six published packages now, and the ``webrtc`` extra is frozen too."""
    done = _run_script()
    assert done.returncode == 0, done.stdout + done.stderr
    assert " 0 failed" in done.stdout
    text = POLICY.read_text(encoding="utf-8")

    def tampered(old: str, new: str, after: str) -> Path:
        assert after in text, f"the policy lacks {after.strip()}"
        start = text.index(after)
        assert old in text[start:], old
        cut = start + text[start:].index(old)
        path = tmp_path / f"policy-{len(list(tmp_path.iterdir()))}.toml"
        path.write_text(text[:cut] + new + text[cut + len(old) :], encoding="utf-8")
        return path

    def fails(path: Path, *needles: str, args: tuple[str, ...] = ()) -> None:
        result = _run_script("--policy", str(path), *args)
        assert result.returncode == 1, result.stdout + result.stderr
        for needle in needles:
            assert needle in result.stderr, (needle, result.stderr)

    fails(
        tampered("closure = [", 'closure = [\n    "zzz-not-in-the-lock",', "\n[runtime.evalsim]\n"),
        "tracer-evalsim",
        "zzz-not-in-the-lock",
    )
    fails(
        tampered('    "websockets",\n', "", "\n[runtime.evalsim]\n"),
        "tracer-evalsim",
        "websockets",
    )
    fails(
        tampered('    "av",\n', "", "\n[runtime.evalsim.extras_webrtc]\n"),
        "tracer-evalsim",
        "webrtc extra closure changed",
        "av",
    )
    fails(
        tampered(
            "closure = [",
            'closure = [\n    "zzz-extra-not-in-the-lock",',
            "\n[runtime.evalsim.extras_webrtc]\n",
        ),
        "webrtc extra closure changed",
        "zzz-extra-not-in-the-lock",
    )
    # without its override pyOpenSSL (free-text licence metadata) is a licence failure by name
    stripped = re.sub(
        r'\[\[overrides\]\]\nname = "pyopenssl".*?(?=\n\[\[|\Z)', "", text, count=1, flags=re.DOTALL
    )
    assert stripped != text
    without = tmp_path / "policy-without-pyopenssl.toml"
    without.write_text(stripped, encoding="utf-8")
    fails(without, "pyOpenSSL")

    # the built wheel is checked against its own Requires-Dist and its one extra
    dist = tmp_path / "dist"
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(dist)],
        cwd=EVALSIM,
        check=True,
        capture_output=True,
    )
    done = _run_script("--wheel", str(dist), "--artifacts-only")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 ok, 0 failed" in done.stdout
    loose = tampered('"websockets>=15.0,<18"', '"websockets>=15.0"', "\n[runtime.evalsim]\n")
    fails(loose, "Requires-Dist", "tracer_evalsim", args=("--wheel", str(dist), "--artifacts-only"))
    no_extra = tampered(
        "    \"aiortc>=1.15.0,<2; extra == 'webrtc'\",\n", "", "\n[runtime.evalsim]\n"
    )
    fails(no_extra, "Requires-Dist", args=("--wheel", str(dist), "--artifacts-only"))


@pytest.mark.parametrize("name", ["tracer-evalsim"])
def test_evalsim_lock_entry_declares_its_extra(name: str) -> None:
    """The lock records the extra, so the checker's extra closure has something to read."""
    assert name in _lock(), "uv.lock does not hold tracer-evalsim"
    entry = _lock()[name]
    assert entry["metadata"]["provides-extras"] == ["webrtc"]
    assert [d["name"] for d in entry["optional-dependencies"]["webrtc"]] == ["aiortc"]


_TWO_VERSIONS_LOCK = """
version = 1
requires-python = ">=3.11"

[[package]]
name = "rootpkg"
version = "0.0.1"
source = { editable = "." }
dependencies = [
    { name = "pyee", version = "13.0.1" },
    { name = "pyee", version = "16.0.0" },
]

[[package]]
name = "pyee"
version = "13.0.1"
source = { registry = "https://pypi.org/simple" }
resolution-markers = ["python_full_version < '3.12'"]
dependencies = [{ name = "typing-extensions" }]

[[package]]
name = "pyee"
version = "16.0.0"
source = { registry = "https://pypi.org/simple" }
resolution-markers = ["python_full_version >= '3.12'"]

[[package]]
name = "typing-extensions"
version = "4.16.0"
source = { registry = "https://pypi.org/simple" }
"""


def test_check_licenses_accepts_one_name_locked_at_two_versions(tmp_path: Path) -> None:
    """``pyee`` is locked at 13.0.1 (Python below 3.12) and at 16.0.0 (3.12 and above). The
    checker must accept the one that the interpreter installed, on every supported Python, and
    still refuse a version the lock does not hold. (Added by qa: the first oracle run failed on
    Python 3.11 because the checker kept the last of the two entries only.)"""
    name = "check_licenses_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    sys.modules[name] = script
    try:
        spec.loader.exec_module(script)
    finally:
        sys.modules.pop(name, None)
    lock_path = tmp_path / "uv.lock"
    lock_path.write_text(textwrap.dedent(_TWO_VERSIONS_LOCK), encoding="utf-8")
    lock = script.Lock.load(lock_path)
    assert [e.version for e in lock.versions["pyee"]] == ["13.0.1", "16.0.0"]
    # the closure follows the dependencies of every locked version of the name
    assert lock.closure(["rootpkg"]) == {"rootpkg", "pyee", "typing-extensions"}
    policy = script.Policy.load(POLICY)

    def dist(version: str) -> Any:
        meta = Message()
        meta["Name"] = "pyee"
        meta["Version"] = version
        meta["License-Expression"] = "MIT"
        return script.Dist(name="pyee", version=version, meta=meta, license_files={})

    def about_pyee(installed: list[Any]) -> list[Any]:
        found = script.evaluate_dists(installed, lock, policy, ["dev"])
        return [f for f in found if f.name == "pyee"]

    for version in ("13.0.1", "16.0.0"):
        (finding,) = about_pyee([dist(version)])
        assert finding.status == "ok", (version, finding)
        assert finding.version == version
    (wrong,) = about_pyee([dist("15.0.0")])
    assert wrong.status == "FAIL"
    assert "13.0.1" in wrong.note
    assert "16.0.0" in wrong.note
    (absent,) = about_pyee([])
    assert absent.status == "FAIL"
    assert "not installed" in absent.note
    # a stale override is judged against every locked version of its name
    stale = [{"name": "pyee", "version": "16.0.0", "spdx": "MIT"}]
    first = [{"name": "pyee", "version": "13.0.1", "spdx": "MIT"}]
    assert not script.policy_findings(lock, _with(policy, overrides=first, exceptions=[]), set())
    other = [{"name": "pyee", "version": "9.9.9", "spdx": "MIT"}]
    assert not script.policy_findings(lock, _with(policy, overrides=stale, exceptions=[]), set())
    assert [
        f.status
        for f in script.policy_findings(lock, _with(policy, overrides=other, exceptions=[]), set())
    ] == ["FAIL"]


def _with(policy: Any, **changes: Any) -> Any:
    fields = {k: getattr(policy, k) for k in policy.__dataclass_fields__}
    fields.update(changes)
    return type(policy)(**fields)


_EXTRA_LOCK = """
version = 1
requires-python = ">=3.11"

[[package]]
name = "tracer-evalsim"
version = "0.0.1"
source = { editable = "packages/evalsim" }

[package.metadata]
requires-dist = [
    { name = "aiortc", extra = "webrtc", specifier = ">=1.15.0,<2" },
    { name = "pyfoo", extra = "foo", specifier = ">=1.0,<2" },
]
"""


def test_check_licenses_allows_only_the_declared_extra_of_evalsim(tmp_path: Path) -> None:
    """A requirement of ``tracer-evalsim`` that belongs to the ``webrtc`` extra is a bounded range
    like any other; a requirement of any other extra is still an "extra dependency" failure, and
    so is every extra of a Phase 3 package. (Added by qa: the branch is dead in the lock format of
    uv 0.12, which writes the extra only into ``marker``, and a dead branch is not a test.)"""
    name = "check_licenses_extra_under_test"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    sys.modules[name] = script
    try:
        spec.loader.exec_module(script)
    finally:
        sys.modules.pop(name, None)
    path = tmp_path / "uv.lock"
    path.write_text(_EXTRA_LOCK, encoding="utf-8")
    notes = [
        f.note for f in script.phase3_findings(script.Lock.load(path), script.Policy.load(POLICY))
    ]
    assert "extra dependency pyfoo" in notes
    assert "extra dependency aiortc" not in notes
    assert not [n for n in notes if "not declared as a bounded range" in n]
    assert script.PHASE4_EXTRAS == {"tracer-evalsim": "webrtc"}
