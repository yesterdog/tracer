# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P4-11: the content-addressed TTS cache (contract docs/phase4/api-surface-batch-b.md section 7;
ADR 0024 amendment A9, invariant IA4; ADR 0026 rule 13).

``--out/tts-cache/<sha256>``: the key is the SHA-256 of the canonical JSON of exactly
``{provider, model, speaker, language_code, sample_rate_hz, codec, pace, text}``; the file name is
the lower-case hex key (no text in any name); the directory is 0700, files 0600, every open is
``O_NOFOLLOW``. A hit makes no request, no usage record and no call-log line, and is validated
like a reply: an invalid entry is a refusal, never repaired or fetched again silently.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from pathlib import Path
from typing import Any

import pytest

from evalproviders_phase4_support import (  # type: ignore[import-not-found,unused-ignore]
    MR_LINE,
    SECOND_MR_LINE,
    TtsRig,
    cache_mod,
    core_tts,
    tts_reply,
    tts_request,
    tts_samples,
    tts_voice,
)
from evalproviders_support import CANARY_KEY  # type: ignore[import-not-found,unused-ignore]

BASE: dict[str, Any] = {
    "provider": "sarvam",
    "model": "bulbul:v3",
    "speaker": "tracerspeaker",
    "language_code": "mr-IN",
    "sample_rate_hz": 16000,
    "codec": "linear16",
    "pace": "1",
    "text": MR_LINE,
}
GOLDEN = "de6911b877297ccb63e01b7d4db58ef9c2afa2e4acfa5c840a14bc4aecbab185"
HEX = re.compile(r"[0-9a-f]{64}")


def key_of(request: Any) -> str:
    voice = request.voice
    key: str = cache_mod().tts_cache_key(
        provider=voice.provider,
        model=voice.model,
        speaker=voice.speaker,
        language_code=voice.language_code,
        sample_rate_hz=request.sample_rate_hz,
        codec=request.codec,
        pace=voice.pace,
        text=request.text,
    )
    return key


def entries(out: Path) -> list[str]:
    return sorted(p.name for p in (out / "tts-cache").iterdir())


# ----------------------------------------------------------------------- the named tests


def test_tts_cache_hit_sends_nothing_and_records_no_usage(tmp_path: Path) -> None:
    """A hit makes no request, no UsageRecord and no call-log line; the audio is byte-identical;
    a second engine over the same ``--out`` (a re-run) spends zero requests too."""
    out = tmp_path / "out"
    cache = cache_mod().TtsCache.open(out)
    rig = TtsRig(tmp_path, [tts_reply("first", frames=640)], cache=cache)
    seen: list[Any] = []
    first = rig.call(tts_request(0), on_usage=seen.append)
    assert first.failure is None
    assert len(first.usage) == 1
    assert len(seen) == 1
    lines_before = list(rig.log_lines)
    ticks, requests = rig.ticker.calls, len(rig.transport.requests)
    second = rig.call(tts_request(1), on_usage=seen.append)  # the same line, another index
    assert second.failure is None
    assert second.audio == first.audio
    assert type(second.audio) is bytes
    assert second.usage == ()
    assert len(seen) == 1, "no record handed out for a hit"
    assert len(rig.transport.requests) == requests == 1, "a hit sends nothing"
    assert rig.log_lines == lines_before, "and logs nothing: there was no request"
    assert (rig.ticker.calls, rig.sleeps.waits) == (ticks, [])
    # a re-run: a new engine, a new cache object, a transport that scripts no step at all
    rerun = TtsRig(tmp_path / "again", [], cache=cache_mod().TtsCache.open(out))
    third = rerun.call(tts_request(0))
    assert third.audio == first.audio
    assert third.usage == ()
    assert rerun.transport.requests == []
    assert rerun.log_lines == []


def test_tts_cache_hit_sends_nothing_and_records_no_usage_a_cap_does_not_charge_a_hit(
    tmp_path: Path,
) -> None:
    """A hit is served when the characters cap is used up and charges nothing."""
    cache = cache_mod().TtsCache.open(tmp_path / "out")
    n = len(MR_LINE)
    rig = TtsRig(tmp_path, [tts_reply()], cap=n, cache=cache)
    assert rig.call(tts_request(0)).failure is None
    for index in range(1, 4):
        again = rig.call(tts_request(index))
        assert again.failure is None, "a hit is served when the characters cap is used up"
    other = rig.call(tts_request(9, SECOND_MR_LINE))
    assert type(other.failure) is core_tts().TtsCapReachedError


def test_tts_cache_hit_sends_nothing_and_records_no_usage_the_entry_is_the_audio(
    tmp_path: Path,
) -> None:
    """The entry is exactly the engine's audio, named by its key."""
    out = tmp_path / "out"
    cache = cache_mod().TtsCache.open(out)
    rig = TtsRig(tmp_path, [tts_reply(frames=100, seed=3)], cache=cache)
    outcome = rig.call(tts_request(0))
    request = tts_request(0)
    key = key_of(request)
    assert entries(out) == [key]
    assert (out / "tts-cache" / key).read_bytes() == outcome.audio == tts_samples(100, seed=3)
    assert cache.get(key) == outcome.audio


def test_tts_cache_hit_sends_nothing_and_records_no_usage_a_failure_is_not_cached(
    tmp_path: Path,
) -> None:
    """A failed request leaves no entry; the next call asks again."""
    from evalproviders_phase4_matrix import status  # type: ignore[import-not-found,unused-ignore]

    out = tmp_path / "out"
    cache = cache_mod().TtsCache.open(out)
    rig = TtsRig(tmp_path, [status(403), tts_reply()], cache=cache)
    assert rig.call(tts_request(0)).failure is not None
    assert entries(out) == []
    assert rig.call(tts_request(0)).failure is None, "the line is requested again, not replayed"
    assert len(rig.transport.requests) == 2
    assert len(entries(out)) == 1


def test_tts_cache_hit_sends_nothing_and_records_no_usage_the_engine_without_a_cache(
    tmp_path: Path,
) -> None:
    """Without a cache object every request is sent and no ``tts-cache`` directory appears."""
    rig = TtsRig(tmp_path, [tts_reply(), tts_reply()])
    rig.call(tts_request(0))
    rig.call(tts_request(1))
    assert len(rig.transport.requests) == 2, "no cache object, no replay"
    assert not (tmp_path / "out" / "tts-cache").exists()


def test_tts_cache_key_covers_every_parameter() -> None:
    """A9: the key is the SHA-256 of the canonical JSON of exactly the eight members; changing
    any one changes the key, so a voice, a rate, a codec, a pace or a model change never replays
    old audio; nothing else (an index, a time) is in it."""
    cache = cache_mod()
    key = cache.tts_cache_key(**BASE)
    assert HEX.fullmatch(key)
    canonical = json.dumps(BASE, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert key == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert key == GOLDEN
    changes: dict[str, Any] = {
        "provider": "other",
        "model": "bulbul:v2",
        "speaker": "anotherspeaker",
        "language_code": "hi-IN",
        "sample_rate_hz": 24000,
        "codec": "mulaw",
        "pace": "1.25",
        "text": MR_LINE + " ",
    }
    assert set(changes) == set(BASE)
    keys = {key}
    for name, value in changes.items():
        changed = cache.tts_cache_key(**{**BASE, name: value})
        assert HEX.fullmatch(changed)
        assert changed != key, name
        keys.add(changed)
    assert len(keys) == 9, "every single change gives its own key"
    assert cache.tts_cache_key(**BASE) == key, "deterministic"


def test_tts_cache_key_covers_every_parameter_text_is_compared_as_written() -> None:
    """No normalisation: the same letters in another Unicode form are another line."""
    cache = cache_mod()
    nfc = "\u00e9"
    nfd = "e\u0301"
    assert cache.tts_cache_key(**{**BASE, "text": nfc}) != cache.tts_cache_key(
        **{**BASE, "text": nfd}
    )
    assert cache.tts_cache_key(**{**BASE, "text": "a"}) != cache.tts_cache_key(
        **{**BASE, "text": "a\n"}
    )
    assert cache.tts_cache_key(**{**BASE, "sample_rate_hz": 8000}) != cache.tts_cache_key(
        **{**BASE, "sample_rate_hz": 16000}
    )


def test_tts_cache_key_covers_every_parameter_arguments_are_keyword_only_and_typed() -> None:
    """The key function takes the eight members by keyword, no default, with their types checked."""
    import inspect

    cache = cache_mod()
    parameters = inspect.signature(cache.tts_cache_key).parameters
    assert list(parameters) == list(BASE)
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters.values())
    assert all(p.default is inspect.Parameter.empty for p in parameters.values())
    for name, bad in [
        ("sample_rate_hz", "16000"),
        ("sample_rate_hz", 16000.0),
        ("sample_rate_hz", True),
        ("text", 5),
        ("text", b"x"),
        ("pace", 1),
        ("model", None),
    ]:
        with pytest.raises(TypeError):
            cache.tts_cache_key(**{**BASE, name: bad})


def test_tts_cache_key_covers_every_parameter_through_the_engine(tmp_path: Path) -> None:
    """Each of the changes is a miss for the engine (a new request); the same request is a hit."""
    cache = cache_mod().TtsCache.open(tmp_path / "out")
    variants = [
        tts_request(0),
        tts_request(0, voice=tts_voice(model="bulbul:v2")),
        tts_request(0, voice=tts_voice(speaker="anotherspeaker")),
        tts_request(0, voice=tts_voice(pace="1.25")),
        tts_request(0, rate=24000),
        tts_request(0, MR_LINE + "!"),
    ]
    rig = TtsRig(
        tmp_path,
        [tts_reply(f"v{i}", rate=24000 if i == 4 else 16000) for i in range(len(variants))],
        cache=cache,
    )
    for request in variants:
        assert rig.call(request).failure is None
    assert len(rig.transport.requests) == len(variants), "no variant replayed another's audio"
    assert len(entries(tmp_path / "out")) == len(variants)
    for request in variants:
        assert rig.call(request).failure is None
    assert len(rig.transport.requests) == len(variants), "and each one hits afterwards"


def test_tts_cache_key_covers_every_parameter_the_index_is_not_in_the_key() -> None:
    """The position of a line in the call is not part of what makes audio the same."""
    assert key_of(tts_request(0)) == key_of(tts_request(41))
    assert key_of(tts_request(0)) == GOLDEN


# -------------------------------------------------------------- links, modes, names


def test_tts_cache_refuses_links_and_is_private(tmp_path: Path) -> None:
    """A9: the directory is 0700 whatever the umask, files are 0600, names are the hex key, and
    a symbolic link in place of the directory or of an entry is refused (``O_NOFOLLOW``)."""
    out = tmp_path / "nested" / "out"
    previous = os.umask(0o000)
    try:
        cache = cache_mod().TtsCache.open(out)
        key = key_of(tts_request(0))
        cache.put(key, b"\x01\x00" * 50)
    finally:
        os.umask(previous)
    directory = out / "tts-cache"
    assert cache_mod().TTS_CACHE_DIR_NAME == "tts-cache"
    assert cache.path == directory
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700, oct(directory.stat().st_mode)
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert [p.name for p in directory.iterdir()] == [key]
    assert stat.S_IMODE((directory / key).stat().st_mode) == 0o600
    assert cache.get(key) == b"\x01\x00" * 50
    other = "f" * 64
    assert cache.get(other) is None, "a miss is None, not an error"
    # a link in place of an entry: refused for reading and for writing, the target untouched
    target = tmp_path / "elsewhere.bin"
    target.write_bytes(b"\x07\x00" * 10)
    link_key = "a" * 64
    (directory / link_key).symlink_to(target)
    with pytest.raises(OSError):  # noqa: PT011 (the text is the platform's)
        cache.get(link_key)
    with pytest.raises(OSError):  # noqa: PT011
        cache.put(link_key, b"\x02\x00" * 10)
    assert target.read_bytes() == b"\x07\x00" * 10
    dangling = "b" * 64
    nowhere = tmp_path / "not-yet.bin"
    (directory / dangling).symlink_to(nowhere)
    with pytest.raises(OSError):  # noqa: PT011
        cache.put(dangling, b"\x02\x00")
    assert not nowhere.exists(), "the link was not followed to create its target"


def test_tts_cache_refuses_links_and_is_private_a_link_in_place_of_the_directory(
    tmp_path: Path,
) -> None:
    """A link (also dangling) in place of ``tts-cache`` is refused and nothing is written through
    it."""
    out = tmp_path / "out"
    out.mkdir()
    real = tmp_path / "real-dir"
    real.mkdir()
    (out / "tts-cache").symlink_to(real)
    with pytest.raises(OSError):  # noqa: PT011
        cache_mod().TtsCache.open(out)
    assert list(real.iterdir()) == [], "nothing was written through the link"
    dangling_out = tmp_path / "out2"
    dangling_out.mkdir()
    (dangling_out / "tts-cache").symlink_to(tmp_path / "no-dir-yet")
    with pytest.raises(OSError):  # noqa: PT011
        cache_mod().TtsCache.open(dangling_out)
    assert not (tmp_path / "no-dir-yet").exists()


def test_tts_cache_refuses_links_and_is_private_an_existing_directory_keeps_its_mode(
    tmp_path: Path,
) -> None:
    """The user's directory is not changed; entries are 0600."""
    out = tmp_path / "out"
    directory = out / "tts-cache"
    directory.mkdir(parents=True)
    directory.chmod(0o755)
    cache = cache_mod().TtsCache.open(out)
    cache.put("c" * 64, b"\x00\x00")
    assert stat.S_IMODE(directory.stat().st_mode) == 0o755, "the user's directory is not changed"
    assert stat.S_IMODE((directory / ("c" * 64)).stat().st_mode) == 0o600


def test_tts_cache_refuses_links_and_is_private_an_entry_that_is_not_a_regular_file(
    tmp_path: Path,
) -> None:
    """A directory or a FIFO planted as an entry is refused at once (a FIFO must not hold the
    run), never read."""
    cache = cache_mod().TtsCache.open(tmp_path / "out")
    directory = tmp_path / "out" / "tts-cache"
    (directory / ("d" * 64)).mkdir()
    os.mkfifo(directory / ("e" * 64))
    for key in ("d" * 64, "e" * 64):
        box: dict[str, Any] = {}

        def attempt(key: str = key, box: dict[str, Any] = box) -> None:
            try:
                box["value"] = cache.get(key)
            except BaseException as error:
                box["error"] = error

        thread = threading.Thread(target=attempt, daemon=True)
        thread.start()
        thread.join(10)
        if thread.is_alive():
            # unblock the reader so the thread can end, then fail
            fd = os.open(directory / key, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
            pytest.fail("the cache blocks on an entry that is not a regular file")
        assert isinstance(box.get("error"), OSError), (key, box)


def test_tts_cache_refuses_links_and_is_private_names_are_hex_keys_only(tmp_path: Path) -> None:
    """A file name is built only from a lower-case hex key, never from other text."""
    cache = cache_mod().TtsCache.open(tmp_path / "out")
    for bad in [
        "",
        "abc",
        "../" + "a" * 61,
        "A" * 64,
        "a" * 63,
        "a" * 65,
        "a" * 63 + "/",
        "g" * 64,
    ]:
        with pytest.raises(ValueError, match="key"):
            cache.get(bad)
        with pytest.raises(ValueError, match="key"):
            cache.put(bad, b"\x00\x00")
    assert list((tmp_path / "out" / "tts-cache").iterdir()) == []
    with pytest.raises(TypeError):
        cache.get(5)
    with pytest.raises(TypeError):
        cache.put("a" * 64, "text")
    with pytest.raises(ValueError, match="audio"):
        cache.put("a" * 64, b"")


def test_tts_cache_refuses_links_and_is_private_no_text_no_key_in_any_name_or_file(
    tmp_path: Path,
) -> None:
    """No text in any name and no API key in any file of the cache."""
    out = tmp_path / "out"
    cache = cache_mod().TtsCache.open(out)
    rig = TtsRig(tmp_path, [tts_reply(), tts_reply()], cache=cache)
    rig.call(tts_request(0, MR_LINE))
    rig.call(tts_request(1, SECOND_MR_LINE))
    names = entries(out)
    assert len(names) == 2
    assert all(HEX.fullmatch(n) for n in names)
    for path in sorted(out.rglob("*")):
        assert MR_LINE not in path.name
        assert SECOND_MR_LINE not in path.name
        if path.is_file() and path.parent.name == "tts-cache":
            assert CANARY_KEY.encode() not in path.read_bytes()
            assert MR_LINE.encode() not in path.read_bytes()


# --------------------------------------------------- a hit is validated like a reply


def _plant(out: Path, request: Any, data: bytes) -> None:
    cache = cache_mod().TtsCache.open(out)
    directory = out / "tts-cache"
    path = directory / key_of(request)
    path.write_bytes(data)
    path.chmod(0o600)
    assert cache.path == directory


@pytest.mark.parametrize(
    ("label", "data"),
    [
        ("empty file", b""),
        ("odd length for 16-bit samples", b"\x01\x00\x02"),
    ],
)
def test_tts_cache_an_invalid_entry_is_a_refusal_never_repaired_or_fetched(
    tmp_path: Path, label: str, data: bytes
) -> None:
    """A9: a hit is validated like a reply; an invalid entry is a refusal, not repaired, not
    fetched again."""
    out = tmp_path / "out"
    request = tts_request(3)
    _plant(out, request, data)
    rig = TtsRig(tmp_path, [tts_reply()], cache=cache_mod().TtsCache.open(out))
    outcome = rig.call(request)
    assert type(outcome.failure) is core_tts().TtsResponseInvalid, label
    assert outcome.failure.reason == "cache_entry_invalid"
    assert outcome.failure.index == 3
    assert outcome.audio is None
    assert outcome.usage == ()
    assert rig.transport.requests == [], "not fetched again silently"
    assert (out / "tts-cache" / key_of(request)).read_bytes() == data, "not repaired"


def test_tts_cache_an_entry_over_the_audio_cap_is_a_refusal(tmp_path: Path) -> None:
    """A9: the audio cap applies to a cache entry; exactly the cap is a hit."""
    out = tmp_path / "out"
    request = tts_request(0, rate=8000, codec="mulaw")
    from evalproviders_phase4_support import models_mod  # type: ignore[import-not-found,unused-ignore]

    cap = models_mod().tts_max_audio_bytes(8000, "mulaw")
    _plant(out, request, b"\x7f" * (cap + 1))
    rig = TtsRig(tmp_path, [], cache=cache_mod().TtsCache.open(out))
    outcome = rig.call(request)
    assert outcome.failure is not None
    assert outcome.failure.reason == "cache_entry_invalid"
    assert rig.transport.requests == []
    _plant(out / "exact", request, b"\x7f" * cap)
    exact = TtsRig(tmp_path / "x", [], cache=cache_mod().TtsCache.open(out / "exact"))
    hit = exact.call(request)
    assert hit.failure is None
    assert hit.audio == b"\x7f" * cap


def test_tts_cache_a_mulaw_entry_of_any_length_is_a_hit(tmp_path: Path) -> None:
    """The cache holds the engine's audio, whatever container the reply used; mu-law bytes have
    no sample width, so odd and even lengths are both valid."""
    out = tmp_path / "out"
    request = tts_request(0, rate=8000, codec="mulaw")
    _plant(out, request, b"\xff\x7f\x00")
    rig = TtsRig(tmp_path, [], cache=cache_mod().TtsCache.open(out))
    outcome = rig.call(request)
    assert outcome.failure is None
    assert outcome.audio == b"\xff\x7f\x00"
    assert outcome.usage == ()
