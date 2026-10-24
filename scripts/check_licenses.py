# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""License gate: scan locked/installed distributions and built artifacts against the policy.

Policy lives in docs/security/license-policy.toml. Metadata is evaluated in this order:
PEP 639 License-Expression, then the short License field, then License:: classifiers.
Classifiers alone never clear a package; they can only reveal copyleft/MPL or force an
override. Overrides and exceptions are pinned to name, version and license-file SHA-256.

Stdlib only. Exit code 1 on any offence.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import os
import re
import sys
import tarfile
import tomllib
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = ROOT / "uv.lock"
DEFAULT_POLICY = ROOT / "docs" / "security" / "license-policy.toml"
ROOT_PROJECT = "tracer-workspace"
RUNTIME_PROJECT = "tracer-evalschema"
EVALDB_PROJECT = "tracer-evaldb"
EVALDB_EXTRA = "postgres"
# Phase 3 packages: project name -> key of the [runtime.<key>] policy table.
PHASE3_PROJECTS = {
    "tracer-evalcore": "evalcore",
    "tracer-evalproviders": "evalproviders",
    "tracer-cli": "cli",
}
MAX_LICENSE_FILE_BYTES = 1_000_000
MAX_ARTIFACT_METADATA_BYTES = 10_000_000
_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

# Ranked from best to worst; OR takes the minimum, AND the maximum.
ALLOWED, MPL, COPYLEFT, BAD, UNCLEAR = 0, 1, 2, 3, 4
CLASS_NAMES = {ALLOWED: "allowed", MPL: "mpl", COPYLEFT: "copyleft", BAD: "bad", UNCLEAR: "unclear"}

_COPYLEFT_RE = re.compile(r"^(?:a|l)?gpl(?:-|$)")
_BAD_PREFIXES = ("sspl", "busl", "cc-by-nc", "licenseref", "proprietary", "commons-clause")
_CLASSIFIER_RESTRICTED = (
    (re.compile(r"affero|\bgpl\b|general public license|\blgpl\b", re.I), COPYLEFT),
    (re.compile(r"mozilla|\bmpl\b", re.I), MPL),
    (re.compile(r"non-?commercial", re.I), BAD),
)
_TOKEN_RE = re.compile(r"\s*(\(|\)|[A-Za-z0-9][A-Za-z0-9.+-]*)")


def _normal_requirement(requirement: str) -> str:
    """A requirement with its specifiers sorted (build backends write ``<3,>=2.11`` for
    ``>=2.11,<3``); the constraint is the same, only the order differs."""
    text = requirement.replace(" ", "")
    if ";" in text:  # an environment marker: only the quote style differs between writers
        head, marker = text.split(";", 1)
        return _normal_requirement(head) + ";" + marker.replace("'", '"')
    match = re.match(r"([A-Za-z0-9_.-]+)(.*)", text)
    if match is None:
        return text
    return match.group(1) + ",".join(sorted(part for part in match.group(2).split(",") if part))


def norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass
class Policy:
    allowed: frozenset[str]
    runtime_closure: frozenset[str]
    runtime_requires_dist: frozenset[str]
    evaldb_closure: frozenset[str]
    evaldb_extras: frozenset[str]
    evaldb_requires_dist: frozenset[str]
    phase3_closure: dict[str, frozenset[str]]
    phase3_requires_dist: dict[str, frozenset[str]]
    overrides: list[dict[str, Any]]
    exceptions: list[dict[str, Any]]

    @classmethod
    def load(cls, path: Path) -> Policy:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        runtime = data.get("runtime", {})
        evaldb = runtime.get("evaldb", {})
        return cls(
            allowed=frozenset(s.lower() for s in data["allowlist"]["spdx"]),
            runtime_closure=frozenset(norm_name(n) for n in runtime.get("closure", [])),
            runtime_requires_dist=frozenset(runtime.get("requires_dist", [])),
            evaldb_closure=frozenset(norm_name(n) for n in evaldb.get("closure", [])),
            evaldb_extras=frozenset(norm_name(n) for n in evaldb.get("extras_postgres", [])),
            evaldb_requires_dist=frozenset(evaldb.get("requires_dist", [])),
            phase3_closure={
                project: frozenset(norm_name(n) for n in runtime.get(key, {}).get("closure", []))
                for project, key in PHASE3_PROJECTS.items()
            },
            phase3_requires_dist={
                project: frozenset(runtime.get(key, {}).get("requires_dist", []))
                for project, key in PHASE3_PROJECTS.items()
            },
            overrides=list(data.get("overrides", [])),
            exceptions=list(data.get("exceptions", [])),
        )


def classify_term(term: str, allowed: frozenset[str]) -> int:
    t = term.lower().rstrip("+")
    if term.lower() in allowed or t in allowed:
        return ALLOWED
    if t.startswith("mpl-"):
        return MPL
    if _COPYLEFT_RE.match(t):
        return COPYLEFT
    if t.startswith(_BAD_PREFIXES):
        return BAD
    return UNCLEAR


def classify_expression(text: str, allowed: frozenset[str]) -> int | None:
    """Class of an SPDX expression, or None if the text is not a parseable expression."""
    tokens: list[str] = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            return None
        tokens.append(m.group(1))
        pos = m.end()
        if text[pos:].strip() == "":
            break
    if not tokens:
        return None
    idx = 0

    def parse_or() -> int:
        nonlocal idx
        best = parse_and()
        while idx < len(tokens) and tokens[idx].upper() == "OR":
            idx += 1
            best = min(best, parse_and())
        return best

    def parse_and() -> int:
        nonlocal idx
        worst = parse_atom()
        while idx < len(tokens) and tokens[idx].upper() == "AND":
            idx += 1
            worst = max(worst, parse_atom())
        return worst

    def parse_atom() -> int:
        nonlocal idx
        if idx >= len(tokens):
            raise ValueError("unexpected end")
        tok = tokens[idx]
        idx += 1
        if tok == "(":
            val = parse_or()
            if idx >= len(tokens) or tokens[idx] != ")":
                raise ValueError("unbalanced")
            idx += 1
            return val
        if tok == ")" or tok.upper() in {"AND", "OR", "WITH"}:
            raise ValueError("unexpected token")
        return classify_term(tok, allowed)

    try:
        val = parse_or()
    except ValueError:
        return None
    return val if idx == len(tokens) else None


@dataclass
class Dist:
    name: str
    version: str
    meta: Message
    license_files: dict[str, bytes] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return norm_name(self.name)


@dataclass
class Verdict:
    klass: int
    basis: str
    declared: str


def classify_metadata(meta: Message, allowed: frozenset[str]) -> Verdict:
    expr = (meta.get("License-Expression") or "").strip()
    short = (meta.get("License") or "").strip()
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    restricted = max(
        (k for c in classifiers for rx, k in _CLASSIFIER_RESTRICTED if rx.search(c)),
        default=ALLOWED,
    )
    primary: int | None = None
    basis = declared = ""
    if expr:
        primary = classify_expression(expr, allowed)
        basis, declared = "License-Expression", expr
    elif short and "\n" not in short and len(short) <= 100:
        primary = classify_expression(short, allowed)
        basis, declared = "License", short
    elif short:
        basis, declared = "License", "<free text>"
    if primary is not None:
        if primary == ALLOWED and restricted != ALLOWED:
            return Verdict(UNCLEAR, basis, f"{declared} (conflicts with classifier)")
        return Verdict(primary, basis, declared)
    if restricted != ALLOWED:
        return Verdict(restricted, "classifier", "; ".join(classifiers))
    if classifiers:
        return Verdict(UNCLEAR, "classifier", "; ".join(classifiers))
    return Verdict(UNCLEAR, "none", declared or "<none>")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_license_like(rel: str) -> bool:
    low = rel.lower().rsplit("/", 1)[-1]
    return low.startswith(("licen", "copying", "notice", "unlicense"))


def installed_dists(paths: list[str] | None) -> list[Dist]:
    out: list[Dist] = []
    for d in md.distributions(path=paths) if paths else md.distributions():
        name = d.metadata["Name"]
        if not name:
            continue
        files: dict[str, bytes] = {}
        for f in d.files or []:
            parts = f.parts
            di = next((i for i, p in enumerate(parts) if p.endswith(".dist-info")), None)
            if di is None:
                continue
            rel = "/".join(parts[di + 1 :])
            if not rel or not _is_license_like(rel):
                continue
            p = Path(str(d.locate_file(f)))
            if p.is_file() and p.stat().st_size <= MAX_LICENSE_FILE_BYTES:
                files[rel] = p.read_bytes()
        meta = BytesParser().parsebytes(d.read_text("METADATA").encode("utf-8"))  # type: ignore[union-attr]
        out.append(Dist(name=name, version=d.version, meta=meta, license_files=files))
    return out


@dataclass
class LockPackage:
    name: str
    version: str
    source: dict[str, Any]
    deps: list[str]
    extra_deps: dict[str, list[str]]
    requires_dist: list[dict[str, Any]]


@dataclass
class Lock:
    packages: dict[str, LockPackage]
    groups: dict[str, list[str]]  # root dev-dependency group -> direct package names

    @classmethod
    def load(cls, path: Path) -> Lock:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        pkgs: dict[str, LockPackage] = {}
        groups: dict[str, list[str]] = {}
        for p in data["package"]:
            key = norm_name(p["name"])
            deps = [norm_name(d["name"]) for d in p.get("dependencies", [])]
            pkgs[key] = LockPackage(
                name=p["name"],
                version=p.get("version", ""),
                source=p.get("source", {}),
                deps=deps,
                extra_deps={
                    extra: [norm_name(d["name"]) for d in items]
                    for extra, items in p.get("optional-dependencies", {}).items()
                },
                requires_dist=p.get("metadata", {}).get("requires-dist", []),
            )
            if key == norm_name(ROOT_PROJECT):
                for g, items in p.get("dev-dependencies", {}).items():
                    groups[g] = [norm_name(i["name"]) for i in items]
        return cls(packages=pkgs, groups=groups)

    def closure(self, roots: Iterable[str], extras: Iterable[str] = ()) -> set[str]:
        """Packages reachable from the roots; ``extras`` are those of the roots only."""
        seen: set[str] = set()
        stack = [norm_name(r) for r in roots]
        root_keys = set(stack)
        while stack:
            n = stack.pop()
            if n in seen or n not in self.packages:
                continue
            seen.add(n)
            stack.extend(self.packages[n].deps)
            if n in root_keys:
                for extra in extras:
                    stack.extend(self.packages[n].extra_deps.get(extra, []))
        return seen


def uv_default_groups(pyproject: Path) -> list[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return list(data.get("tool", {}).get("uv", {}).get("default-groups", ["dev"]))


def find_entry(entries: list[dict[str, Any]], dist: Dist) -> dict[str, Any] | None:
    for e in entries:
        if norm_name(e["name"]) == dist.key and e.get("version") == dist.version:
            return e
    return None


def pin_error(entry: dict[str, Any], dist: Dist, what: str) -> str | None:
    rel = entry["license_file"]
    data = dist.license_files.get(rel)
    if data is None:
        return f"{what} pinned license file {rel!r} not found in the distribution"
    got = sha256(data)
    if got != entry["license_file_sha256"]:
        return f"{what} license-file hash mismatch for {rel}: got {got}"
    return None


@dataclass
class Finding:
    name: str
    version: str
    spdx: str
    status: str  # ok | FAIL
    note: str


def evaluate_dists(
    dists: list[Dist], lock: Lock, policy: Policy, default_groups: list[str]
) -> list[Finding]:
    findings: list[Finding] = []
    runtime = lock.closure([RUNTIME_PROJECT])
    group_members = {g: lock.closure(names) for g, names in lock.groups.items()}
    by_key = {d.key: d for d in dists}
    workspace = {
        k
        for k, p in lock.packages.items()
        if "workspace" in p.source or "virtual" in p.source or "editable" in p.source
    }

    def fail(d: Dist, spdx: str, note: str) -> None:
        findings.append(Finding(d.name, d.version, spdx, "FAIL", note))

    for key, lp in sorted(lock.packages.items()):
        if key == norm_name(ROOT_PROJECT):
            continue
        d = by_key.get(key)
        if d is None:
            pinned = find_entry(policy.overrides, Dist(lp.name, lp.version, Message()))
            if pinned is not None and pinned.get("platform_only"):
                findings.append(
                    Finding(
                        lp.name,
                        lp.version,
                        pinned["spdx"],
                        "ok",
                        "not installable on this platform; relies on the version-pinned override",
                    )
                )
                continue
            findings.append(
                Finding(
                    lp.name,
                    lp.version,
                    "?",
                    "FAIL",
                    "locked but not installed (sync with --all-groups)",
                )
            )
            continue
        if d.version != lp.version:
            fail(d, "?", f"installed version differs from lock ({lp.version})")
            continue
        override = find_entry(policy.overrides, d)
        if override is not None:
            err = pin_error(override, d, "override")
            if err:
                fail(d, override["spdx"], err)
                continue
            klass = classify_expression(override["spdx"], policy.allowed)
            verdict = Verdict(UNCLEAR if klass is None else klass, "override", override["spdx"])
        else:
            verdict = classify_metadata(d.meta, policy.allowed)
        spdx = verdict.declared
        if verdict.klass == ALLOWED:
            findings.append(Finding(d.name, d.version, spdx, "ok", verdict.basis))
            continue
        if verdict.klass in (UNCLEAR, BAD):
            fail(
                d,
                spdx,
                f"{CLASS_NAMES[verdict.klass]} license ({verdict.basis}); needs an override",
            )
            continue
        exc = find_entry(policy.exceptions, d)
        label = CLASS_NAMES[verdict.klass]
        if exc is None:
            fail(d, spdx, f"{label} license outside the exception list")
            continue
        exc_class = classify_expression(exc["spdx"], policy.allowed)
        if exc_class != verdict.klass:
            fail(d, spdx, f"exception {exc['spdx']} does not cover detected {label} license")
            continue
        err = pin_error(exc, d, "exception")
        if err:
            fail(d, spdx, err)
            continue
        problems = scope_problems(key, exc, verdict.klass, runtime, group_members, default_groups)
        if problems:
            fail(d, spdx, "; ".join(problems))
            continue
        findings.append(
            Finding(d.name, d.version, exc["spdx"], "ok", f"exception ({exc['scope']})")
        )

    for key in sorted(set(by_key) - set(lock.packages)):
        d = by_key[key]
        fail(d, "?", "installed but not in uv.lock")
    findings.extend(policy_findings(lock, policy, workspace))
    findings.extend(runtime_findings(lock, policy, runtime))
    return findings


def scope_problems(
    key: str,
    exc: dict[str, Any],
    klass: int,
    runtime: set[str],
    group_members: dict[str, set[str]],
    default_groups: list[str],
) -> list[str]:
    problems: list[str] = []
    allowed_groups = set(exc.get("groups", []))
    if key in runtime:
        problems.append("exception package is in the runtime closure")
    reached = {g for g, members in group_members.items() if key in members}
    extra = reached - allowed_groups
    if extra:
        problems.append(f"reachable from group(s) not covered by the exception: {sorted(extra)}")
    if not reached:
        problems.append("exception package is not reachable from any dependency group")
    if klass == COPYLEFT:
        defaults = set(default_groups)
        in_default = {g for g in reached if g in defaults}
        # A default group pulling it in, directly or by include, breaks the opt-in rule.
        if in_default:
            problems.append(
                f"copyleft package reachable from default group(s) {sorted(in_default)}"
            )
        if allowed_groups & defaults:
            problems.append("copyleft exception names a default group")
    return problems


def policy_findings(lock: Lock, policy: Policy, workspace: set[str]) -> list[Finding]:
    out: list[Finding] = []
    for kind, entries in (("override", policy.overrides), ("exception", policy.exceptions)):
        for e in entries:
            lp = lock.packages.get(norm_name(e["name"]))
            if lp is None or lp.version != e.get("version"):
                out.append(
                    Finding(
                        e["name"],
                        str(e.get("version")),
                        e.get("spdx", "?"),
                        "FAIL",
                        f"stale {kind}: not in uv.lock at that version",
                    )
                )
    for e in policy.exceptions:
        if norm_name(e["name"]) in workspace:
            out.append(
                Finding(
                    e["name"], str(e.get("version")), "?", "FAIL", "exception for workspace member"
                )
            )
    return out


def runtime_findings(lock: Lock, policy: Policy, runtime: set[str]) -> list[Finding]:
    out: list[Finding] = []
    expected = set(policy.runtime_closure) | {norm_name(RUNTIME_PROJECT)}
    if policy.runtime_closure and runtime != expected:
        out.append(
            Finding(
                RUNTIME_PROJECT,
                "",
                "?",
                "FAIL",
                f"runtime closure changed: extra {sorted(runtime - expected)}, "
                f"missing {sorted(expected - runtime)}",
            )
        )
    out.extend(evaldb_findings(lock, policy))
    out.extend(phase3_findings(lock, policy))
    lp = lock.packages.get(norm_name(RUNTIME_PROJECT))
    if lp is not None:
        for r in lp.requires_dist:
            if r.get("extra"):
                out.append(
                    Finding(
                        RUNTIME_PROJECT, lp.version, "?", "FAIL", f"extra dependency {r['name']}"
                    )
                )
            elif not re.fullmatch(r">=\d+(\.\d+)*,<\d+(\.\d+)*", str(r.get("specifier", ""))):
                out.append(
                    Finding(
                        RUNTIME_PROJECT,
                        lp.version,
                        "?",
                        "FAIL",
                        f"runtime dependency {r['name']} not declared as a bounded range (>=X,<Y)",
                    )
                )
    return out


def evaldb_findings(lock: Lock, policy: Policy) -> list[Finding]:
    """The second published package: its base closure and its [postgres] extra are frozen too."""
    out: list[Finding] = []
    lp = lock.packages.get(norm_name(EVALDB_PROJECT))
    if lp is None or not policy.evaldb_closure:
        return out

    def fail(note: str) -> None:
        out.append(Finding(EVALDB_PROJECT, lp.version, "?", "FAIL", note))

    own = {norm_name(EVALDB_PROJECT)}
    base = lock.closure([EVALDB_PROJECT]) - own
    expected = set(policy.evaldb_closure) | {norm_name(RUNTIME_PROJECT)}
    if base != expected:
        fail(
            f"runtime closure changed: extra {sorted(base - expected)}, "
            f"missing {sorted(expected - base)}"
        )
    extra_only = lock.closure([EVALDB_PROJECT], [EVALDB_EXTRA]) - own - base
    if extra_only != policy.evaldb_extras:
        fail(
            f"{EVALDB_EXTRA} extra closure changed: got {sorted(extra_only)}, "
            f"policy {sorted(policy.evaldb_extras)}"
        )
    for r in lp.requires_dist:
        if norm_name(r["name"]) == norm_name(RUNTIME_PROJECT):
            continue  # the workspace package: pinned exactly in pyproject, an editable source here
        if not re.fullmatch(r">=\d+(\.\d+)*,<\d+(\.\d+)*", str(r.get("specifier", ""))):
            fail(f"runtime dependency {r['name']} not declared as a bounded range (>=X,<Y)")
    return out


def phase3_findings(lock: Lock, policy: Policy) -> list[Finding]:
    """The Phase 3 packages: each closure is frozen and every non-workspace range is bounded."""
    out: list[Finding] = []
    workspace = {
        k
        for k, p in lock.packages.items()
        if "workspace" in p.source or "virtual" in p.source or "editable" in p.source
    }
    for project in PHASE3_PROJECTS:
        lp = lock.packages.get(norm_name(project))
        wanted = policy.phase3_closure.get(project)
        if lp is None or not wanted:
            continue

        def fail(note: str, project: str = project, version: str = lp.version) -> None:
            out.append(Finding(project, version, "?", "FAIL", note))

        got = lock.closure([project]) - workspace
        if got != wanted:
            fail(
                f"runtime closure changed: extra {sorted(got - wanted)}, "
                f"missing {sorted(wanted - got)}"
            )
        for r in lp.requires_dist:
            if norm_name(r["name"]) in workspace:
                continue  # pinned exactly in pyproject (the schema version of the data)
            if r.get("extra"):
                fail(f"extra dependency {r['name']}")
            elif not re.fullmatch(r">=\d+(\.\d+)*,<\d+(\.\d+)*", str(r.get("specifier", ""))):
                fail(f"runtime dependency {r['name']} not declared as a bounded range (>=X,<Y)")
    return out


def _open_artifact(path: Path) -> tuple[list[str], Callable[[str], bytes]]:
    if path.suffix == ".whl":
        z = zipfile.ZipFile(path)
        return z.namelist(), z.read
    with tarfile.open(path, "r:gz") as tf:
        names = [m.name for m in tf.getmembers() if m.isfile()]

    def read(n: str) -> bytes:
        with tarfile.open(path, "r:gz") as tf:
            f = tf.extractfile(n)
            if f is None:
                raise ValueError(f"{n} is not a regular file")
            return f.read(MAX_ARTIFACT_METADATA_BYTES)

    return names, read


def check_artifact(path: Path, policy: Policy) -> list[Finding]:
    names, read = _open_artifact(path)
    label = path.name
    out: list[Finding] = []

    def fail(note: str) -> None:
        out.append(Finding(label, "", "?", "FAIL", note))

    meta_names = [
        n
        for n in names
        if n.endswith(".dist-info/METADATA") or n.endswith("/PKG-INFO") or n == "PKG-INFO"
    ]
    if len(meta_names) != 1:
        return [
            Finding(label, "", "?", "FAIL", f"expected one METADATA/PKG-INFO, found {meta_names}")
        ]
    meta = BytesParser().parsebytes(read(meta_names[0]))
    verdict = classify_metadata(meta, policy.allowed)
    if verdict.klass != ALLOWED:
        fail(f"artifact license {CLASS_NAMES[verdict.klass]}: {verdict.declared}")
    requires = {_normal_requirement(r) for r in meta.get_all("Requires-Dist") or []}
    wheel_name = norm_name(str(meta.get("Name", "")))
    is_evaldb = wheel_name == norm_name(EVALDB_PROJECT)
    phase3 = {norm_name(n): d for n, d in policy.phase3_requires_dist.items()}
    if is_evaldb:
        wanted_dist = policy.evaldb_requires_dist
    elif wheel_name in phase3:
        wanted_dist = phase3[wheel_name]
    else:
        wanted_dist = policy.runtime_requires_dist
    wanted = {_normal_requirement(r) for r in wanted_dist}
    if wanted_dist and requires != wanted:
        fail(f"Requires-Dist {sorted(requires)} != policy {sorted(wanted)}")
    if (meta.get_all("Provides-Extra") or []) != ([EVALDB_EXTRA] if is_evaldb else []):
        fail("artifact declares unexpected extras")
    for e in policy.exceptions:
        pkg = norm_name(e["name"])
        if any((m := _REQ_NAME_RE.match(r)) and norm_name(m.group(1)) == pkg for r in requires):
            fail(f"artifact requires excepted package {e['name']}")
        for imp in e.get("import_names", []):
            if any(n.split("/", 1)[0] == imp or f"/{imp}/" in f"/{n}" for n in names):
                fail(f"artifact contains files of excepted package {e['name']} ({imp})")
        for n in names:
            low = n.rsplit("/", 1)[-1].lower()
            if low in {x.lower() for x in e.get("forbidden_files", [])}:
                fail(f"artifact contains {n} (license file of excepted package {e['name']})")
    if not out:
        out.append(Finding(label, str(meta.get("Version", "")), verdict.declared, "ok", "artifact"))
    return out


def render(findings: list[Finding]) -> str:
    lines = ["| package | version | license | status | note |", "|---|---|---|---|---|"]
    for f in findings:
        lines.append(f"| {f.name} | {f.version} | {f.spdx} | {f.status} | {f.note} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    ap.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    ap.add_argument("--pyproject", type=Path, default=ROOT / "pyproject.toml")
    ap.add_argument(
        "--path", action="append", help="dist-info parent dir to scan instead of sys.path"
    )
    ap.add_argument("--wheel", type=Path, help="directory of built .whl/.tar.gz files")
    ap.add_argument("--artifacts-only", action="store_true", help="with --wheel, skip the env scan")
    ap.add_argument("--report", type=Path, help="write the markdown table here")
    args = ap.parse_args(argv)

    policy = Policy.load(args.policy)
    findings: list[Finding] = []
    if not args.artifacts_only:
        lock = Lock.load(args.lock)
        findings += evaluate_dists(
            installed_dists(args.path), lock, policy, uv_default_groups(args.pyproject)
        )
    if args.wheel:
        arts = sorted(
            p for p in args.wheel.iterdir() if p.suffix == ".whl" or p.name.endswith(".tar.gz")
        )
        if not arts:
            findings.append(Finding(str(args.wheel), "", "?", "FAIL", "no artifacts found"))
        for a in arts:
            findings += check_artifact(a, policy)

    table = render(findings)
    if args.report:
        args.report.write_text(table, encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(table)
    bad = [f for f in findings if f.status != "ok"]
    for f in bad:
        print(f"FAIL {f.name} {f.version} [{f.spdx}]: {f.note}", file=sys.stderr)
    print(f"{len(findings) - len(bad)} ok, {len(bad)} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
