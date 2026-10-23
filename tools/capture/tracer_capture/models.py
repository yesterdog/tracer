# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Pinned local models: fetch, hash, and licence record. Public downloads only, no API keys."""

from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KOKORO_DIR = Path("~/.cache/pipecat/kokoro-onnx").expanduser()
_KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = {
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
WHISPER_REPO = "Systran/faster-whisper-small"
WHISPER_REV = "536b0662742c02347bc0e980a01041f333bce120"
LLM_REPO = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
LLM_REV = "8b403126fc14f14cfc99bb4cfa72ecbc129ea677"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class ModelRecord:
    role: str
    name: str
    source: str
    revision: str
    licence: str
    licence_source: str
    files: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "name": self.name,
            "source": self.source,
            "revision": self.revision,
            "licence": self.licence,
            "licence_source": self.licence_source,
            "files_sha256": self.files,
        }


def _hf_snapshot(repo: str, rev: str) -> Path:
    from huggingface_hub import snapshot_download

    path = Path(snapshot_download(repo, revision=rev))
    # Later loads use the repo id offline, so the cache's `main` must be the pinned revision.
    main = path.parents[1] / "refs" / "main"
    if main.read_text().strip() != rev:
        raise RuntimeError(f"{repo}: cached main is not the pinned revision {rev}")
    return path


def _hash_dir(root: Path) -> dict[str, str]:
    # Hash the resolved blobs, keyed by their snapshot-relative names.
    return {
        str(p.relative_to(root)): sha256_file(p.resolve())
        for p in sorted(root.rglob("*"))
        if p.is_file() or p.is_symlink()
    }


def ensure_models() -> tuple[dict[str, Path], list[ModelRecord]]:
    KOKORO_DIR.mkdir(parents=True, exist_ok=True)
    for name, digest in KOKORO_FILES.items():
        dest = KOKORO_DIR / name
        if not dest.exists():
            urllib.request.urlretrieve(_KOKORO_BASE + name, dest)  # noqa: S310
        if sha256_file(dest) != digest:
            raise RuntimeError(f"{name}: sha256 differs from the pinned value")
    whisper = _hf_snapshot(WHISPER_REPO, WHISPER_REV)
    llm = _hf_snapshot(LLM_REPO, LLM_REV)
    from pipecat.audio.vad import silero

    vad = Path(silero.__file__).parent / "data" / "silero_vad.onnx"
    paths = {"kokoro": KOKORO_DIR, "whisper": whisper, "llm": llm, "vad": vad}
    records = [
        ModelRecord(
            "tts",
            "Kokoro v1.0 (kokoro-onnx model files)",
            _KOKORO_BASE,
            "model-files-v1.0",
            "Apache-2.0",
            "https://github.com/thewh1teagle/kokoro-onnx (model files are Kokoro-82M, "
            "https://huggingface.co/hexgrad/Kokoro-82M, license: apache-2.0)",
            {n: sha256_file(KOKORO_DIR / n) for n in KOKORO_FILES},
        ),
        ModelRecord(
            "stt",
            WHISPER_REPO,
            f"https://huggingface.co/{WHISPER_REPO}",
            WHISPER_REV,
            "MIT",
            f"https://huggingface.co/{WHISPER_REPO} (model card license: mit)",
            _hash_dir(whisper),
        ),
        ModelRecord(
            "llm",
            LLM_REPO,
            f"https://huggingface.co/{LLM_REPO}",
            LLM_REV,
            "Apache-2.0",
            f"https://huggingface.co/{LLM_REPO} (model card license: apache-2.0)",
            _hash_dir(llm),
        ),
        ModelRecord(
            "vad",
            "Silero VAD (silero_vad.onnx bundled in pipecat-ai 1.13.0)",
            "https://github.com/snakers4/silero-vad",
            "bundled with pipecat-ai==1.13.0",
            "MIT",
            "https://github.com/snakers4/silero-vad/blob/master/LICENSE",
            {"silero_vad.onnx": sha256_file(vad)},
        ),
    ]
    return paths, records
