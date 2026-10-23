# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-10a: Artifact, AudioFormat and ChannelMap (ADR 0004 Decision 5, ADR 0007 Decisions 6-8,
ADR 0012 Decisions 4 and 5, ADR 0016 Decision 4).

Assumed public API (docs/phase1/api-surface.md section 22), ``tracer_evalschema.artifact``:
``Artifact`` (``art``, a record of fact with a required ``privacy`` marker), ``AudioFormat``,
``ChannelMap{artifact_id, channels: ChannelAssignment[{index, role}], determination, method,
confidence?}``, ``agent_audio_available(channel_maps) -> bool`` and
``check_channel_map(channel_map, artifact) -> None`` (a ``ValueError``: the rules that need the
artifact). The URL rules are those of ``tracer_evalschema.credentials`` and ``url_rules``.

Names are SCOPE section 5 names unless the docstring says "Proposed name".
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import pytest
from pydantic import ValidationError

from ._support import api
from ._support_b2 import (
    contains_key,
    embedded_models,
    error_texts,
    property_enum,
    registered_class,
    schema_accepts,
    schema_of,
)
from ._support_b3 import (
    AWS_KEY,
    DEVANAGARI,
    PASTED_KEY,
    QUOTE_MARK,
    artifact_doc,
    assert_input_hidden,
    assert_rejected3,
    audio_format,
    channel_map_doc,
    doc3,
    entity_class3,
    inferred_map_doc,
    make3,
    marked_id,
    marked_key,
    marked_set,
    new_id,
    privacy_human,
)

HOST = "recordings.example.com"


def _artifact(**over: Any) -> Any:
    return make3("Artifact", **over)


def _channel_map(**over: Any) -> Any:
    return registered_class("ChannelMap").model_validate(channel_map_doc(**over))


def _reject_uri(uri: str) -> None:
    assert_rejected3(entity_class3("Artifact"), artifact_doc(uri=uri), field="uri")


def _accept_uri(uri: str) -> None:
    assert _artifact(uri=uri).uri == uri


def _roles(*roles: str) -> list[dict[str, Any]]:
    return [{"index": index, "role": role} for index, role in enumerate(roles)]


# --------------------------------------------------------------------------- Artifact fields


def test_artifact_sha256_hex() -> None:
    """ADR 0012 I7: ``sha256`` is exactly 64 lowercase hexadecimal characters (pydantic and the
    JSON Schema ``pattern`` agree). Upper case, a wrong length, non-hex text, padding and a
    trailing newline are rejected; ``size`` is a non-negative integer; ``turn_ref`` is an optional
    ``turn_`` ID; the record round-trips."""
    artifact = entity_class3("Artifact")
    for good in ("0" * 64, "ab" * 32, "0123456789abcdef" * 4):
        assert _artifact(sha256=good).sha256 == good
    for bad in (
        "AB" * 32,
        "ab" * 31 + "a",
        "ab" * 32 + "a",
        "g" * 64,
        " " + "a" * 63,
        "a" * 64 + "\n",
        "sha256:" + "a" * 57,
        "",
        None,
        12345,
    ):
        assert_rejected3(artifact, artifact_doc(sha256=bad), field="sha256")
    pattern = schema_of(artifact)["properties"]["sha256"]["pattern"]
    for good in ("0" * 64, "ab" * 32):
        assert re.search(pattern, good)
    for bad in ("AB" * 32, "ab" * 31, "ab" * 32 + "a", "g" * 64):
        assert not re.search(pattern, bad)
    assert schema_accepts(artifact, artifact_doc())
    assert not schema_accepts(artifact, artifact_doc(sha256="AB" * 32))
    for good_size in (0, 1, 2**40):
        assert _artifact(size=good_size).size == good_size
    for bad_size in (-1, 1.5, "10", True, None):
        assert_rejected3(artifact, artifact_doc(size=bad_size), field="size")
    turn = new_id("turn")
    assert str(_artifact(turn_ref=turn).turn_ref) == turn
    assert _artifact().turn_ref is None
    for bad_ref in (new_id("tsc"), new_id("art"), "turn-1", 7):
        assert_rejected3(artifact, artifact_doc(turn_ref=bad_ref), field="turn_ref")
    for missing in ("uri", "sha256", "size", "privacy"):
        doc = {k: v for k, v in artifact_doc().items() if k != missing}
        assert_rejected3(artifact, doc, field=missing)
    loaded = _artifact(turn_ref=turn)
    assert artifact.model_validate_json(loaded.model_dump_json()) == loaded


def test_audio_format_fields() -> None:
    """ADR 0012 I7: ``AudioFormat{container, codec, sample_rate_hz, channels, bits_per_sample?}``.
    ``container`` and ``codec`` are open MachineKeys (an unseen codec is fine), the three numbers
    are strict positive integers when present (a float, a boolean, text and zero are rejected;
    ``bits_per_sample`` alone is optional since N27, revision 11, so ``None`` is accepted for it
    and stays rejected for the other two), unknown fields are rejected, and ``audio_format`` is
    optional on an Artifact (a non-audio one has none)."""
    fmt = registered_class("AudioFormat")
    assert {"container", "codec", "sample_rate_hz", "channels", "bits_per_sample"} == set(
        fmt.model_fields
    )
    loaded = fmt.model_validate(audio_format())
    assert (loaded.container, loaded.codec) == ("wav", "pcm_s16le")
    assert (loaded.sample_rate_hz, loaded.channels, loaded.bits_per_sample) == (16000, 2, 16)
    assert fmt.model_validate(audio_format(container="ogg", codec="some-new-codec")).codec == (
        "some-new-codec"
    )
    for name in ("sample_rate_hz", "channels", "bits_per_sample"):
        for bad in (0, -1, 1.5, "16", True, *(() if name == "bits_per_sample" else (None,))):
            with pytest.raises(ValidationError):
                fmt.model_validate(audio_format(**{name: bad}))
    for name in ("container", "codec"):
        for bad in ("two words", "", None, DEVANAGARI):
            with pytest.raises(ValidationError):
                fmt.model_validate(audio_format(**{name: bad}))
    for missing in ("container", "codec", "sample_rate_hz", "channels"):
        with pytest.raises(ValidationError):
            fmt.model_validate({k: v for k, v in audio_format().items() if k != missing})
    with pytest.raises(ValidationError):
        fmt.model_validate(audio_format(duration_s=3))
    assert not {"id", "schema_version", "created_at", "metadata"} & set(fmt.model_fields)
    plain = {k: v for k, v in artifact_doc().items() if k != "audio_format"}
    assert entity_class3("Artifact").model_validate(plain).audio_format is None
    assert _artifact(audio_format=audio_format(channels=1)).audio_format.channels == 1
    assert_rejected3(entity_class3("Artifact"), artifact_doc(audio_format=audio_format(channels=0)))


def test_audio_format_bits_per_sample_optional() -> None:
    """N27 (default applied in revision 11, owner may revisit; api-surface 22.9 item 2): a lossy
    codec has no bit depth, so a producer omits ``bits_per_sample`` instead of inventing one. A
    format without it validates and round-trips, an explicit ``null`` loads as absent, the JSON
    Schema lists the other four fields as required and not this one, and the 16-bit PCM of the
    stereo WAV mapping keeps its value. Proposed name (SCOPE section 5 lists it under P1-10a)."""
    fmt = registered_class("AudioFormat")
    lossy = dict(audio_format(container="ogg", codec="opus"))
    lossy.pop("bits_per_sample")
    loaded = fmt.model_validate(lossy)
    assert (loaded.container, loaded.codec, loaded.bits_per_sample) == ("ogg", "opus", None)
    assert fmt.model_validate_json(loaded.model_dump_json()) == loaded
    assert fmt.model_validate(loaded.model_dump(mode="json")) == loaded
    nulled = fmt.model_validate(audio_format(container="ogg", codec="opus", bits_per_sample=None))
    assert nulled.bits_per_sample is None
    assert nulled == loaded
    schema = schema_of(fmt)
    assert set(schema["required"]) == {"container", "codec", "sample_rate_hz", "channels"}
    assert "bits_per_sample" in schema["properties"]
    artifact = entity_class3("Artifact")
    opus = artifact_doc(audio_format=lossy)
    assert schema_accepts(artifact, opus)
    kept = _artifact(audio_format=lossy)
    assert kept.audio_format.bits_per_sample is None
    assert artifact.model_validate_json(kept.model_dump_json()) == kept
    pcm = fmt.model_validate(audio_format())
    assert pcm.bits_per_sample == 16
    assert fmt.model_validate_json(pcm.model_dump_json()).bits_per_sample == 16


# ------------------------------------------------------------------------------- URI rules


def test_artifact_uri_rejects_presigned_signature() -> None:
    """P-20 (ADR 0007 Decisions 6 and 6b): ``Artifact.uri`` is never a presigned or keyed URL, which
    is a bearer credential until it expires. Userinfo and the signature parameters of AWS, Google
    Cloud, CloudFront and Azure SAS are rejected, in any case and when percent-encoded; plain
    object URIs (https, http, s3, gs) are accepted."""
    for uri in (
        f"https://{HOST}/calls/1.wav",
        f"http://{HOST}/calls/1.wav",
        f"https://{HOST}/calls/1.wav?version=3&download=1",
        "s3://recordings-bucket/calls/2026/10/1.wav",
        "gs://recordings-bucket/calls/2026/10/1.wav",
        f"https://{HOST}:8443/calls/1.wav",
    ):
        _accept_uri(uri)
    signed = (
        f"https://{HOST}/1.wav?X-Amz-Signature=0123456789abcdef",
        f"https://{HOST}/1.wav?x-amz-signature=0123",
        f"https://{HOST}/1.wav?X-AMZ-SIGNATURE=0123",
        f"https://{HOST}/1.wav?%58-Amz-Signature=0123",
        f"https://{HOST}/1.wav?X-Amz-Credential=ok&X-Amz-Date=20261022T000000Z",
        f"https://{HOST}/1.wav?X-Amz-Security-Token=abc",
        f"https://{HOST}/1.wav?X-Goog-Signature=0123456789",
        f"https://{HOST}/1.wav?X-Goog-Credential=svc",
        f"https://{HOST}/1.wav?Expires=1790000000&Signature=abc&Key-Pair-Id=K1",
        f"https://{HOST}/1.wav?sv=2022-11-02&se=2026-10-23&sp=r&sig=abc",
        f"https://{HOST}/1.wav?signature=abc",
        f"https://{HOST}/1.wav?token=abc",
        f"https://{HOST}/1.wav?key=abc",
        f"https://{HOST}/1.wav?api_key=abc",
        "s3://recordings-bucket/1.wav?X-Amz-Signature=abc",
        f"https://{HOST}/1.wav?a=1;sig=abc",
        f"https://{HOST}/1.wav?a=1&&sig=abc",
    )
    for uri in signed:
        _reject_uri(uri)
    for uri in (
        f"https://user:secret@{HOST}/1.wav",
        f"https://user@{HOST}/1.wav",
        f"https://:secret@{HOST}/1.wav",
        "s3://user:secret@recordings-bucket/1.wav",
    ):
        _reject_uri(uri)
    for uri in (
        "recordings/1.wav",
        "/data/recordings/1.wav",
        "",
        "https://",
        f"https://{HOST}/my recording.wav",
        f"https://{HOST}/1.wav\n",
        f" https://{HOST}/1.wav",
        f"HTTPS://{HOST}/1.wav",
        f"https://{HOST}/%zz.wav",
    ):
        _reject_uri(uri)
    for bad in (None, 7, ["https://x"], {"u": 1}):
        assert_rejected3(entity_class3("Artifact"), artifact_doc(uri=bad), field="uri")
    assert "uri" in schema_of(entity_class3("Artifact"))["properties"]


def test_artifact_uri_scheme_set_closed() -> None:
    """O5 (owner, revision 12; ADR 0007 Decision 6c; api-surface 22.9 item 16): the scheme set of
    ``Artifact.uri`` is closed: ``http``, ``https``, ``s3``, ``gs`` and ``file``. Anything else is
    rejected at ``uri``: other network schemes, ``data:`` and ``blob:`` (a URI that embeds the
    recording or a handle to it), relative references, a scheme-relative ``//host`` reference,
    and a store scheme without a bucket or a host."""
    artifact = entity_class3("Artifact")
    for uri in (
        "http://h/1.wav",
        "https://h/1.wav",
        "s3://bucket/1.wav",
        "gs://bucket/1.wav",
        "file:///data/recordings/1.wav",
    ):
        _accept_uri(uri)
        loaded = _artifact(uri=uri)
        assert artifact.model_validate_json(loaded.model_dump_json()) == loaded
    for uri in (
        "ftp://h/1.wav",
        "data:audio/wav;base64,AAAA",
        "blob:https://h/1",
        "ws://h/1.wav",
        "wss://h/1.wav",
        "sip:rec@h",
        "az://c/1.wav",
        "hdfs://h/1.wav",
        "//h/1.wav",
        "./1.wav",
        "s3:///1.wav",
        "https:///1.wav",
    ):
        _reject_uri(uri)


def test_artifact_file_uri_form() -> None:
    """O5 (details confirmed by the owner in revision 13; ADR 0007 Decision 6c): a ``file:`` URI is
    ``file:///`` followed by an absolute path, nothing else. No host (``localhost`` in any case
    and every other host are rejected), not the ``file:/path`` or ``file:path`` forms, no query,
    no ``.`` or ``..`` segment, not an empty path. ``file:///C:/...`` is an absolute path and is
    accepted, and so is a percent-escaped path. A credential pattern in the path is rejected and
    the message does not show it."""
    for uri in (
        "file:///data/recordings/1.wav",
        "file:///C:/rec/1.wav",
        "file:///data/rec%20one.wav",
    ):
        _accept_uri(uri)
    for uri in (
        "file:/data/1.wav",
        "file://localhost/data/1.wav",
        "file://LOCALHOST/data/1.wav",
        "file://server/share/1.wav",
        "file:data/1.wav",
        "file://./1.wav",
        "file:///",
        "file:///data/../1.wav",
        "file:///data/./1.wav",
        "file:///data/1.wav?x=1",
    ):
        _reject_uri(uri)
    artifact = entity_class3("Artifact")
    for secret in (PASTED_KEY, AWS_KEY):
        uri = "file:///home/u/" + secret + ".wav"
        error = assert_rejected3(artifact, artifact_doc(uri=uri), field="uri")
        for text in error_texts(error):
            assert secret not in text, text


def test_artifact_uri_fragment_rejected() -> None:
    """O5 (ADR 0007 Decision 6c): a fragment is rejected for every scheme, an empty one included
    (a range of a recording is an ``EvidenceRef{kind: artifact_range}``, not part of the URI). The
    message says that a fragment is the problem and does not show the URI."""
    artifact = entity_class3("Artifact")
    for uri in (
        "https://h/1.wav#t=10",
        "https://h/1.wav#",
        "s3://bucket/1.wav#x",
        "file:///data/1.wav#x",
    ):
        error = assert_rejected3(artifact, artifact_doc(uri=uri), field="uri")
        messages = [str(e["msg"]).lower() for e in error.errors(include_input=False)]
        assert any("fragment" in message for message in messages), messages
        for text in error_texts(error):
            assert uri not in text, text


LOOPBACK_ACCEPTED = (
    "http://localhost:9000/1.wav",
    "https://LOCALHOST/1.wav",
    "http://localhost./1.wav",
    "http://rec.localhost/1.wav",
    "http://127.0.0.1/1.wav",
    "http://127.9.9.9:8080/1.wav",
    "http://127.1/1.wav",
    "http://[::1]/1.wav",
    "http://[0:0:0:0:0:0:0:1]:9000/1.wav",
    "http://[::ffff:127.0.0.1]/1.wav",
)
# Spellings that D1 (api-surface 22.12) counts as loopback besides the ten above. They are tested
# on the pure function only: whether the record accepts them is not pinned (no test may reject a
# loopback host on the record, and none requires accepting these).
LOOPBACK_SPELLINGS = (
    "http://0x7f.0.0.1/1.wav",
    "http://2130706433/1.wav",
    "http://0177.0.0.1/1.wav",
    "http://0x7f000001/1.wav",
    "http://127.0.1/1.wav",
    "http://127.0.0.0x1/1.wav",
    "http://127.0.0.1./1.wav",
    "http://LocalHost./1.wav",
    "http://a.b.LOCALHOST/1.wav",
    "http://%6Cocalhost/1.wav",
    "http://[0000:0000:0000:0000:0000:0000:0000:0001]/1.wav",
    "http://[::ffff:7f00:1]/1.wav",
    "http://[::FFFF:127.255.255.254]/1.wav",
    "http://[0:0:0:0:0:ffff:7f00:1]/1.wav",
)
NOT_LOOPBACK = (
    "https://example.com/1.wav",
    "http://localhost.example/1.wav",
    "http://mylocalhost/1.wav",
    "http://127.0.0.1.example/1.wav",
    "http://128.0.0.1/1.wav",
    "http://0.0.0.0/1.wav",
    "http://[::]/1.wav",
    "http://[::2]/1.wav",
    "http://10.0.0.1/1.wav",
    "s3://localhost/1.wav",
    "file:///data/1.wav",
)
# Further near misses that follow from D1: not in 127.0.0.0/8 after WHATWG parsing, not ::1 or an
# IPv4-mapped loopback address, one trailing dot removed and no more, other private ranges.
NOT_LOOPBACK_NEAR = (
    "http://localhost../1.wav",
    "http://localhostx/1.wav",
    "http://xlocalhost/1.wav",
    "http://localhost.localdomain/1.wav",
    "http://126.255.255.255/1.wav",
    "http://128.0.0.0/1.wav",
    "http://1.0.0.127/1.wav",
    "http://127.0.0.256/1.wav",
    "http://127.0.0.1.1/1.wav",
    "http://127.0.0.1.0/1.wav",
    "http://126.256.0.1/1.wav",
    "http://192.168.0.1/1.wav",
    "http://169.254.0.1/1.wav",
    "http://host.docker.internal/1.wav",
    "http://[fe80::1]/1.wav",
    "http://[::1:0]/1.wav",
    "http://[1::1]/1.wav",
    "http://[::ffff:128.0.0.1]/1.wav",
    "http://[::ffff:10.0.0.1]/1.wav",
    "http://[::127.0.0.1]/1.wav",
    "https://example.com/1.wav?host=localhost",
    "https://example.com/localhost/1.wav",
    "s3://127.0.0.1/1.wav",
    "gs://localhost/1.wav",
)


def test_artifact_uri_loopback_host_rules() -> None:
    """O8 (owner, revision 13; ADR 0007 Decision 6d; D1 in api-surface 22.12), the record half: a
    loopback host in an ``http`` or ``https`` URI is a valid ``Artifact.uri`` (a local object store
    in a CLI or CI setup) and round-trips unchanged; P1-14 rejects it in shared and benchmark
    exports. ``artifact.is_loopback_uri(uri)`` is the pure definition: ``localhost`` and
    ``*.localhost``, anything the WHATWG IPv4 parser maps into 127.0.0.0/8, ``::1`` and the
    IPv4-mapped loopback addresses, compared after ASCII case-folding and the removal of one
    trailing dot, as written and percent-decoded once. Not loopback: the unspecified addresses,
    private ranges, look-alike names, and the authority of ``s3``, ``gs`` and ``file`` URIs."""
    module = api("artifact")
    artifact = entity_class3("Artifact")
    for uri in LOOPBACK_ACCEPTED:
        _accept_uri(uri)
        loaded = _artifact(uri=uri)
        assert artifact.model_validate_json(loaded.model_dump_json()) == loaded
        assert module.is_loopback_uri(uri) is True, uri
    for uri in LOOPBACK_SPELLINGS:
        assert module.is_loopback_uri(uri) is True, uri
    for uri in (*NOT_LOOPBACK, *NOT_LOOPBACK_NEAR):
        assert module.is_loopback_uri(uri) is False, uri
    assert not (set(LOOPBACK_ACCEPTED) & set(NOT_LOOPBACK))
    for uri in ("https://example.com/1.wav", "http://localhost.example/1.wav"):
        _accept_uri(uri)  # a name that only looks like a loopback host is an ordinary host


def test_channel_map_channels_nonempty() -> None:
    """N36 (resolved part, revision 11; api-surface 22.9 item 7): a ``ChannelMap`` names at least
    one channel. An empty ``channels`` list is rejected by pydantic (at ``channels``) and by the
    JSON Schema (``minItems: 1``), and the same map with one channel is valid."""
    model = registered_class("ChannelMap")
    doc = channel_map_doc()
    assert model.model_validate(doc).channels
    empty = {**doc, "channels": []}
    with pytest.raises(ValidationError) as caught:
        model.model_validate(empty)
    assert any("channels" in [str(part) for part in e["loc"]] for e in caught.value.errors())
    assert not schema_accepts(model, empty)
    assert schema_accepts(model, doc)
    assert schema_of(model)["properties"]["channels"]["minItems"] == 1
    one = {**doc, "channels": [{"index": 0, "role": "agent"}]}
    assert model.model_validate(one).channels[0].role == "agent"


def _urls_with_param(name: str) -> list[str]:
    return [f"https://{HOST}/1.wav?{name}=abc", f"https://{HOST}/1.wav?room=1&{name}=abc"]


def test_artifact_uri_rejects_wider_credential_names_and_patterns() -> None:
    """P-60 and N15 (ADR 0007 Decision 6b; accepted by the owner, revision 9 and 10), the Artifact
    half: every one of the 25 names of ``credentials.URL_CREDENTIAL_PARAMETER_NAMES`` is rejected
    in the query of an artifact URI in any letter case, with ``-`` and ``_`` interchangeable and
    percent-encoded; look-alike names (``tokenizer``, ``access.token``, ``x-api-keys``) are not
    credential names. A known credential pattern (ADR 0007 Decision 5a, with its boundary rule)
    anywhere in the URI, as written or percent-decoded once, is rejected, while a high-entropy
    path segment without a named pattern is accepted."""
    names = api("credentials").URL_CREDENTIAL_PARAMETER_NAMES
    assert len(names) == 25
    for name in sorted(names):
        spellings = {
            name,
            name.upper(),
            name.title(),
            name.replace("-", "_"),
            name.replace("_", "-"),
            "%" + format(ord(name[0]), "02X") + name[1:],
        }
        for spelling in spellings:
            for uri in _urls_with_param(spelling):
                _reject_uri(uri)
        assert name
    for name in (
        "tokenizer",
        "monkey",
        "keyboard",
        "keys",
        "signal",
        "signature_version",
        "secretary",
        "passwords",
        "token_id",
        "access_token_id",
        "credential_type",
        "access.token",
        "accesstoken",
        "x-api-keys",
        "api--key",
    ):
        for uri in _urls_with_param(name):
            _accept_uri(uri)
    samples = {
        "openai": PASTED_KEY,
        "aws": AWS_KEY,
        "github": "gh" + "p_" + "A1" * 18,
        "jwt": "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + ".sig",
    }
    for secret in samples.values():
        for uri in (
            f"https://{HOST}/{secret}/1.wav",
            f"https://{HOST}/calls/task_{secret}/1.wav",
            f"https://{HOST}/1.wav?room={secret}",
            f"https://{HOST}/1.wav?room=1&label={secret}",
            f"s3://bucket/{secret}",
            f"gs://bucket/dir/{secret}.wav",
            f"https://{secret}.{HOST}/1.wav",
            f"https://bucket-{secret}.{HOST}/1.wav",
        ):
            _reject_uri(uri)
    _reject_uri(f"https://{HOST}/%73k-" + PASTED_KEY[3:])  # decoded once, then matched
    _reject_uri(f"https://{HOST}/1.wav?room=%41KIA" + AWS_KEY[4:])
    _accept_uri(f"https://{HOST}/desk-" + PASTED_KEY[3:])  # the boundary rule: not a key
    _accept_uri(f"https://{HOST}/1.wav?room=x{AWS_KEY}")
    _accept_uri(f"https://{HOST}/rec/Zx9Kq2LmN4pR7sT1vW3yB5dF8gH0jA6cEu/1.wav")  # high entropy ok
    _accept_uri(f"https://{HOST}/1.wav?etag=Zx9Kq2LmN4pR7sT1vW3yB5dF8gH0jA6cEu")
    # a percent-encoded space in a name still reads as a different name
    _accept_uri(f"https://{HOST}/1.wav?{quote('access token')}=1")


def test_artifact_errors_do_not_echo_uri_credentials() -> None:
    """Proposed name (ADR 0007 Decision 8 (a), (c), (d)): the error for a rejected URI names the
    rule and the URL part and never contains the credential, in ``str``, ``repr``,
    ``errors(include_input=False)`` and ``json(include_input=False)``; ``Artifact`` hides its input
    in validation errors, and so does every model that embeds an Artifact (the set is closed
    under embedding)."""
    artifact = entity_class3("Artifact")
    assert artifact.model_config.get("hide_input_in_errors") is True
    secrets = {
        "key": PASTED_KEY,
        "aws": AWS_KEY,
        "password": "hunter2-" + "pa55word",
    }
    attempts = [
        (f"https://{HOST}/{PASTED_KEY}/1.wav", PASTED_KEY),
        (f"https://{HOST}/1.wav?room={AWS_KEY}", AWS_KEY),
        (f"https://{HOST}/1.wav?sig={secrets['password']}", secrets["password"]),
        (f"https://user:{secrets['password']}@{HOST}/1.wav", secrets["password"]),
        (f"https://{HOST}/1.wav?X-Amz-Signature={secrets['password']}", secrets["password"]),
    ]
    for uri, secret in attempts:
        error = assert_rejected3(artifact, artifact_doc(uri=uri), field="uri")
        for text in error_texts(error):
            assert secret not in text, (uri, text)
        with pytest.raises(ValidationError) as caught:
            artifact.model_validate_json(json.dumps(artifact_doc(uri=uri)))
        for text in error_texts(caught.value):
            assert secret not in text
    embedding = [
        m.__name__
        for m in api("registry").model_classes()
        if artifact in embedded_models(m) and m.model_config.get("hide_input_in_errors") is not True
    ]
    assert not embedding, f"models that embed an Artifact but show their input: {embedding}"


# ----------------------------------------------------------------------------- ChannelMap


def test_channel_role_set_exact() -> None:
    """Proposed name (P-31, ADR 0003 Decision 1, ADR 0012 Decision 5): the channel roles are
    caller, agent, unknown and mixed (``mixed`` is a mono channel carrying both speakers), and
    the determination is declared or inferred. The roles of removed concepts are rejected."""
    channel_map = registered_class("ChannelMap")
    entry = registered_class("ChannelAssignment")
    assert set(property_enum(channel_map, "determination")) == {"declared", "inferred"}
    assert set(property_enum(entry, "role")) == {"caller", "agent", "unknown", "mixed"}
    for role in ("caller", "agent", "unknown", "mixed"):
        assert entry.model_validate({"index": 0, "role": role}).role == role
    for bad in ("ivr", "human_agent", "user", "bot", "stereo", "Agent", "", None):
        with pytest.raises(ValidationError):
            entry.model_validate({"index": 0, "role": bad})
    for bad in ("measured", "guessed", "Declared", "", None):
        with pytest.raises(ValidationError):
            channel_map.model_validate(channel_map_doc(determination=bad))
    assert not contains_key(schema_of(channel_map), "const")
    assert not {"id", "schema_version", "created_at", "metadata"} & set(channel_map.model_fields)
    assert set(channel_map.model_fields) == {
        "artifact_id",
        "channels",
        "determination",
        "method",
        "confidence",
    }
    declared = _channel_map()
    assert channel_map.model_validate_json(declared.model_dump_json()) == declared
    assert schema_accepts(channel_map, json.loads(declared.model_dump_json()))
    bad_artifacts: list[Any] = [new_id("run"), str(api("ids").ArtifactId.new().uuid), "art_x", 7]
    for bad_artifact in bad_artifacts:
        with pytest.raises(ValidationError):
            channel_map.model_validate({**channel_map_doc(), "artifact_id": bad_artifact})


def test_channel_map_declared_forbids_confidence() -> None:
    """ADR 0012 I8: a ``declared`` map (the producer's documented layout, for example Pipecat's
    stereo left=caller right=agent) carries ``method`` naming the declaration and no
    ``confidence``: a declaration is not a probability, and any confidence, even 1.0 or 0.0, is
    rejected."""
    channel_map = registered_class("ChannelMap")
    declared = _channel_map()
    assert declared.determination == "declared"
    assert declared.method == "pipecat_audio_buffer_processor_stereo"
    assert declared.confidence is None
    for confidence in (0.0, 0.5, 1.0):
        with pytest.raises(ValidationError):
            channel_map.model_validate(channel_map_doc(confidence=confidence))
    with pytest.raises(ValidationError):
        channel_map.model_validate(channel_map_doc(method=None))
    with pytest.raises(ValidationError):
        channel_map.model_validate({k: v for k, v in channel_map_doc().items() if k != "method"})


def test_channel_map_inferred_requires_method_confidence() -> None:
    """ADR 0012 I9, A-21: an ``inferred`` map (our analysis) needs ``method`` and a ``confidence``
    in [0,1]; either one missing is rejected, and so is an out-of-range or non-finite
    confidence."""
    channel_map = registered_class("ChannelMap")
    inferred = channel_map.model_validate(inferred_map_doc())
    assert (inferred.determination, inferred.confidence) == ("inferred", 0.9)
    for confidence in (0.0, 1.0, 0.25):
        assert (
            channel_map.model_validate(inferred_map_doc(confidence=confidence)).confidence
            == confidence
        )
    no_confidence = {k: v for k, v in inferred_map_doc().items() if k != "confidence"}
    no_method = {k: v for k, v in inferred_map_doc().items() if k != "method"}
    with pytest.raises(ValidationError):
        channel_map.model_validate(no_confidence)
    with pytest.raises(ValidationError):
        channel_map.model_validate({**no_confidence, "confidence": None})
    with pytest.raises(ValidationError):
        channel_map.model_validate(no_method)
    for bad in (-0.01, 1.01, float("nan"), float("inf"), "0.9", True, 2):
        with pytest.raises(ValidationError):
            channel_map.model_validate(inferred_map_doc(confidence=bad))
    for bad in ("two words", "", DEVANAGARI):
        with pytest.raises(ValidationError):
            channel_map.model_validate(inferred_map_doc(method=bad))
    assert '"minimum": 0' in json.dumps(schema_of(channel_map)["properties"]["confidence"])


def test_channel_map_single_agent_channel() -> None:
    """ADR 0012 I10, A-21: at most one channel of a map has ``role=agent`` (a second AI agent is a
    separate run). Zero agent channels is valid (the agent is then not identified), and so is
    one."""
    channel_map = registered_class("ChannelMap")
    good_layouts: list[tuple[str, ...]] = [
        ("caller", "agent"),
        ("agent", "caller"),
        ("caller", "unknown"),
        ("mixed",),
    ]
    for roles in good_layouts:
        loaded = channel_map.model_validate(channel_map_doc(channels=_roles(*roles)))
        assert [c.role for c in loaded.channels] == list(roles)
    bad_layouts: list[tuple[str, ...]] = [
        ("agent", "agent"),
        ("agent", "caller", "agent"),
        ("agent", "agent", "agent"),
    ]
    for roles in bad_layouts:
        with pytest.raises(ValidationError):
            channel_map.model_validate(channel_map_doc(channels=_roles(*roles)))
        with pytest.raises(ValidationError):
            channel_map.model_validate(inferred_map_doc(channels=_roles(*roles)))
    for bad in (
        {"index": -1, "role": "agent"},
        {"index": 1.5, "role": "agent"},
        {"index": True, "role": "agent"},
    ):
        with pytest.raises(ValidationError):
            channel_map.model_validate(channel_map_doc(channels=[bad]))
    with pytest.raises(ValidationError):
        channel_map.model_validate(
            channel_map_doc(channels=[{"index": 0, "role": "agent", "gain": 1}])
        )


def test_channel_map_indexes_unique() -> None:
    """Proposed name (a reading of ADR 0012 Decision 5, listed as an ambiguity): two entries for
    one channel index would assign two roles to one channel and are rejected."""
    channel_map = registered_class("ChannelMap")
    with pytest.raises(ValidationError):
        channel_map.model_validate(
            channel_map_doc(
                channels=[{"index": 0, "role": "caller"}, {"index": 0, "role": "agent"}]
            )
        )
    loaded = channel_map.model_validate(
        channel_map_doc(channels=[{"index": 1, "role": "agent"}, {"index": 0, "role": "caller"}])
    )
    assert [c.index for c in loaded.channels] == [1, 0]  # order is kept, not sorted


def test_channel_index_within_channels() -> None:
    """ADR 0012 I11: every channel ``index`` is smaller than the ``channels`` count of the
    artifact's ``AudioFormat`` (``check_channel_map`` with the artifact): a stereo recording has
    indexes 0 and 1, a mono one only 0."""
    check = api("artifact").check_channel_map
    stereo = _artifact(audio_format=audio_format(channels=2))
    mono = _artifact(audio_format=audio_format(channels=1))
    ok = _channel_map(artifact_id=str(stereo.id))
    check(ok, stereo)
    check(_channel_map(artifact_id=str(mono.id), channels=_roles("mixed")), mono)
    with pytest.raises(ValueError, match=r"\b2\b"):
        check(
            _channel_map(artifact_id=str(stereo.id), channels=_roles("caller", "agent", "unknown")),
            stereo,
        )
    with pytest.raises(ValueError, match=r"\b1\b"):
        check(_channel_map(artifact_id=str(mono.id)), mono)
    with pytest.raises(ValueError, match=r"\b5\b"):
        check(
            _channel_map(artifact_id=str(stereo.id), channels=[{"index": 5, "role": "agent"}]),
            stereo,
        )


def test_channel_map_artifact_must_be_audio() -> None:
    """ADR 0012 I11: the artifact of a channel map must be audio (it has an ``audio_format``) and
    must be the artifact the map names."""
    check = api("artifact").check_channel_map
    plain = make3("Artifact", **{"audio_format": None})
    assert plain.audio_format is None
    with pytest.raises(ValueError, match=str(plain.id)):
        check(_channel_map(artifact_id=str(plain.id)), plain)
    audio = _artifact()
    with pytest.raises(ValueError, match=str(audio.id)):
        check(_channel_map(artifact_id=new_id("art")), audio)
    check(_channel_map(artifact_id=str(audio.id)), audio)


def test_unknown_agent_channel_excludes_agent_audio() -> None:
    """ADR 0004 Decision 5, I9: if no ChannelMap identifies an agent channel, ``agent_audio`` is
    not available. ``agent_audio_available`` is true iff some map has an agent channel; a Run
    cannot list ``agent_audio`` in ``available_inputs`` without one (an unknown or inferred-only
    caller/unknown layout, or no map at all, does not identify the agent), and may omit it even
    when the agent is identified."""
    available = api("artifact").agent_audio_available
    agent_map = _channel_map()
    unknown_map = _channel_map(channels=_roles("caller", "unknown"))
    assert available([agent_map]) is True
    assert available([unknown_map]) is False
    assert available([]) is False
    assert available([unknown_map, agent_map]) is True
    inferred = registered_class("ChannelMap").model_validate(inferred_map_doc())
    assert available([inferred]) is True
    run_model = entity_class3("Run")
    with_agent = ["audio", "agent_audio", "transcript"]
    ok = make3("Run", available_inputs=with_agent, channel_maps=[channel_map_doc()])
    assert "agent_audio" in ok.available_inputs
    make3("Run", available_inputs=["audio"], channel_maps=[channel_map_doc()])  # not forced
    for maps in ([], [channel_map_doc(channels=_roles("caller", "unknown"))]):
        assert_rejected3(
            run_model,
            doc3("Run", available_inputs=with_agent, channel_maps=maps),
            field="available_inputs",
        )
    make3("Run", available_inputs=["audio", "transcript"], channel_maps=[])


def test_mono_channel_mixed_excludes_agent_audio() -> None:
    """P-31 (ADR 0012 I17): a mono channel carrying both speakers is ``mixed``; it leaves
    ``agent_audio`` unavailable (the agent *is* on it, but not separable), also next to a caller
    channel, and a Run cannot claim ``agent_audio`` on that evidence alone. A map with one
    ``agent`` channel and one ``mixed`` one still identifies the agent."""
    available = api("artifact").agent_audio_available
    mono = _channel_map(channels=_roles("mixed"))
    assert mono.channels[0].role == "mixed"
    assert available([mono]) is False
    assert available([_channel_map(channels=_roles("caller", "mixed"))]) is False
    assert available([_channel_map(channels=_roles("mixed", "agent"))]) is True
    assert_rejected3(
        entity_class3("Run"),
        doc3(
            "Run",
            available_inputs=["audio", "agent_audio"],
            channel_maps=[channel_map_doc(channels=_roles("mixed"))],
        ),
        field="available_inputs",
    )
    make3(
        "Run",
        available_inputs=["audio", "transcript"],
        channel_maps=[channel_map_doc(channels=_roles("mixed"))],
    )


def test_artifact_errors_do_not_quote_rejected_values() -> None:
    """N42 (owner, option A; ADR 0007 Decision 8 (c) extended; api-surface 22.12): no shared
    validator quotes a rejected value in any field of a model that hides its input; an error
    gives the field location and the rule only. A rejected typed
    ID, closed-set member, MachineKey and hash pattern on ``Artifact`` (also inside its
    ``audio_format`` and ``privacy`` marker) do not appear in ``str``, ``repr``,
    ``errors(include_input=False)`` or ``json(include_input=False)``, through ``model_validate``
    and ``model_validate_json``, and the error is still located at the field. Proposed name
    (SCOPE section 5, P1-10a)."""
    artifact = entity_class3("Artifact")
    human = privacy_human
    cases: list[tuple[str, dict[str, Any], str]] = [
        ("typed ID", artifact_doc(id=marked_id("art")), "id"),
        ("typed ID", artifact_doc(turn_ref=marked_id("turn")), "turn_ref"),
        ("pattern", artifact_doc(sha256="g" + QUOTE_MARK), "sha256"),
        (
            "MachineKey",
            artifact_doc(audio_format=audio_format(container=marked_key())),
            "container",
        ),
        ("MachineKey", artifact_doc(audio_format=audio_format(codec=marked_key())), "codec"),
        (
            "ClosedSet",
            artifact_doc(privacy=human(human_subject_reason=marked_set())),
            "human_subject_reason",
        ),
        ("ClosedSet", artifact_doc(privacy=human(consent={"status": marked_set()})), "status"),
    ]
    for label, doc, field in cases:
        assert_input_hidden(artifact, doc, QUOTE_MARK, field=field, label=label)
