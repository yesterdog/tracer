# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-08, the request side of the Sarvam TTS adapter (contract docs/phase4/api-surface-batch-b.md
section 2; ADR 0024 amendments A1 and A2, invariant IA1; ADR 0026 rules 11 and 12).

The request is exactly seven JSON fields; the model, the speaker, the language, the rate and the
codec come from the request record and nothing is defaulted by the engine; any language but
``mr-IN`` and any text over 2,500 code points is refused before anything is sent. Every request is
an ``httpx.MockTransport`` request: no test opens a socket.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import ssl
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from evalproviders_phase4_support import (  # type: ignore[import-not-found,unused-ignore]
    CANARY_KEY,
    LONG_MR,
    MR_LINE,
    SECOND_MR_LINE,
    TtsRig,
    cache_mod,
    client_mod,
    core_tts,
    models_mod,
    tts_context,
    tts_mod,
    tts_reply,
    tts_request,
    tts_voice,
)
from evalproviders_support import SRC, chain  # type: ignore[import-not-found,unused-ignore]

RATES = [8000, 16000, 22050, 24000, 32000, 44100, 48000]
FIELDS = {
    "text",
    "language_code",
    "speaker",
    "model",
    "pace",
    "speech_sample_rate",
    "output_audio_codec",
}


def _body(request: Any) -> dict[str, Any]:
    document = json.loads(request.content.decode("utf-8"), parse_float=Decimal)
    assert isinstance(document, dict)
    return document


# ------------------------------------------------------------------ the request, field by field


def test_sarvam_tts_sends_explicit_model_speaker_language_rate(tmp_path: Path) -> None:
    """ADR 0024 A2 / IA1: ``POST https://api.sarvam.ai/text-to-speech`` with exactly the fields
    ``text``, ``language_code``, ``speaker``, ``model``, ``pace``, ``speech_sample_rate`` and
    ``output_audio_codec``; the voice and the rate come from the request, ``language_code`` is
    ``mr-IN``, the rate is the request's own (nothing is resampled) and the codec is as asked."""
    for number, rate in enumerate(RATES):
        voice = tts_voice(model="bulbul:v3", speaker="tracerspeaker", pace="0.75")
        rig = TtsRig(tmp_path / f"r{number}", [tts_reply(rate=rate)])
        rig.call(tts_request(3, SECOND_MR_LINE, rate=rate, voice=voice))
        (request,) = rig.transport.requests
        assert request.method == "POST"
        assert str(request.url) == "https://api.sarvam.ai/text-to-speech"
        body = _body(request)
        assert set(body) == FIELDS, "exactly these fields, nothing else"
        assert body["text"] == SECOND_MR_LINE
        assert body["language_code"] == "mr-IN"
        assert body["speaker"] == "tracerspeaker"
        assert body["model"] == "bulbul:v3"
        assert Decimal(str(body["pace"])) == Decimal("0.75")
        assert type(body["speech_sample_rate"]) is int
        assert body["speech_sample_rate"] == rate
        assert body["output_audio_codec"] == "wav"  # the domain codec linear16 goes as wav


def test_sarvam_tts_sends_explicit_model_speaker_language_rate_mulaw_for_telephony(
    tmp_path: Path,
) -> None:
    """ADR 0024 A2: ``mulaw`` for the telephony dialect, at 8,000 Hz. Only the request is pinned
    (what the reply holds for ``mulaw`` is not verified, A2)."""
    rig = TtsRig(tmp_path, [tts_reply(rate=8000)])
    rig.call(tts_request(0, MR_LINE, rate=8000, codec="mulaw"))
    body = _body(rig.transport.requests[0])
    assert set(body) == FIELDS
    assert (body["speech_sample_rate"], body["output_audio_codec"]) == (8000, "mulaw")


@pytest.mark.parametrize("pace", ["0.5", "1", "1.25", "2", "0.75", "1.5", "0.55555"])
def test_sarvam_tts_sends_explicit_model_speaker_language_rate_pace_is_a_json_number(
    tmp_path: Path, pace: str
) -> None:
    """The profile holds ``pace`` as a decimal string (no float in any record); the wire carries
    a JSON number of the same value."""
    rig = TtsRig(tmp_path, [tts_reply()])
    rig.call(tts_request(0, voice=tts_voice(pace=pace)))
    content = rig.transport.requests[0].content
    assert re.search(rb'"pace"\s*:\s*[0-9]', content), content
    assert Decimal(str(_body(rig.transport.requests[0])["pace"])) == Decimal(pace)


@pytest.mark.parametrize(
    "text",
    [
        "a",
        'quote " and \\ backslash',
        "line\nbreak",
        "tab\there",
        "\U0001f600 astral",
        "\u200d\u200c",
        "  padded \n",
        "\u00a0nbsp\u00a0",
    ],
)
def test_sarvam_tts_sends_explicit_model_speaker_language_rate_text_is_verbatim(
    tmp_path: Path, text: str
) -> None:
    """The text goes to the provider byte for byte (quotes, newlines, astral characters,
    joiners)."""
    rig = TtsRig(tmp_path, [tts_reply()])
    rig.call(tts_request(0, text))
    assert _body(rig.transport.requests[0])["text"] == text


def test_sarvam_tts_sends_explicit_model_speaker_language_rate_every_voice_field_goes_through(
    tmp_path: Path,
) -> None:
    """Model and speaker come from the request's voice, whatever their value."""
    for number, (model, speaker) in enumerate(
        [("bulbul:v2", "anushka"), ("bulbul:v3", "other.speaker-2"), ("m", "S")]
    ):
        rig = TtsRig(tmp_path / f"v{number}", [tts_reply()])
        rig.call(tts_request(0, voice=tts_voice(model=model, speaker=speaker)))
        body = _body(rig.transport.requests[0])
        assert (body["model"], body["speaker"]) == (model, speaker)


def test_sarvam_tts_sends_explicit_model_speaker_language_rate_the_engine_defaults_nothing() -> (
    None
):
    """No default model, speaker, language, rate, codec or pace in code (ADR 0026 rule 12):
    none of them is a parameter of the engine or of its factory."""
    names = {
        "model",
        "speaker",
        "language",
        "language_code",
        "rate",
        "sample_rate",
        "sample_rate_hz",
        "codec",
        "pace",
        "voice",
        "text",
    }
    tts = tts_mod()
    for target in (tts.SarvamTts.__init__, tts.create_sarvam_tts):
        assert not set(inspect.signature(target).parameters) & names, target
    parameters = inspect.signature(tts.SarvamTts.synthesise).parameters
    assert list(parameters) == ["self", "request", "context", "on_usage"]
    assert parameters["on_usage"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["on_usage"].default is None


def test_sarvam_tts_the_engine_is_a_tts_engine_that_streams_usage(tmp_path: Path) -> None:
    """ADR 0026 rule 4: the adapter satisfies both runtime protocols of the boundary."""
    core = core_tts()
    rig = TtsRig(tmp_path, [])
    assert isinstance(rig.engine, core.TtsEngine)
    assert isinstance(rig.engine, core.UsageStreamingTtsEngine)
    assert rig.engine.streams_usage is True
    assert rig.engine.provider == "sarvam"


# ------------------------------------------------------------ refused before the send


NOT_MR = ["hi-IN", "en-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN", "pa-IN", "gu-IN", "mr-US"]


def _nothing_happened(rig: Any, sink: list[Any]) -> None:
    assert rig.transport.requests == [], "nothing was sent"
    assert rig.log_lines == [], "no call log line: there was no attempt"
    assert sink == [], "no usage"
    assert rig.sleeps.waits == []


@pytest.mark.parametrize("language", NOT_MR)
def test_sarvam_tts_refuses_non_mr_and_over_2500_chars_before_send(
    tmp_path: Path, language: str
) -> None:
    """ADR 0024 A2 / IA1, BL-162, BL-278: any language but ``mr-IN`` is refused before the send
    with ``TtsLanguageNotSupportedError`` (a configuration error, raised, not an outcome)."""
    sink: list[Any] = []
    rig = TtsRig(tmp_path, [tts_reply()])
    request = tts_request(4, voice=tts_voice(language_code=language))
    with pytest.raises(core_tts().TtsLanguageNotSupportedError) as raised:
        rig.call(request, on_usage=sink.append)
    error: Any = raised.value
    assert isinstance(raised.value, core_tts().TtsConfigurationError)
    assert isinstance(raised.value, ValueError)
    assert error.reason == "language_not_supported"
    assert error.index == 4
    assert chain(error) == [error]
    assert language not in str(error)
    _nothing_happened(rig, sink)


LIMIT_CASES = [
    ("devanagari", "\u0905" * 2500, "\u0905" * 2501),
    ("ascii", "a" * 2500, "a" * 2501),
    ("astral", "\U0001f600" * 2500, "\U0001f600" * 2501),
    ("combining", "\u0915\u094d" * 1250, "\u0915\u094d" * 1250 + "\u0915"),
]


@pytest.mark.parametrize(("label", "fits", "over"), LIMIT_CASES, ids=[c[0] for c in LIMIT_CASES])
def test_sarvam_tts_refuses_non_mr_and_over_2500_chars_before_send_the_length_limit(
    tmp_path: Path, label: str, fits: str, over: str
) -> None:
    """2,500 code points are sent; 2,501 are refused with ``TtsTextTooLongError`` before the send.
    Code points, not bytes (2,500 Devanagari letters are 7,500 bytes) and not UTF-16 units."""
    assert models_mod().TTS_MAX_TEXT_CHARS == 2500
    assert (len(fits), len(over)) == (2500, 2501)
    ok = TtsRig(tmp_path / "ok", [tts_reply()])
    ok.call(tts_request(0, fits))
    assert len(ok.transport.requests) == 1, "a text of exactly 2,500 code points is sent"
    sink: list[Any] = []
    rig = TtsRig(tmp_path / "over", [tts_reply()])
    with pytest.raises(core_tts().TtsTextTooLongError) as raised:
        rig.call(tts_request(6, over), on_usage=sink.append)
    error: Any = raised.value
    assert isinstance(raised.value, core_tts().TtsConfigurationError)
    assert isinstance(raised.value, ValueError)
    assert (error.reason, error.index) == ("text_too_long", 6)
    assert chain(error) == [error]
    assert over[:10] not in str(error)
    _nothing_happened(rig, sink)


def test_sarvam_tts_refuses_non_mr_and_over_2500_chars_before_send_one_over_the_limit(
    tmp_path: Path,
) -> None:
    """One code point over the limit is refused; exactly the limit is sent."""
    sink: list[Any] = []
    rig = TtsRig(tmp_path, [tts_reply()])
    with pytest.raises(core_tts().TtsTextTooLongError):
        rig.call(tts_request(0, LONG_MR + "\u0905"), on_usage=sink.append)
    _nothing_happened(rig, sink)
    only = TtsRig(tmp_path / "exact", [tts_reply()])
    only.call(tts_request(0, LONG_MR))
    assert len(only.transport.requests) == 1


def test_sarvam_tts_refuses_non_mr_and_over_2500_chars_before_send_nothing_is_cached(
    tmp_path: Path,
) -> None:
    """A refusal writes nothing to the cache."""
    cache = cache_mod().TtsCache.open(tmp_path / "out")
    rig = TtsRig(tmp_path, [tts_reply()], cache=cache)
    with pytest.raises(core_tts().TtsTextTooLongError):
        rig.call(tts_request(0, LONG_MR + "x"))
    with pytest.raises(core_tts().TtsLanguageNotSupportedError):
        rig.call(tts_request(0, voice=tts_voice(language_code="hi-IN")))
    assert list((tmp_path / "out" / "tts-cache").iterdir()) == []


def test_sarvam_tts_a_refusal_does_not_stop_the_next_request(tmp_path: Path) -> None:
    """A configuration refusal is per request; the engine stays usable."""
    rig = TtsRig(tmp_path, [tts_reply("after-refusal")])
    with pytest.raises(core_tts().TtsTextTooLongError):
        rig.call(tts_request(0, LONG_MR + "x"))
    outcome = rig.call(tts_request(1))
    assert outcome.failure is None
    assert len(rig.transport.requests) == 1


# ---------------------------------------------------------------------------- the endpoint


def test_sarvam_tts_base_url_not_overridable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0024 rule 3 applied to TTS (A1): the host and the path are constants; no argument,
    variable or file moves them, and the request carries no query."""
    for name in (
        "SARVAM_BASE_URL",
        "SARVAM_API_URL",
        "SARVAM_API_BASE",
        "SARVAM_ENDPOINT",
        "SARVAM_TTS_URL",
        "TRACER_SARVAM_URL",
        "API_BASE_URL",
        "BASE_URL",
    ):
        monkeypatch.setenv(name, "http://evil.example:8080")
    rig = TtsRig(tmp_path, [tts_reply()])
    rig.call()
    (request,) = rig.transport.requests
    assert str(request.url) == "https://api.sarvam.ai/text-to-speech"
    assert (request.url.scheme, request.url.host, request.url.port) == (
        "https",
        "api.sarvam.ai",
        None,
    )
    assert request.url.path == "/text-to-speech"
    assert request.url.query == b""
    forbidden = {
        "url",
        "base_url",
        "endpoint",
        "host",
        "port",
        "verify",
        "cert",
        "proxy",
        "proxies",
        "follow_redirects",
        "http2",
        "trust_env",
        "headers",
        "path",
        "scheme",
        "event_hooks",
    }
    tts = tts_mod()
    for target in (tts.SarvamTts.__init__, tts.SarvamTts.synthesise, tts.create_sarvam_tts):
        names = set(inspect.signature(target).parameters)
        assert not names & forbidden, (target, names & forbidden)
    models = models_mod()
    assert models.API_BASE_URL == "https://api.sarvam.ai"
    assert models.TTS_PATH == "/text-to-speech"
    assert models.TTS_URL == "https://api.sarvam.ai/text-to-speech"
    assert models.TTS_URL == models.API_BASE_URL + models.TTS_PATH
    assert "sarvam.ai" not in str(inspect.signature(tts.SarvamTts.__init__))


def test_sarvam_tts_base_url_not_overridable_the_urls_are_built_not_written() -> None:
    """The Phase 3 source scan allows two URL literals in ``sarvam/models.py`` and no other; so
    the TTS and chat URLs must be built from ``API_BASE_URL`` and a path constant."""
    tree = ast.parse((SRC / "sarvam" / "models.py").read_text("utf-8"))
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"TTS_URL", "CHAT_URL"}:
                    found[target.id] = node.value
    assert set(found) == {"TTS_URL", "CHAT_URL"}
    for name, value in found.items():
        assert isinstance(value, ast.BinOp), f"{name} must be API_BASE_URL + a path"


# ---------------------------------------------------------------- key, headers, one client


def test_sarvam_tts_the_key_is_in_one_header_and_nowhere_else(tmp_path: Path) -> None:
    """ADR 0024 rule 5: one ``api-subscription-key`` header, one ``accept-encoding: identity``,
    the key in no URL, body or other header."""
    rig = TtsRig(tmp_path, [tts_reply()])
    rig.call()
    (request,) = rig.transport.requests
    assert request.headers.get_list("api-subscription-key") == [CANARY_KEY]
    assert request.headers.get_list("accept-encoding") == ["identity"]
    assert CANARY_KEY not in str(request.url)
    assert CANARY_KEY.encode() not in request.content
    others = [v for k, v in request.headers.items() if k.lower() != "api-subscription-key"]
    assert not any(CANARY_KEY in v for v in others)


def test_sarvam_tts_and_chat_build_no_http_client_of_their_own() -> None:
    """ADR 0024 rule 3 for the new adapters: the one ``httpx.Client`` of the package (the existing
    scan wants exactly one, in ``sarvam/client.py``, with ``trust_env=False`` and no redirect) is
    the one ``build_http_client`` returns; neither new adapter names ``httpx.Client`` or an
    ``httpx`` shortcut, so the hardening of the client cannot be bypassed."""
    for name in ("tts.py", "chat.py"):
        tree = ast.parse((SRC / "sarvam" / name).read_text("utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "httpx"
            ):
                assert node.func.attr not in {
                    "Client",
                    "AsyncClient",
                    "HTTPTransport",
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "head",
                    "options",
                    "request",
                    "stream",
                }, (name, node.func.attr)


def test_sarvam_build_http_client_is_the_hardened_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0024 rule 3 and batch G finding F3, now for the builder all three adapters share:
    no redirect, no environment (proxy, certificates, ``.netrc``), TLS verified, 10 s and 60 s."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "no-such-ca-file.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "no-such-ca-dir"))
    build = client_mod().build_http_client
    assert list(inspect.signature(build).parameters) == ["transport"]
    http = build(None)
    try:
        assert http.trust_env is False
        assert http.follow_redirects is False
        assert http._mounts == {}
        context = http._transport._pool._ssl_context
        assert isinstance(context, ssl.SSLContext)
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert http.timeout.connect == 10.0
        assert http.timeout.pool == 10.0
        assert http.timeout.read == 60.0
        assert http.timeout.write == 60.0
    finally:
        http.close()


def test_sarvam_tts_close_leaves_the_shared_call_log_open(tmp_path: Path) -> None:
    """The call log belongs to the run (one file for every engine, A8), not to an engine."""
    rig = TtsRig(tmp_path, [tts_reply(), tts_reply()])
    rig.call()
    rig.engine.close()
    rig.engine.close()  # closing twice is harmless
    other = TtsRig(tmp_path / "second", [tts_reply()])
    other.call()
    assert len(rig.log_lines) == 2
    rig.log.before(stage="tts", index=9, attempt=1, bytes_sent=1, characters=1)
    assert len(rig.log_lines) == 3, "the log is still writable after the engine was closed"


def test_sarvam_tts_a_request_is_a_request_record(tmp_path: Path) -> None:
    """The engine takes the typed records and refuses loose dictionaries before any request."""
    rig = TtsRig(tmp_path, [])
    with pytest.raises(TypeError):
        rig.engine.synthesise({"text": "x"}, tts_context())
    with pytest.raises(TypeError):
        rig.engine.synthesise(tts_request(), None)
    assert rig.transport.requests == []
    assert rig.log_lines == []
