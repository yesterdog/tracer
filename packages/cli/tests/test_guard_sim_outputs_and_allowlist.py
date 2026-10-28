# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# ruff: noqa: E501, PT018
"""P4-30: the repository guard refuses a staged output of ``tracer simulate`` and a target allowlist.

The outputs hold personal data and the caller's audio: the stereo recording (``recording-<UTC>.wav``,
the agent's side is a real system's speech), the call log (``sim_call_log.jsonl``), the TTS cache
(``tts-cache/<64 hex>``), the usage recovery file and, in the output directory, anything that sits
next to them. A target allowlist names the hosts the operator may call and may name the environment
variable of a bearer token; it is a local, git-ignored file (ADR 0026 rule 16). This guard and the
``.gitignore`` entries keep all of them out of the repository, as ``test_guard_rejects_staged_report_
store_and_call_log`` does for the outputs of ``tracer score``. The real
``scripts/guard_private_fixtures.py`` runs in a throwaway repository (the project repository is never
touched). Contract: docs/phase4/api-surface-batch-f.md section 6.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cli_batchf_support import (  # type: ignore[import-not-found,unused-ignore]
    GIT,
    GUARD_SCRIPT,
    ROOT,
    git,
    guard_repo,
    run_guard,
)

RECORDING_MESSAGE = "tracer simulated recording tracked, staged or in history: "
CALL_LOG_MESSAGE = "tracer call log tracked, staged or in history: "
CACHE_MESSAGE = "tracer TTS cache entry tracked, staged or in history: "
ALLOWLIST_MESSAGE = "tracer target allowlist tracked, staged or in history: "
RECOVERY_MESSAGE = "tracer usage recovery file tracked, staged or in history: "
STORE_MESSAGE = "tracer store file tracked, staged or in history: "
REPORT_MESSAGE = "tracer report file tracked, staged or in history: "

HEX = "0123456789abcdef" * 4
#: A canonical 16-bit stereo WAV header and a few samples (no real audio).
WAV = (
    b"RIFF" + (36 + 16).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
    + (1).to_bytes(2, "little") + (2).to_bytes(2, "little") + (16000).to_bytes(4, "little")
    + (64000).to_bytes(4, "little") + (4).to_bytes(2, "little") + (16).to_bytes(2, "little")
    + b"data" + (16).to_bytes(4, "little") + bytes(range(16))
)  # fmt: skip
SQLITE_MAGIC = b"SQLite format 3\x00"
#: A call-log line of a simulated run (integers and short keys only; ADR 0024 amendment A8).
SIM_LOG_LINE = (
    '{"at":"2026-10-27T12:00:00.000000Z","event":"before","stage":"tts","index":0,"attempt":1,'
    '"bytes_sent":128,"characters":7,"audio_seconds":null,"status":null,"error_kind":null,'
    '"elapsed_ms":null,"request_id":null}\n'
)
RECOVERY_JSON = (
    '{"format":"tracer.usage_recovery/1","created_at":"2026-10-27T12:00:00Z","failed_step":"call",'
    '"error_class":"KeyboardInterrupt","usage_records":[],"cost_records":[],"cost_summaries":[],'
    '"pricing_catalogs":[]}\n'
)
ENTRY = "ws://127.0.0.1:8765/ws-client auth_token_env=AGENT_BEARER origin=http://localhost:3000\n"


def _stage(repo: Path, path: str, data: bytes | str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    git(repo, "add", "-f", "--", path)


def _lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return result.stderr.splitlines()


def _refused_staged_and_tracked(repo: Path, path: str, message: str) -> None:
    for state in ("staged", "tracked"):
        result = run_guard(repo)
        assert result.returncode == 1, (state, path)
        assert message + path in _lines(result), (state, path, result.stderr)
        if state == "staged":
            git(repo, "commit", "-q", "-m", "tracked")


def _rows(rows: list[tuple[str, bytes | str]]) -> pytest.MarkDecorator:
    return pytest.mark.parametrize(
        ("path", "data"), [pytest.param(path, data, id=path) for path, data in rows]
    )


# ---------------------------------------------------------------------- the named test


def test_guard_rejects_staged_sim_outputs_and_allowlist(tmp_path: Path) -> None:
    repo = guard_repo(tmp_path)
    assert run_guard(repo).returncode == 0, "a clean repository passes"
    outputs: dict[str, tuple[bytes | str, str]] = {
        "out/recording-20261027T120000Z.wav": (WAV, RECORDING_MESSAGE),
        "out/sim_call_log.jsonl": (SIM_LOG_LINE, CALL_LOG_MESSAGE),
        f"out/tts-cache/{HEX}": (b"\x00\x01\x02\x03", CACHE_MESSAGE),
        "out/usage-recovery-20261027T120000Z.json": (RECOVERY_JSON, RECOVERY_MESSAGE),
        "ops/allowlist.txt": (ENTRY, ALLOWLIST_MESSAGE),
    }
    # written but not staged: not a finding (the files are git-ignored in real use)
    for path, (data, _message) in outputs.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    assert run_guard(repo).returncode == 0, "untracked outputs do not trip the guard"
    # staged one at a time: exit 1, the path and the kind are named on stderr
    for path, (_data, message) in outputs.items():
        git(repo, "add", "-f", "--", path)
        result = run_guard(repo)
        assert result.returncode == 1, path
        assert _lines(result) == [message + path], (path, result.stderr)
        git(repo, "rm", "-q", "--cached", "--", path)
        assert run_guard(repo).returncode == 0, f"{path} unstaged again"
    # all staged: all reported, each on its own line, sorted by path
    for path in outputs:
        git(repo, "add", "-f", "--", path)
    result = run_guard(repo)
    assert result.returncode == 1
    for path, (_data, message) in outputs.items():
        assert message + path in _lines(result)
    assert len(_lines(result)) == len(outputs)
    paths = [line.rsplit(": ", 1)[1] for line in _lines(result)]
    assert paths == sorted(paths)
    # tracked after a commit: still refused
    git(repo, "commit", "-q", "-m", "synthetic")
    assert len(_lines(run_guard(repo))) == len(outputs)
    # removed from the index but still in history: refused by name
    for path in outputs:
        git(repo, "rm", "-q", "--cached", "--", path)
    git(repo, "commit", "-q", "-m", "untrack")
    history = run_guard(repo)
    assert history.returncode == 1
    for path, (_data, message) in outputs.items():
        assert message + path in _lines(history)


# ---------------------------------------------------------------------- the recordings


@pytest.mark.parametrize(
    "path",
    [
        "recording-20261027T120000Z.wav",
        "out/recording-20261027T120000Z.wav",
        "deep/er/out dir/recording-20261231T235959Z.wav",
        "café/recording-20260101T000000Z.wav",
    ],
)
def test_guard_refuses_a_recording_by_its_name_whatever_it_holds(tmp_path: Path, path: str) -> None:
    repo = guard_repo(tmp_path)
    _stage(repo, path, b"not audio\n")
    result = run_guard(repo)
    assert result.returncode == 1
    assert RECORDING_MESSAGE + path in _lines(result)


def test_guard_refuses_any_wav_in_a_directory_that_holds_an_output(tmp_path: Path) -> None:
    """The output directory is the unit: a recording renamed or copied inside it (a trimmed take, a
    per-channel export) is as private as the original. The siblings decide, not the name."""
    repo = guard_repo(tmp_path)
    for sibling, data in (
        ("tracer.sqlite", SQLITE_MAGIC + b"\x00" * 100),
        ("sim_call_log.jsonl", SIM_LOG_LINE),
        ("report-20261027T120000Z.html", "<html></html>\n"),
    ):
        folder = f"out-{sibling.split('.')[0].split('-')[0]}"
        _stage(repo, f"{folder}/take-1.wav", WAV)
        assert run_guard(repo).returncode == 0, "a lone wav is a fixture, not an output"
        _stage(repo, f"{folder}/{sibling}", data)
        result = run_guard(repo)
        assert result.returncode == 1, sibling
        assert RECORDING_MESSAGE + f"{folder}/take-1.wav" in _lines(result), result.stderr
        git(repo, "rm", "-q", "--cached", "--", f"{folder}/take-1.wav", f"{folder}/{sibling}")
        assert run_guard(repo).returncode == 0


@_rows(
    [
        ("tests/fixtures/real/clip.wav", WAV),
        ("tools/capture/clips/silence_2s.wav", WAV),
        ("docs/examples/synthetic-mr/stereo.wav", WAV),
        ("docs/recording-notes.md", "A recording-20261027T120000Z.wav is a file of a run.\n"),
        ("docs/recording-2026.wav.txt", "notes\n"),
        ("src/recording.wav.py", "NAME = 'recording-20261027T120000Z.wav'\n"),
    ],
)
def test_guard_does_not_refuse_what_is_not_a_recording(
    tmp_path: Path, path: str, data: bytes | str
) -> None:
    repo = guard_repo(tmp_path)
    _stage(repo, path, data)
    result = run_guard(repo)
    assert result.returncode == 0, (path, result.stderr)
    assert result.stderr == ""


# ---------------------------------------------------------------------- the call log, the cache


@pytest.mark.parametrize(
    "path", ["out/sim_call_log.jsonl", "sim_call_log.jsonl", "tools/other/sim_call_log.jsonl"]
)
def test_guard_refuses_a_simulated_call_log_by_its_name(tmp_path: Path, path: str) -> None:
    repo = guard_repo(tmp_path)
    _stage(repo, path, "{}\n")  # the name decides, whatever the first line says
    result = run_guard(repo)
    assert result.returncode == 1
    assert CALL_LOG_MESSAGE + path in _lines(result)


def test_guard_refuses_a_renamed_simulated_call_log_by_the_shape_of_its_first_line(
    tmp_path: Path,
) -> None:
    repo = guard_repo(tmp_path)
    _stage(repo, "out/run7-requests.jsonl", SIM_LOG_LINE + SIM_LOG_LINE)
    _refused_staged_and_tracked(repo, "out/run7-requests.jsonl", CALL_LOG_MESSAGE)


@pytest.mark.parametrize(
    "path",
    [
        f"out/tts-cache/{HEX}",
        f"tts-cache/{HEX}",
        "deep/er/out/tts-cache/anything",
        "out/tts-cache/sub/entry.bin",
    ],
)
def test_guard_refuses_everything_under_a_tts_cache_directory(tmp_path: Path, path: str) -> None:
    repo = guard_repo(tmp_path)
    _stage(repo, path, b"\x00\x01")
    result = run_guard(repo)
    assert result.returncode == 1
    assert CACHE_MESSAGE + path in _lines(result)


@_rows(
    [
        ("docs/tts-cache.md", "The cache is a directory named tts-cache.\n"),
        ("src/tts_cache.py", "NAME = 'tts-cache'\n"),
        ("docs/tts-cache-design/notes.txt", "notes\n"),
        ("packages/evalproviders/src/tracer_evalproviders/ttscache.py", "x = 1\n"),
    ],
)
def test_guard_does_not_refuse_what_is_not_a_cache_entry(
    tmp_path: Path, path: str, data: bytes | str
) -> None:
    repo = guard_repo(tmp_path)
    _stage(repo, path, data)
    result = run_guard(repo)
    assert result.returncode == 0, (path, result.stderr)


# ---------------------------------------------------------------------- the recovery file


@pytest.mark.parametrize(
    "path",
    ["usage-recovery-20261027T120000Z.json", "out/usage-recovery-20261231T235959Z.json"],
)
def test_guard_refuses_a_recovery_file_by_its_name(tmp_path: Path, path: str) -> None:
    repo = guard_repo(tmp_path)
    _stage(repo, path, "{}\n")
    result = run_guard(repo)
    assert result.returncode == 1
    assert RECOVERY_MESSAGE + path in _lines(result)


def test_guard_refuses_a_renamed_recovery_file_by_its_format_member(tmp_path: Path) -> None:
    repo = guard_repo(tmp_path)
    _stage(repo, "notes/after-the-crash.json", RECOVERY_JSON)
    _refused_staged_and_tracked(repo, "notes/after-the-crash.json", RECOVERY_MESSAGE)


def test_guard_refuses_a_recovery_file_whatever_the_order_of_its_members(tmp_path: Path) -> None:
    """A tool that sorts the keys (``jq -S``) moves ``format`` away from the first place."""
    repo = guard_repo(tmp_path)
    sorted_json = json.dumps(json.loads(RECOVERY_JSON), sort_keys=True) + "\n"
    assert not sorted_json.startswith('{"format"')
    _stage(repo, "notes/sorted.json", sorted_json)
    _refused_staged_and_tracked(repo, "notes/sorted.json", RECOVERY_MESSAGE)


@_rows(
    [
        ("docs/usage-recovery.md", "The recovery file is usage-recovery-<UTC>.json.\n"),
        ("data/usage.json", '{"format": "something-else/1"}\n'),
        ("data/usage-recovery-notes.json", '{"a": 1}\n'),
        # prose and source that quote the format member are not recovery files
        ("docs/recovery-format.md", 'The first member is `"format": "tracer.usage_recovery/1"`.\n'),
        ("src/recovery.py", 'FORMAT = {"format": "tracer.usage_recovery/1"}\n'),
    ],
)
def test_guard_does_not_refuse_what_is_not_a_recovery_file(
    tmp_path: Path, path: str, data: bytes | str
) -> None:
    repo = guard_repo(tmp_path)
    _stage(repo, path, data)
    result = run_guard(repo)
    assert result.returncode == 0, (path, result.stderr)


# ---------------------------------------------------------------------- the allowlist


@pytest.mark.parametrize(
    "path",
    [
        "allowlist",
        "allowlist.txt",
        "ops/Allowlist.TXT",
        "ops/agents.allowlist",
        "deep/er/ops/targets.allowlist",
    ],
)
def test_guard_refuses_an_allowlist_by_its_name_whatever_it_holds(
    tmp_path: Path, path: str
) -> None:
    repo = guard_repo(tmp_path)
    _stage(repo, path, "# nothing yet\n")
    result = run_guard(repo)
    assert result.returncode == 1
    assert ALLOWLIST_MESSAGE + path in _lines(result)


@_rows(
    [
        ("ops/targets.conf", ENTRY),
        ("ops/targets.conf", "# the sample agent\n\n" + ENTRY + "wss://agent.example.org:443/ws\n"),
        ("ops/t.txt", "http://localhost:7860/api/offer\n"),
        ("ops/t2.txt", "ws://127.0.0.1:8765/ws-client bot_holds_telephony_credentials=false\n"),
    ],
)
def test_guard_refuses_a_renamed_allowlist_by_its_content(
    tmp_path: Path, path: str, data: bytes | str
) -> None:
    repo = guard_repo(tmp_path)
    _stage(repo, path, data)
    _refused_staged_and_tracked(repo, path, ALLOWLIST_MESSAGE)


@_rows(
    [
        # a list of links is not an allowlist: no entry field, no runner path
        ("docs/links.txt", "https://example.org/docs\nhttps://example.org/more\n"),
        ("docs/one.txt", "https://example.org/docs\n"),
        # prose that mentions an entry is not an allowlist
        (
            "docs/allowlist-howto.md",
            "Write one entry per line, such as ws://127.0.0.1:8765/ws-client.\n",
        ),
        (
            "docs/notes.txt",
            "see ws://127.0.0.1:8765/ws-client for the sample agent\nand more text\n",
        ),
        # source that carries an entry
        ("src/entry.py", "ENTRY = 'ws://127.0.0.1:8765/ws-client'\n"),
        # a file with an entry among other lines
        ("ops/mixed.txt", ENTRY + "this line is not an entry\n"),
        ("packages/evalsim/src/tracer_evalsim/allowlist.py", "x = 1\n"),
        ("packages/evalsim/tests/test_allowlist_rules.py", "x = 1\n"),
        ("docs/phase4/allowlist-notes.md", "notes\n"),
    ],
)
def test_guard_does_not_refuse_what_is_not_an_allowlist(
    tmp_path: Path, path: str, data: bytes | str
) -> None:
    repo = guard_repo(tmp_path)
    _stage(repo, path, data)
    result = run_guard(repo)
    assert result.returncode == 0, (path, result.stderr)
    assert result.stderr == ""


# ---------------------------------------------------------------------- what stays as it was


def test_guard_still_refuses_what_it_refused_before(tmp_path: Path) -> None:
    repo = guard_repo(tmp_path)
    _stage(repo, "out/report-20261027T120000Z.html", "<html></html>\n")
    _stage(repo, "out/tracer.sqlite", SQLITE_MAGIC + b"\x00" * 100)
    _stage(repo, ".env.local", "X=1\n")
    result = run_guard(repo)
    assert result.returncode == 1
    lines = _lines(result)
    assert REPORT_MESSAGE + "out/report-20261027T120000Z.html" in lines
    assert STORE_MESSAGE + "out/tracer.sqlite" in lines
    assert any(line.endswith(": .env.local") for line in lines)
    assert not any(RECORDING_MESSAGE in line for line in lines)


def test_guard_script_adds_no_import_beyond_the_standard_library() -> None:
    import ast
    import sys

    script = ROOT / "scripts" / "guard_private_fixtures.py"
    tree = ast.parse(script.read_text("utf-8"))
    imported = {
        alias.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for alias in n.names
    } | {
        n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert imported <= set(sys.stdlib_module_names), sorted(imported - set(sys.stdlib_module_names))


def test_guard_has_no_false_positive_on_this_repository() -> None:
    """The guard is a gate of the repository (the CI job ``guard``): its new content rules must not
    refuse a file the repository legitimately holds. The real script is imported and run over the
    repository's own index, HEAD and history (``findings``, the gate itself) and over the content of
    every tracked or untracked-but-not-ignored file (the files a developer is about to add: this very
    suite quotes the recovery format, the docs quote every output name). Nothing under the private
    fixture directory is read; ignored files are not listed."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("guard_under_test", GUARD_SCRIPT)
    assert spec is not None and spec.loader is not None
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    assert guard.findings(ROOT) == []
    listed = subprocess.run(  # noqa: S603
        [GIT, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    flagged = []
    for path in sorted({p for p in listed.split("\0") if p}):
        if path.startswith(guard.PRIVATE_PREFIX) or not (ROOT / path).is_file():
            continue
        kind = guard.kind_by_name(path)
        if kind is None:
            with (ROOT / path).open("rb") as handle:
                kind = guard.kind_by_content(path, handle.read(guard.HEAD_BYTES))
        if kind is not None:
            flagged.append((path, kind))
    assert flagged == []


# ---------------------------------------------------------------------- .gitignore


def _ignored(path: str) -> bool:
    proc = subprocess.run(  # noqa: S603
        [GIT, "check-ignore", "--no-index", "-q", "--", path], cwd=ROOT, check=False
    )
    assert proc.returncode in (0, 1), proc
    return proc.returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        "out/recording-20261027T120000Z.wav",
        "deep/er/out/recording-20261231T235959Z.wav",
        "out/sim_call_log.jsonl",
        "deep/er/out/sim_call_log.jsonl",
        f"out/tts-cache/{HEX}",
        "tts-cache/anything",
        "out/usage-recovery-20261027T120000Z.json",
        "allowlist.txt",
        "ops/allowlist.txt",
        "ops/agents.allowlist",
    ],
)
def test_gitignore_lists_the_outputs_of_tracer_simulate_and_the_allowlist(path: str) -> None:
    assert _ignored(path), f"{path} is not git-ignored"


@pytest.mark.parametrize(
    "path",
    [
        "packages/evalsim/src/tracer_evalsim/allowlist.py",
        "packages/evalsim/tests/test_evalsim_fake_agent_and_allowlist.py",
        "packages/evalschema/tests/fixtures/real/pipecat-1.13.0/en/f6_stereo.wav",
        "packages/evalschema/tests/fixtures/real/sarvam-2026-10-23/clips/silence_2s.wav",
        "packages/evalproviders/src/tracer_evalproviders/simcalllog.py",
        "packages/evalproviders/src/tracer_evalproviders/ttscache.py",
        "tools/capture/sarvam_call_log.jsonl",
        "docs/adr/0026-simulated-caller-architecture.md",
        "docs/phase4/api-surface-batch-f.md",
        "docs/security/allowlist-notes.md",
    ],
)
def test_gitignore_does_not_hide_ordinary_files_of_phase_4(path: str) -> None:
    assert not _ignored(path), f"{path} is ignored: the rule is broader than the outputs"
