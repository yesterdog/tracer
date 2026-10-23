# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-15, part 2: ``schema.lock``, the drift check, the update procedure, the command line, the
CI step and what the wheel and the sdist ship.

Contract: docs/phase1/api-surface-schema-export.md (sections 3 to 6). ADR 0008 Decisions 7, 9 and
10 and invariants I10 to I12.

The lock is a canonical JSON file (same byte form as the schemas) ``{"algorithm": "sha256",
"files": {<file name>: <64 hex>}, "schema_version": <SCHEMA_VERSION>}``. The shipped directory
``tracer_evalschema/schemas/`` holds the schema files and the lock, committed, never edited by
hand; ``python -m tracer_evalschema.schema_export --write`` regenerates it and ``--check`` fails
on any difference (a changed, missing or foreign file, a lock that does not match, or, given the
lock of a released version, a change under that version).
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import pytest

from ._support import CURRENT_SCHEMA_VERSION, FIXTURES, PACKAGE_DIR, ROOT, api
from ._support_b8 import (
    FILE_NAME_FORM,
    LOCK_NAME,
    SCHEMA_SUFFIX,
    SHA256_HEX,
    SHIPPED_DIR,
    canonical_dumps,
    export_module,
    read_directory,
    rendered_files,
    run_cli,
    sha256_hex,
)

# ------------------------------------------------------------------------------ helpers


def _lock(data: bytes) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(data)
    return parsed


def _fresh(tmp_path: Path, name: str = "schemas") -> Path:
    """A directory holding exactly what the generator writes."""
    target = tmp_path / name
    export_module().export_json_schema(target)
    return target


# --------------------------------------------------------------------------- the lock bytes


def test_lock_is_canonical_json_of_version_algorithm_and_one_hash_per_file() -> None:
    """P (contract 3.1): ``render_lock(files)`` is the canonical form (sorted keys, two-space
    indent, ASCII, one LF) of exactly three members: ``algorithm`` (``sha256``), ``files`` (file
    name -> lowercase hex SHA-256 of the file's bytes, every schema file once, the lock itself
    never) and ``schema_version``. No timestamp, path, tool version or comment: the lock is
    reproducible. The hashes are recomputed here with ``hashlib``, not asked from the module."""
    module = export_module()
    files = rendered_files()
    data = module.render_lock(files)
    expected = {
        "algorithm": "sha256",
        "files": {name: sha256_hex(blob) for name, blob in sorted(files.items())},
        "schema_version": CURRENT_SCHEMA_VERSION,
    }
    assert data == canonical_dumps(expected)
    lock = _lock(data)
    assert set(lock) == {"algorithm", "files", "schema_version"}
    assert LOCK_NAME not in lock["files"]
    assert all(SHA256_HEX.fullmatch(h) for h in lock["files"].values())
    assert list(lock["files"]) == sorted(lock["files"])


def test_lock_does_not_depend_on_the_order_of_the_files_it_is_given() -> None:
    """P: ``render_lock`` sorts by file name, so a reversed or shuffled mapping gives the same
    bytes."""
    module = export_module()
    files = rendered_files()
    assert module.render_lock(dict(reversed(list(files.items())))) == module.render_lock(files)


def test_lock_hash_changes_with_exactly_the_changed_file() -> None:
    """One flipped byte in one file changes that file's hash and no other member of the lock."""
    module = export_module()
    files = dict(rendered_files())
    victim = sorted(files)[0]
    before = _lock(module.render_lock(files))["files"]
    files[victim] = files[victim] + b" "
    after = _lock(module.render_lock(files))["files"]
    assert [n for n in before if before[n] != after[n]] == [victim]


def test_render_lock_rejects_a_name_that_is_not_a_schema_file() -> None:
    """P: the lock lists schema files only. A name that is the lock itself, has a path
    separator, or lacks the suffix raises ``ValueError`` (a lock for ``../x`` must not exist)."""
    module = export_module()
    for bad in (
        LOCK_NAME,
        "../Run.schema.json",
        "sub/Run.schema.json",
        "Run.json",
        "run.schema.json",
    ):
        with pytest.raises(ValueError, match="schema file"):
            module.render_lock({bad: b"{}"})


# ------------------------------------------------------------------ the shipped directory


def test_schema_directory_is_the_package_data_directory() -> None:
    """P (contract 4): ``schema_directory()`` is ``tracer_evalschema/schemas`` next to the
    package's modules (package data, found without importing ``importlib.resources`` tricks) and
    it exists in a checkout and in an installed wheel."""
    module = export_module()
    package_root = Path(api("").__file__).resolve().parent
    assert Path(module.schema_directory()).resolve() == package_root / "schemas"
    assert (package_root / "schemas").is_dir()


def test_shipped_directory_holds_only_the_schema_files_and_the_lock() -> None:
    """Nothing else is package data under ``schemas/``: no README, no ``.py``, no editor file, no
    subdirectory. Every name is ``<Class>.schema.json`` or ``schema.lock``."""
    entries = sorted(SHIPPED_DIR.iterdir())
    assert entries, f"{SHIPPED_DIR} does not exist or is empty"
    for entry in entries:
        assert entry.is_file(), entry.name
        assert not entry.is_symlink(), entry.name
        assert entry.name == LOCK_NAME or FILE_NAME_FORM.fullmatch(entry.name), entry.name


def test_shipped_schemas_match_generated() -> None:
    """ADR 0008 Decision 9 / SCOPE name ``test_jsonschema_byte_identical_two_runs`` in CI form: the
    committed directory equals the generator's output byte for byte, file for file, and the
    committed lock equals ``render_lock`` of that output. This is the check that fails on drift
    when a model changes and ``--write`` was not run."""
    module = export_module()
    files = rendered_files()
    shipped = read_directory(SHIPPED_DIR)
    assert set(shipped) == {*files, LOCK_NAME}
    for name, data in files.items():
        assert shipped[name] == data, f"{name} differs from the generated schema"
    assert shipped[LOCK_NAME] == module.render_lock(files)


def test_schema_lock_matches_version() -> None:
    """ADR 0008 I11 (SCOPE name): the committed lock records ``SCHEMA_VERSION``, the package
    version equals it, and every committed file's ``$id`` carries the same version. A version bump
    without regenerating (or the reverse) fails here."""
    lock = _lock((SHIPPED_DIR / LOCK_NAME).read_bytes())
    package = api("")
    assert (
        lock["schema_version"]
        == package.SCHEMA_VERSION
        == package.__version__
        == CURRENT_SCHEMA_VERSION
    )
    for path in sorted(SHIPPED_DIR.glob("*" + SCHEMA_SUFFIX)):
        schema = json.loads(path.read_bytes())
        assert schema["$id"].split(":")[3] == lock["schema_version"], path.name
        assert sha256_hex(path.read_bytes()) == lock["files"][path.name], path.name
    on_disk = {p.name for p in SHIPPED_DIR.glob("*" + SCHEMA_SUFFIX)}
    assert on_disk == set(lock["files"])


def test_check_schema_directory_reports_nothing_for_the_shipped_and_a_fresh_directory(
    tmp_path: Path,
) -> None:
    """``check_schema_directory`` returns an empty list for the committed directory and for a
    directory the generator just wrote."""
    module = export_module()
    assert module.check_schema_directory(SHIPPED_DIR) == []
    assert module.check_schema_directory(str(_fresh(tmp_path))) == []


# ------------------------------------------------------------------- negative controls (drift)


def _flip(path: Path) -> None:
    data = path.read_bytes()
    path.write_bytes(data.replace(b'"type": "object"', b'"type": "objects"', 1))
    assert path.read_bytes() != data


def _tamper_changed_file(directory: Path) -> str:
    _flip(directory / "Run.schema.json")
    return "[changed] Run.schema.json"


def _tamper_missing_file(directory: Path) -> str:
    (directory / "Turn.schema.json").unlink()
    return "[missing] Turn.schema.json"


def _tamper_extra_schema_file(directory: Path) -> str:
    (directory / "Zombie.schema.json").write_bytes(b"{}\n")
    return "[extra] Zombie.schema.json"


def _tamper_foreign_file(directory: Path) -> str:
    (directory / "NOTES.txt").write_text("hand-written\n", encoding="utf-8")
    return "[extra] NOTES.txt"


def _tamper_lock_missing(directory: Path) -> str:
    (directory / LOCK_NAME).unlink()
    return "[lock]"


def _tamper_lock_hash(directory: Path) -> str:
    lock = _lock((directory / LOCK_NAME).read_bytes())
    lock["files"]["Run.schema.json"] = "0" * 64
    (directory / LOCK_NAME).write_bytes(canonical_dumps(lock))
    return "[lock]"


def _tamper_lock_old_version(directory: Path) -> str:
    lock = _lock((directory / LOCK_NAME).read_bytes())
    lock["schema_version"] = "0.0.0"
    (directory / LOCK_NAME).write_bytes(canonical_dumps(lock))
    return "[lock]"


def _tamper_lock_not_canonical(directory: Path) -> str:
    lock = _lock((directory / LOCK_NAME).read_bytes())
    (directory / LOCK_NAME).write_text(json.dumps(lock), encoding="utf-8")
    return "[lock]"


def _tamper_file_and_lock_together(directory: Path) -> str:
    """A hand edit hidden by editing the lock to match: still drift against the generator."""
    _flip(directory / "Run.schema.json")
    lock = _lock((directory / LOCK_NAME).read_bytes())
    lock["files"]["Run.schema.json"] = sha256_hex((directory / "Run.schema.json").read_bytes())
    (directory / LOCK_NAME).write_bytes(canonical_dumps(lock))
    return "[changed] Run.schema.json"


def _tamper_lock_garbage(directory: Path) -> str:
    (directory / LOCK_NAME).write_bytes(b"\x00\xff not json")
    return "[lock]"


TAMPERS = {
    "changed schema file": _tamper_changed_file,
    "missing schema file": _tamper_missing_file,
    "extra schema file": _tamper_extra_schema_file,
    "foreign file": _tamper_foreign_file,
    "lock missing": _tamper_lock_missing,
    "lock hash edited": _tamper_lock_hash,
    "lock records another version": _tamper_lock_old_version,
    "lock not canonical": _tamper_lock_not_canonical,
    "file and lock edited together": _tamper_file_and_lock_together,
    "lock is not json": _tamper_lock_garbage,
}


@pytest.mark.parametrize("label", sorted(TAMPERS))
def test_schema_change_without_version_bump_fails(label: str, tmp_path: Path) -> None:
    """ADR 0008 I11 negative control (SCOPE name ``test_schema_change_without_version_bump_fails``):
    every way the committed directory can drift from the generator is reported by
    ``check_schema_directory`` with a tagged line that names the file (``[changed]``,
    ``[missing]``, ``[extra]``) or the lock (``[lock]``), and never quotes a path of the machine.
    A file edited together with its lock entry is still ``[changed]``: the generator, not the
    lock, is the reference."""
    module = export_module()
    directory = _fresh(tmp_path)
    assert module.check_schema_directory(directory) == []
    expected = TAMPERS[label](directory)
    problems = module.check_schema_directory(directory)
    assert problems, label
    assert any(p.startswith(expected) for p in problems), (expected, problems)
    assert not any(str(tmp_path) in p for p in problems), problems
    assert all(isinstance(p, str) and p.startswith("[") for p in problems)


def test_a_model_change_is_drift_until_the_directory_is_regenerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real scenario behind I11, with the generator changed instead of a file: the schemas a
    model change produces differ from the committed ones, so the check fails until ``--write``
    runs. Simulated by replacing ``render_schemas`` (``check_schema_directory`` and
    ``export_json_schema`` call the module-level name, contract 5.2)."""
    module = export_module()
    directory = _fresh(tmp_path)
    original = module.render_schemas

    def changed() -> dict[str, bytes]:
        files = dict(original())
        doc = json.loads(files["Run.schema.json"])
        doc["description"] = doc["description"] + " (changed)"
        files["Run.schema.json"] = canonical_dumps(doc)
        return files

    monkeypatch.setattr(module, "render_schemas", changed)
    problems = module.check_schema_directory(directory)
    assert "[changed] Run.schema.json" in problems
    assert any(p.startswith("[lock]") for p in problems)
    module.export_json_schema(directory)
    assert module.check_schema_directory(directory) == []


def test_released_version_forbids_schema_changes_under_the_same_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0008 Decision 7 ("Changing a schema file without bumping the version fails CI"): once
    a version is released its lock is recorded (``tests/fixtures/frozen/<V>/schema.lock``). Given
    that lock as ``released_locks={V: bytes}``, a directory whose lock differs is reported
    ``[released]`` even though it is consistent with the generator; an older version's recorded
    lock only has to be a valid lock of that version."""
    module = export_module()
    directory = _fresh(tmp_path)
    released = {CURRENT_SCHEMA_VERSION: (directory / LOCK_NAME).read_bytes()}
    assert module.check_schema_directory(directory, released_locks=released) == []

    original = module.render_schemas

    def changed() -> dict[str, bytes]:
        files = dict(original())
        doc = json.loads(files["Turn.schema.json"])
        doc["title"] = "Turn"
        doc["description"] = "changed after release"
        files["Turn.schema.json"] = canonical_dumps(doc)
        return files

    monkeypatch.setattr(module, "render_schemas", changed)
    module.export_json_schema(directory)
    assert module.check_schema_directory(directory) == []
    problems = module.check_schema_directory(directory, released_locks=released)
    assert any(p.startswith("[released]") for p in problems), problems
    assert not any(str(tmp_path) in p for p in problems)

    older = canonical_dumps({"algorithm": "sha256", "files": {}, "schema_version": "0.0.0"})
    ok = module.check_schema_directory(directory, released_locks={"0.0.0": older})
    assert not any(p.startswith("[released]") for p in ok)
    for bad in (b"not json", canonical_dumps({"schema_version": "9.9.9"})):
        problems = module.check_schema_directory(directory, released_locks={"0.0.0": bad})
        assert any(p.startswith("[released]") for p in problems), bad


def test_released_locks_in_the_frozen_fixture_directories_are_immutable() -> None:
    """ADR 0008 Decision 5 and I18 for the schemas: every ``tests/fixtures/frozen/<V>/schema.lock``
    that exists records version ``<V>``; when ``<V>`` is the current version the committed lock
    equals it byte for byte. The test iterates in its body (no empty ``parametrize``), so it never
    skips; with no released version it checks the committed directory against an empty record."""
    module = export_module()
    released: dict[str, bytes] = {}
    frozen = FIXTURES / "frozen"
    if frozen.is_dir():
        for directory in sorted(p for p in frozen.iterdir() if p.is_dir()):
            lock = directory / LOCK_NAME
            if lock.is_file():
                released[directory.name] = lock.read_bytes()
    for version, data in released.items():
        assert _lock(data)["schema_version"] == version, version
    assert module.check_schema_directory(SHIPPED_DIR, released_locks=released) == []


# ----------------------------------------------------------------------- update procedure


def test_update_procedure_regenerates_stale_directories_and_is_idempotent(tmp_path: Path) -> None:
    """The documented procedure (contract 5.1): ``export_json_schema(directory)`` brings any
    drifted directory back to the generator's output. It removes a stale ``*.schema.json`` (an
    entity that no longer exists), rewrites changed files and the lock, leaves files that are not
    schema files alone (the check still reports them, ``[extra]``), and a second run changes no
    byte."""
    module = export_module()
    directory = _fresh(tmp_path)
    _flip(directory / "Run.schema.json")
    (directory / "Gone.schema.json").write_bytes(b"{}\n")
    (directory / LOCK_NAME).unlink()
    (directory / "NOTES.txt").write_text("keep me\n", encoding="utf-8")
    written = module.export_json_schema(directory)
    assert written == sorted([*rendered_files(), LOCK_NAME])
    assert not (directory / "Gone.schema.json").exists()
    assert (directory / "NOTES.txt").read_text(encoding="utf-8") == "keep me\n"
    assert module.check_schema_directory(directory) == ["[extra] NOTES.txt"]
    (directory / "NOTES.txt").unlink()
    assert module.check_schema_directory(directory) == []
    snapshot = read_directory(directory)
    module.export_json_schema(directory)
    assert read_directory(directory) == snapshot


# ---------------------------------------------------------------------------- command line


def test_cli_check_exit_codes_and_messages(tmp_path: Path) -> None:
    """``python -m tracer_evalschema.schema_export --check [--dir DIR]``: exit 0 and no output on a
    clean directory; exit 1 on drift with one tagged line per problem on stderr (the same lines
    ``check_schema_directory`` returns), no traceback and no path of the machine; a missing
    directory is drift (every file ``[missing]``), not a crash. Run from another working
    directory, so the default directory is found from the package, not the cwd."""
    directory = _fresh(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    clean = run_cli(["--check", "--dir", str(directory)], cwd=elsewhere)
    assert (clean.returncode, clean.stderr.strip()) == (0, "")
    _flip(directory / "Run.schema.json")
    drift = run_cli(["--check", "--dir", str(directory)], cwd=elsewhere)
    assert drift.returncode == 1
    assert "[changed] Run.schema.json" in drift.stderr.splitlines()
    assert "Traceback" not in drift.stderr
    assert str(tmp_path) not in drift.stderr + drift.stdout
    absent = run_cli(["--check", "--dir", str(tmp_path / "nope")], cwd=elsewhere)
    assert absent.returncode == 1
    assert "Traceback" not in absent.stderr
    assert "[missing] Run.schema.json" in absent.stderr.splitlines()
    default = run_cli(["--check"], cwd=elsewhere)
    assert (default.returncode, default.stderr.strip()) == (0, "")


def test_cli_write_then_check(tmp_path: Path) -> None:
    """``--write --dir DIR`` regenerates (exit 0), a following ``--check`` passes, and the bytes
    equal ``render_schemas``; ``--write`` on a drifted directory repairs it."""
    directory = tmp_path / "out"
    written = run_cli(["--write", "--dir", str(directory)])
    assert written.returncode == 0, written.stderr
    assert run_cli(["--check", "--dir", str(directory)]).returncode == 0
    assert read_directory(directory) == {
        **rendered_files(),
        LOCK_NAME: export_module().render_lock(rendered_files()),
    }
    _flip(directory / "Run.schema.json")
    assert run_cli(["--check", "--dir", str(directory)]).returncode == 1
    assert run_cli(["--write", "--dir", str(directory)]).returncode == 0
    assert run_cli(["--check", "--dir", str(directory)]).returncode == 0


@pytest.mark.parametrize(
    "args",
    [[], ["--write", "--check"], ["--frobnicate"], ["--dir"]],
    ids=["no-mode", "both-modes", "unknown-flag", "dir-without-value"],
)
def test_cli_usage_errors_exit_with_code_2(args: list[str]) -> None:
    """Usage errors (no mode, both modes, an unknown flag, a flag without value) exit with code 2
    and a usage line, never with 0 or a traceback."""
    result = run_cli(args)
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()
    assert "Traceback" not in result.stderr


def test_cli_check_of_the_shipped_directory_passes() -> None:
    """The command CI runs: no arguments beyond ``--check`` and exit code 0."""
    result = run_cli(["--check"])
    assert (result.returncode, result.stderr.strip()) == (0, "")


def test_ci_workflow_runs_the_drift_check() -> None:
    """The CI check that fails on drift (SCOPE P1-15, ADR 0008 Decision 9): the workflow has a
    step ``run:`` that calls ``python -m tracer_evalschema.schema_export --check`` through
    ``uv run --locked``, in the lint job or the test job (any job; one step is enough)."""
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    steps = [line.strip() for line in text.splitlines() if line.strip().startswith("- run:")]
    assert any(
        "uv run --locked" in s and "-m tracer_evalschema.schema_export --check" in s for s in steps
    ), steps


def test_gitattributes_keeps_generated_files_byte_stable_on_every_platform() -> None:
    """Hashes are of bytes, so a Windows checkout must not turn LF into CRLF: the repository's
    ``.gitattributes`` has a line for the shipped directory (or ``*.schema.json`` and
    ``schema.lock``) that sets ``-text``, ``binary`` or ``eol=lf``."""
    path = ROOT / ".gitattributes"
    assert path.is_file(), ".gitattributes is missing"
    rules = [
        line.split()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]

    def covers(pattern: str, name: str) -> bool:
        return "schemas" in pattern or pattern.endswith(name) or pattern == "*.schema.json"

    def stable(attrs: list[str]) -> bool:
        return any(a in ("-text", "binary", "eol=lf", "text eol=lf") for a in attrs)

    assert any(covers(r[0], "*.schema.json") and stable(r[1:]) for r in rules), rules
    assert any(covers(r[0], "schema.lock") and stable(r[1:]) for r in rules), rules


# ------------------------------------------------------------- the hash helper and imports


def test_digest_sha256_hex_helper_matches_hashlib() -> None:
    """P (contract 5.3, ambiguity A6): ``test_digest_verification_single_entry_point`` forbids any
    module but ``digest.py`` to import ``hashlib``, so the generator hashes through
    ``digest.sha256_hex(data: bytes) -> str`` (lowercase hex SHA-256)."""
    digest = api("digest")
    for data in (b"", b"abc", bytes(range(256)), "\u0928\u092e\u0938\u094d\u0924\u0947".encode()):
        assert digest.sha256_hex(data) == sha256_hex(data)
    with pytest.raises(TypeError):
        digest.sha256_hex("text")


def test_schema_export_does_not_hash_or_compare_digests_itself() -> None:
    """Same rule as ``test_digest_verification_single_entry_point``, stated for the new module so
    the failure names it: no ``import hashlib``, ``import hmac``, ``compare_digest`` or
    ``hashlib.`` call in ``schema_export.py``."""
    source = Path(export_module().__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
    }
    assert not {"hashlib", "hmac", "rfc8785"} & imported
    assert "compare_digest" not in source


def test_generator_adds_no_runtime_dependency() -> None:
    """ADR 0008 Decision 10: the runtime dependencies of ``tracer-evalschema`` stay pydantic and
    rfc8785; the generator, ``jsonschema`` and ``hypothesis`` are not among them (the last two
    are test-only, in the workspace root's ``test`` group)."""
    meta = tomllib.loads((PACKAGE_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    names = sorted(
        re.split(r"[<>=!~ \[]", dep, maxsplit=1)[0].lower()
        for dep in meta["project"]["dependencies"]
    )
    assert names == ["pydantic", "rfc8785"]
    root = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    test_group = " ".join(root["dependency-groups"]["test"])
    assert "jsonschema" in test_group
    assert "hypothesis" in test_group


# -------------------------------------------------------------------- wheel and sdist content


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(  # noqa: S603 - fixed interpreter and module, no shell
        [sys.executable, "-m", "hatchling", "build", "-d", str(out)],
        cwd=PACKAGE_DIR,
        check=True,
        capture_output=True,
    )
    wheels, sdists = sorted(out.glob("*.whl")), sorted(out.glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1
    return wheels[0], sdists[0]


def test_wheel_ships_the_generated_schemas_byte_for_byte(built: tuple[Path, Path]) -> None:
    """ADR 0008 Decision 7 ("Files ship inside the wheel") and OQ1; P (contract 6): the wheel has
    ``tracer_evalschema/schemas/<Class>.schema.json`` for every entity and
    ``tracer_evalschema/schemas/schema.lock``, each equal to the generator's bytes, and nothing
    else under ``schemas/`` (no ``.py``). Pinned here as package data, not generated at install
    time: a consumer in another language reads static files."""
    wheel, _sdist = built
    files = {**rendered_files(), LOCK_NAME: export_module().render_lock(rendered_files())}
    with zipfile.ZipFile(wheel) as archive:
        prefix = "tracer_evalschema/schemas/"
        shipped = {
            n.removeprefix(prefix): archive.read(n)
            for n in archive.namelist()
            if n.startswith(prefix) and not n.endswith("/")
        }
    assert shipped == files


def test_sdist_ships_the_same_schemas(built: tuple[Path, Path]) -> None:
    """The sdist is a source distribution that must rebuild the same wheel, so it carries the
    committed schema files and the lock (identical bytes) under the package's ``src`` tree."""
    _wheel, sdist = built
    files = {**rendered_files(), LOCK_NAME: export_module().render_lock(rendered_files())}
    marker = "/src/tracer_evalschema/schemas/"
    with tarfile.open(sdist) as archive:
        shipped: dict[str, bytes] = {}
        for member in archive.getmembers():
            if marker in "/" + member.name and member.isfile():
                extracted = archive.extractfile(member)
                assert extracted is not None
                shipped[member.name.rsplit("/", 1)[1]] = extracted.read()
    assert shipped == files


def _sdist_members(sdist: Path) -> list[tarfile.TarInfo]:
    with tarfile.open(sdist) as archive:
        return archive.getmembers()


def test_sdist_contains_no_tests_directory_and_no_captured_data(
    built: tuple[Path, Path],
) -> None:
    """A25 (auto-approved; supersedes the sdist half of A23): the sdist ships NO tests. No member
    has a ``tests`` or ``fixtures`` path component (the tests need the repository: tests/fixtures/
    real, tests/support, scripts/, docs/ and the root pyproject, so they cannot run from an
    unpacked sdist), and none sits under ``private`` or ``real`` (provider captures and call
    data never ship). What it does hold is the package source, README, LICENSE, NOTICE and the
    pyproject."""
    _wheel, sdist = built
    names = [m.name for m in _sdist_members(sdist)]
    assert names
    forbidden = {"tests", "fixtures", "private", "real", "docs", "scripts"}
    offenders = [n for n in names if forbidden & set(n.split("/")[1:])]
    assert offenders == [], offenders
    top = {n.split("/", 1)[0] for n in names}
    assert len(top) == 1, top
    root = top.pop()
    required = {
        f"{root}/pyproject.toml",
        f"{root}/README.md",
        f"{root}/LICENSE",
        f"{root}/NOTICE",
        f"{root}/src/tracer_evalschema/__init__.py",
    }
    assert required <= set(names), sorted(required - set(names))


def test_sdist_ships_27_schema_files_byte_identical_to_the_repository(
    built: tuple[Path, Path],
) -> None:
    """A25: the sdist holds the 26 ``<Class>.schema.json`` files and ``schema.lock`` (27 files),
    each byte-identical to the ones committed under ``src/tracer_evalschema/schemas`` in the
    repository (not only to the generator's output: the committed file is what a consumer
    reads), and no other file under ``schemas/``."""
    _wheel, sdist = built
    committed_dir = PACKAGE_DIR / "src" / "tracer_evalschema" / "schemas"
    committed = {p.name: p.read_bytes() for p in committed_dir.iterdir() if p.is_file()}
    assert len(committed) == 27, sorted(committed)
    marker = "/src/tracer_evalschema/schemas/"
    shipped: dict[str, bytes] = {}
    with tarfile.open(sdist) as archive:
        for member in archive.getmembers():
            if marker in "/" + member.name and member.isfile():
                extracted = archive.extractfile(member)
                assert extracted is not None
                shipped[member.name.rsplit("/", 1)[1]] = extracted.read()
    assert shipped == committed


SDIST_ROOT_FILES = (
    "README.md",
    "LICENSE",
    "NOTICE",
    "SCHEMA_POLICY.md",
    "CHANGELOG.md",
    "pyproject.toml",
    "PKG-INFO",
    ".gitignore",
)
SDIST_JUNK = {"__pycache__", ".DS_Store"}


def _package_source_files() -> list[str]:
    """Every file below ``src/tracer_evalschema`` in the checkout, relative to the package
    directory, without bytecode caches and editor litter."""
    base = PACKAGE_DIR / "src" / "tracer_evalschema"
    return sorted(
        path.relative_to(PACKAGE_DIR).as_posix()
        for path in base.rglob("*")
        if path.is_file()
        and not SDIST_JUNK & set(path.parts)
        and path.suffix not in {".pyc", ".pyo"}
    )


def test_sdist_members_are_exactly_the_package_and_the_documents(
    built: tuple[Path, Path],
) -> None:
    """A26 (with A30): the sdist holds exactly these files and nothing else: every file below
    ``src/tracer_evalschema/`` (the modules, ``py.typed``, the data files and the 27 schema
    files), ``README.md``, ``LICENSE``, ``NOTICE``, ``SCHEMA_POLICY.md``, ``CHANGELOG.md``,
    ``pyproject.toml`` and the two files hatchling adds, ``PKG-INFO`` and ``.gitignore``. No
    tests, no fixtures, no scripts, no docs, no directory entries and no link members. All
    members sit below one top directory ``<name>-<version>/``, and the member list has no
    duplicates."""
    _wheel, sdist = built
    members = _sdist_members(sdist)
    names = [m.name for m in members]
    assert len(names) == len(set(names)), "duplicate members"
    assert all(m.isfile() for m in members), [m.name for m in members if not m.isfile()]
    tops = {n.split("/", 1)[0] for n in names}
    assert len(tops) == 1, tops
    (top,) = tops
    assert top == "tracer_evalschema-0.0.1", top
    relative = sorted(n.removeprefix(top + "/") for n in names)
    expected = sorted([*_package_source_files(), *SDIST_ROOT_FILES])
    assert relative == expected, {
        "unexpected": sorted(set(relative) - set(expected)),
        "missing": sorted(set(expected) - set(relative)),
    }
    schemas = [n for n in relative if n.startswith("src/tracer_evalschema/schemas/")]
    assert len(schemas) == 27, schemas
    assert sum(n.endswith(".schema.json") for n in schemas) == 26
    assert "src/tracer_evalschema/schemas/schema.lock" in schemas
    tests = [
        n
        for n in relative
        if any(
            part in {"tests", "test", "conftest.py"} or part.startswith("test_")
            for part in n.split("/")
        )
    ]
    assert tests == [], tests


def test_sdist_members_exactness_check_catches_extras_and_gaps() -> None:
    """Not vacuous: the comparison used above reports a planted extra file and a planted gap, and
    the package listing excludes bytecode caches."""
    listing = _package_source_files()
    assert "src/tracer_evalschema/__init__.py" in listing
    assert "src/tracer_evalschema/py.typed" in listing
    assert not [n for n in listing if "__pycache__" in n or n.endswith(".pyc")]
    expected = sorted([*listing, *SDIST_ROOT_FILES])
    with_extra = sorted([*expected, "tests/test_x.py"])
    assert with_extra != expected
    assert sorted(set(expected) - {"NOTICE"}) != expected


def test_wheel_built_from_the_unpacked_sdist_passes_the_drift_check(
    built: tuple[Path, Path], tmp_path: Path
) -> None:
    """A25: the sdist must rebuild. It is unpacked into an empty directory, a wheel is built from
    it there (no access to the repository), the wheel is unpacked, and
    ``python -m tracer_evalschema.schema_export --check`` run FROM THAT WHEEL's code exits 0
    ("no drift"; the module under check is asserted to be the unpacked one, not the checkout).
    Its schema files equal the repository's byte for byte. Never skipped: ``hatchling`` is a
    pinned build requirement and ``built`` fails hard without it."""
    _wheel, sdist = built
    unpacked = tmp_path / "sdist"
    unpacked.mkdir()
    with tarfile.open(sdist) as archive:
        if hasattr(tarfile, "data_filter"):
            archive.extractall(unpacked, filter="data")
        else:  # pragma: no cover - Python 3.11 before 3.11.4
            for member in archive.getmembers():
                assert not Path(member.name).is_absolute()
                assert ".." not in Path(member.name).parts
            archive.extractall(unpacked)  # noqa: S202 - members checked above
    (root,) = [p for p in unpacked.iterdir() if p.is_dir()]
    out = tmp_path / "wheel-dist"
    result = subprocess.run(  # noqa: S603 - fixed interpreter and module, no shell
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(out)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    (wheel,) = sorted(out.glob("*.whl"))
    site = tmp_path / "site"
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert all(not n.startswith("/") and ".." not in n.split("/") for n in names)
        archive.extractall(site)  # noqa: S202 - member names checked on the line above
    assert not [n for n in names if n.split("/")[0] in {"tests", "fixtures"}]
    runner = (
        "import runpy, sys, tracer_evalschema, pathlib\n"
        "site = pathlib.Path(sys.argv[1]).resolve()\n"
        "sys.path.insert(0, str(site))\n"
        "for name in [m for m in sys.modules if m.startswith('tracer_evalschema')]:\n"
        "    del sys.modules[name]\n"
        "import tracer_evalschema\n"
        "assert pathlib.Path(tracer_evalschema.__file__).resolve().is_relative_to(site)\n"
        "sys.argv = ['schema_export', '--check']\n"
        "try:\n"
        "    runpy.run_module('tracer_evalschema.schema_export', run_name='__main__')\n"
        "except SystemExit as stop:\n"
        "    sys.exit(stop.code)\n"
    )
    checked = subprocess.run(  # noqa: S603 - fixed interpreter, no shell
        [sys.executable, "-I", "-c", runner, str(site)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, (checked.stdout + checked.stderr)[-2000:]
    committed_dir = PACKAGE_DIR / "src" / "tracer_evalschema" / "schemas"
    committed = {p.name: p.read_bytes() for p in committed_dir.iterdir() if p.is_file()}
    rebuilt_dir = site / "tracer_evalschema" / "schemas"
    rebuilt = {p.name: p.read_bytes() for p in rebuilt_dir.iterdir() if p.is_file()}
    assert rebuilt == committed


def test_wheel_requirements_are_unchanged_by_the_generator(built: tuple[Path, Path]) -> None:
    """The wheel metadata requires exactly pydantic and rfc8785 (pinned as before); neither
    ``jsonschema`` nor ``hypothesis`` appears in METADATA or RECORD, and no generated file is a
    Python module."""
    wheel, _sdist = built
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(
            archive.read(n).decode() for n in archive.namelist() if n.endswith("/METADATA")
        )
        names = archive.namelist()
    requires = sorted(
        re.split(r"[<>=!~ ;\[(]", line.split(":", 1)[1].strip(), maxsplit=1)[0].lower()
        for line in metadata.splitlines()
        if line.startswith("Requires-Dist:")
    )
    assert requires == ["pydantic", "rfc8785"]
    assert "jsonschema" not in metadata.lower()
    assert "hypothesis" not in metadata.lower()
    assert not [n for n in names if "/schemas/" in n and n.endswith(".py")]
