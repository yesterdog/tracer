# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""F3 capture: Sarvam speech-to-text REST responses for synthetic clips.

    cd tools/capture && uv run python -m tracer_capture.sarvam_capture --env-file ../../.env.local

Clips are synthetic only: Sarvam TTS output for invented sentences, and the left (caller) channel
of the Kokoro recordings already captured under fixtures/real/pipecat-1.13.0.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import importlib.metadata as md
import io
import json
import platform
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from tracer_capture.sarvam import SarvamClient, load_key

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "packages/evalschema/tests/fixtures/real"
PIPECAT = FIXTURES / "pipecat-1.13.0"
CALL_LOG = REPO / "tools/capture/sarvam_call_log.jsonl"
DOC_URLS = {
    "speech_to_text": "https://docs.sarvam.ai/api-reference/speech-to-text/transcribe",
    "text_to_speech": "https://docs.sarvam.ai/api-reference/text-to-speech/convert",
}
STT_PATH = "/speech-to-text"
TTS_PATH = "/text-to-speech"
TTS_MODEL = "bulbul:v3"
SAMPLE_RATE = 16000

# Invented, harmless sentences (library opening hours). Native review still needed.
SENTENCES = {
    "en": ("en-IN", "Hello, I would like to check the opening hours of the library tomorrow."),
    "hi": ("hi-IN", "नमस्ते, मैं कल पुस्तकालय के खुलने का समय जानना चाहता हूँ।"),
    "mr": ("mr-IN", "नमस्कार, मला उद्या ग्रंथालय किती वाजता उघडते ते जाणून घ्यायचे आहे."),
    "te": ("te-IN", "నమస్కారం, రేపు గ్రంథాలయం ఎన్ని గంటలకు తెరుస్తారో తెలుసుకోవాలనుకుంటున్నాను."),
    "ta": ("ta-IN", "வணக்கம், நாளை நூலகம் எத்தனை மணிக்குத் திறக்கும் என்பதை அறிய விரும்புகிறேன்."),
    "kn": ("kn-IN", "ನಮಸ್ಕಾರ, ನಾಳೆ ಗ್ರಂಥಾಲಯ ಎಷ್ಟು ಗಂಟೆಗೆ ತೆರೆಯುತ್ತದೆ ಎಂದು ತಿಳಿಯಲು ಬಯಸುತ್ತೇನೆ."),
    "ml": ("ml-IN", "നമസ്കാരം, നാളെ ലൈബ്രറി എത്ര മണിക്ക് തുറക്കുമെന്ന് അറിയാൻ ആഗ്രഹിക്കുന്നു."),
    "pa": ("pa-IN", "ਸਤ ਸ੍ਰੀ ਅਕਾਲ, ਮੈਂ ਕੱਲ੍ਹ ਲਾਇਬ੍ਰੇਰੀ ਦੇ ਖੁੱਲ੍ਹਣ ਦਾ ਸਮਾਂ ਜਾਣਨਾ ਚਾਹੁੰਦਾ ਹਾਂ।"),
    "gu": ("gu-IN", "નમસ્તે, મારે કાલે પુસ્તકાલય કેટલા વાગ્યે ખુલે છે તે જાણવું છે."),
}
KOKORO = {"kokoro_en": ("en", "en"), "kokoro_hi": ("hi", "hi"), "kokoro_en_tool": ("en_tool", "en")}

# (case id, clip id, form fields) ; language_code None means the field is omitted.
STT_CASES: list[tuple[str, str, dict[str, str]]] = []
for _l, (_code, _) in SENTENCES.items():
    STT_CASES.append((f"stt_{_l}_forced", f"tts_{_l}", {"language_code": _code}))
for _l, (_code, _) in SENTENCES.items():
    STT_CASES.append((f"stt_{_l}_unknown", f"tts_{_l}", {"language_code": "unknown"}))
STT_CASES += [
    ("stt_en_field_omitted", "tts_en", {}),
    ("stt_hi_timestamps", "tts_hi", {"language_code": "hi-IN", "with_timestamps": "true"}),
    ("stt_en_timestamps", "tts_en", {"language_code": "en-IN", "with_timestamps": "true"}),
    (
        "stt_ta_timestamps_unknown",
        "tts_ta",
        {"language_code": "unknown", "with_timestamps": "true"},
    ),
    ("stt_hi_clip_forced_ta", "tts_hi", {"language_code": "ta-IN"}),
    ("stt_en_clip_forced_hi", "tts_en", {"language_code": "hi-IN"}),
    ("stt_silence_forced_hi", "silence_2s", {"language_code": "hi-IN"}),
    ("stt_silence_unknown", "silence_2s", {"language_code": "unknown"}),
    ("stt_short_300ms_unknown", "short_300ms", {"language_code": "unknown"}),
    ("stt_kokoro_en_unknown", "kokoro_en", {"language_code": "unknown"}),
    ("stt_kokoro_hi_unknown", "kokoro_hi", {"language_code": "unknown"}),
    ("stt_kokoro_en_tool_forced", "kokoro_en_tool", {"language_code": "en-IN"}),
    (
        "stt_hi_mode_translate_v3",
        "tts_hi",
        {"language_code": "hi-IN", "model": "saaras:v3", "mode": "translate"},
    ),
    ("err_invalid_language_code", "tts_en", {"language_code": "xx-YY"}),
    ("err_underscore_language_code", "tts_hi", {"language_code": "hi_IN"}),
    ("err_bn_in_unrelated_clip", "tts_hi", {"language_code": "bn-IN"}),
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, samples, rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def make_tts_clips(client: SarvamClient, out: Path) -> dict[str, bytes]:
    clips: dict[str, bytes] = {}
    (out / "clips").mkdir(parents=True, exist_ok=True)
    for lang, (code, text) in SENTENCES.items():
        clip_id = f"tts_{lang}"
        path = out / "clips" / f"{clip_id}.wav"
        meta_path = out / "clips" / f"{clip_id}.tts_request.json"
        if path.exists():
            clips[clip_id] = path.read_bytes()
            continue
        body: dict[str, object] = {
            "text": text,
            "language_code": code,
            "model": TTS_MODEL,
            "speech_sample_rate": SAMPLE_RATE,
            "output_audio_codec": "wav",
        }
        res = client.call(
            endpoint=TTS_PATH,
            clip_id=clip_id,
            language_requested=code,
            case=f"tts_{lang}",
            json_body=body,
        )
        if res.status != 200:
            raise SystemExit(f"TTS {lang} failed with status {res.status}: {res.body[:300]!r}")
        parsed = json.loads(res.body)
        audio = base64.b64decode(parsed["audios"][0])
        client.assert_clean(audio, res.body)
        path.write_bytes(audio)
        write_json(
            meta_path,
            {
                "endpoint": f"POST https://api.sarvam.ai{TTS_PATH}",
                "request_json": body,
                "auth": "api-subscription-key header (value not recorded)",
                "requested_at_utc": res.requested_at_utc,
                "response_status": res.status,
                "response_keys": sorted(parsed.keys()),
                "response_request_id": parsed.get("request_id"),
                "response_headers": res.headers,
                "audios_count": len(parsed["audios"]),
                "note": "base64 audio decoded to the sibling wav; body not stored again",
            },
        )
        clips[clip_id] = audio
    return clips


def make_local_clips(out: Path, clips: dict[str, bytes]) -> None:
    sil = wav_bytes(np.zeros(2 * SAMPLE_RATE, dtype=np.int16), SAMPLE_RATE)
    clips["silence_2s"] = sil
    hi, rate = sf.read(io.BytesIO(clips["tts_hi"]), dtype="int16")
    mono = hi if hi.ndim == 1 else hi[:, 0]
    clips["short_300ms"] = wav_bytes(mono[: int(0.3 * rate)], rate)
    for clip_id, (folder, _) in KOKORO.items():
        data, rate = sf.read(PIPECAT / folder / "f6_stereo.wav", dtype="int16")
        clips[clip_id] = wav_bytes(data[: 12 * rate, 0], rate)  # left = caller, first 12 s
    for clip_id in ("silence_2s", "short_300ms", *KOKORO):
        (out / "clips" / f"{clip_id}.wav").write_bytes(clips[clip_id])


def run_stt(
    client: SarvamClient, out: Path, clips: dict[str, bytes], only: set[str] | None
) -> None:
    for case, clip_id, fields in STT_CASES:
        if only is not None and case not in only:
            continue
        cdir = out / case
        if (cdir / "response_body.json").exists():
            continue
        cdir.mkdir(parents=True, exist_ok=True)
        audio = clips[clip_id]
        res = client.call(
            endpoint=STT_PATH,
            clip_id=clip_id,
            language_requested=fields.get("language_code"),
            case=case,
            data=fields,
            file=(f"{clip_id}.wav", audio, "audio/wav"),
        )
        client.assert_clean(res.body, json.dumps(res.headers))
        (cdir / "response_body.json").write_bytes(res.body)
        write_json(
            cdir / "request.json",
            {
                "endpoint": f"POST https://api.sarvam.ai{STT_PATH}",
                "content_type": "multipart/form-data",
                "auth": "api-subscription-key header (value not recorded)",
                "form_fields": fields,
                "file": {
                    "name": f"{clip_id}.wav",
                    "clip": f"../clips/{clip_id}.wav",
                    "sha256": sha256(audio),
                    "bytes": len(audio),
                },
                "requested_at_utc": res.requested_at_utc,
            },
        )
        write_json(
            cdir / "response_meta.json",
            {
                "status": res.status,
                "elapsed_s": round(res.elapsed_s, 3),
                "headers": res.headers,
            },
        )
        print(f"{case}: {res.status}")


def run_error_cases(client: SarvamClient, out: Path, clips: dict[str, bytes]) -> None:
    """Protocol-level errors: placeholder key, no file, non-audio bytes, empty file."""
    variants: list[tuple[str, dict[str, Any]]] = [
        (
            "err_bad_key",
            {
                "key_override": "not-a-real-key-capture-test",
                "file": ("tts_en.wav", clips["tts_en"], "audio/wav"),
            },
        ),
        ("err_no_file", {}),
        ("err_not_audio", {"file": ("note.wav", b"this is not audio\n", "audio/wav")}),
        ("err_empty_file", {"file": ("empty.wav", b"", "audio/wav")}),
    ]
    for case, kwargs in variants:
        cdir = out / case
        if (cdir / "response_body.json").exists():
            continue
        cdir.mkdir(parents=True, exist_ok=True)
        fields = {"language_code": "en-IN"}
        res = client.call(
            endpoint=STT_PATH,
            clip_id=case,
            language_requested="en-IN",
            case=case,
            data=fields,
            **kwargs,
        )
        client.assert_clean(res.body, json.dumps(res.headers))
        (cdir / "response_body.json").write_bytes(res.body)
        file_meta = kwargs.get("file")
        write_json(
            cdir / "request.json",
            {
                "endpoint": f"POST https://api.sarvam.ai{STT_PATH}",
                "content_type": "multipart/form-data",
                "auth": (
                    "api-subscription-key header: placeholder string, not a real key"
                    if "key_override" in kwargs
                    else "api-subscription-key header (value not recorded)"
                ),
                "form_fields": fields,
                "file": None
                if file_meta is None
                else {
                    "name": file_meta[0],
                    "sha256": sha256(file_meta[1]),
                    "bytes": len(file_meta[1]),
                },
                "requested_at_utc": res.requested_at_utc,
            },
        )
        write_json(
            cdir / "response_meta.json",
            {
                "status": res.status,
                "elapsed_s": round(res.elapsed_s, 3),
                "headers": res.headers,
            },
        )
        print(f"{case}: {res.status}")


def write_provenance(out: Path, calls: int) -> None:
    files = {
        str(p.relative_to(out)): {"sha256": sha256(p.read_bytes())}
        for p in sorted(out.rglob("*"))
        if p.is_file() and p.name != "provenance.json"
    }
    prov = {
        "source": "Sarvam AI REST API (https://api.sarvam.ai), speech-to-text and text-to-speech",
        "retrieval_date": dt.datetime.now(dt.UTC).date().isoformat(),
        "method": "captured",
        "doc_urls": DOC_URLS,
        "doc_retrieval_date": "2026-10-23",
        "software": {
            "python": platform.python_version(),
            "httpx": md.version("httpx"),
            "soundfile": md.version("soundfile"),
            "numpy": md.version("numpy"),
            "tracer_capture_module": "tracer_capture.sarvam_capture",
        },
        "api": {
            "stt_endpoint": f"POST https://api.sarvam.ai{STT_PATH}",
            "tts_endpoint": f"POST https://api.sarvam.ai{TTS_PATH}",
            "stt_default_model_per_docs": "saaras:v4",
            "tts_model_used": TTS_MODEL,
            "api_version": "none in URL; dated by retrieval_date",
        },
        "command": (
            "cd tools/capture && uv run python -m tracer_capture.sarvam_capture "
            "--env-file ../../.env.local"
        ),
        "license": (
            "Apache-2.0 for the harness and the invented sentences. API response bodies are "
            "model output from Sarvam AI; the owner is the API customer. Audio is Sarvam TTS "
            "output and Kokoro (Apache-2.0) output for invented text."
        ),
        "pii_status": "synthetic_no_personal_data",
        "indic_verified": "needs_native_review",
        "total_api_calls_logged": calls,
        "call_log": "tools/capture/sarvam_call_log.jsonl",
        "secrets": "the API key is never written; request metadata records only form fields",
        "files": files,
    }
    write_json(out / "provenance.json", prov)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=FIXTURES / "sarvam-2026-10-23")
    ap.add_argument("--only", nargs="*", help="run only these STT case ids")
    ap.add_argument("--provenance-only", action="store_true")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    client = SarvamClient(load_key(args.env_file), CALL_LOG)
    if not args.provenance_only:
        clips = make_tts_clips(client, args.out)
        make_local_clips(args.out, clips)
        run_stt(client, args.out, clips, set(args.only) if args.only else None)
        run_error_cases(client, args.out, clips)
    write_provenance(args.out, client.calls_made)
    print(f"calls logged: {client.calls_made}")


if __name__ == "__main__":
    main()
