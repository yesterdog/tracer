# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Credential rules for stored URLs (ADR 0007 Decisions 6, 6a and 6b; P-20, P-60, N15).

Endpoint URLs are copied into every run and export, so a URL that carries a credential is
rejected, never stored or flagged. Three rules apply, in this order:

1. **Syntax.** An absolute URI made of the RFC 3986 characters only (ASCII, no white space or
   control characters). ``ws``/``webrtc``: a lowercase scheme of the transport, ``//`` and a host;
   any userinfo (an ``@`` in the authority, which ends at the first ``/``, ``?`` or ``#``) and any
   fragment are rejected. ``sip``: ``sip:``/``sips:`` and a host; the user part is the address
   and is allowed, but a password (a ``:`` in the userinfo, also as ``%3A``) is rejected. Every
   percent escape in the URL must be well formed and decode as UTF-8.
2. **Credential parameter names.** Query parameters (ws, webrtc) and SIP ``;uri-parameters`` and
   ``?headers`` are split at ``&`` and ``;``; a name is the text before the first ``=``. It is
   percent-decoded once, case-folded, ``-`` is read as ``_``, and the whole name is compared with
   ``credentials.URL_CREDENTIAL_PARAMETER_NAMES``.
3. **Credential patterns.** A ``credentials.CREDENTIAL_PATTERNS`` match (with its left boundary)
   anywhere in a part of the URL, as written or percent-decoded once, rejects it. The
   high-entropy reason of Decision 5a is not applied to URLs.

Errors name the rule and the URL part (and, outside a model that hides its input, a parameter
name as found in the URL), never the value (ADR 0007 Decision 8; inside a hiding model no part of
the URL is quoted, finding N42). The checks raise ``PydanticCustomError``, so they report as a
``ValidationError`` inside a model. Rules 2 and 3 cannot be written in JSON Schema (code-only);
the JSON Schema patterns below state the scheme and the character set.

``Artifact.uri`` (ADR 0007 Decisions 6c and 6d; ``check_artifact_uri``) adds a closed scheme set
(``http``, ``https``, ``s3``, ``gs``, ``file``), the ``file:///`` form and the rejection of every
fragment. ``is_loopback_host_uri`` is the loopback definition of Decision 6d (api-surface 22.12
D1, with the spellings qa pinned): ``localhost`` and ``*.localhost``, every host the WHATWG URL
Standard IPv4 parser maps into 127.0.0.0/8, and ``::1`` and the IPv4-mapped loopback addresses,
compared after ASCII case-folding and the removal of one trailing dot, as written and
percent-decoded once.

``PriceProvenance.url`` (ADR 0007 Decision 6e; ``check_price_source_url``) is an ``http``/``https``
URL whose fragment is allowed and checked like the query, and whose loopback host is rejected on
the record itself (N66).
"""

from __future__ import annotations

import contextlib
import ipaddress
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Final

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

from tracer_evalschema._hiding import values_hidden
from tracer_evalschema.credentials import contains_credential_pattern, is_credential_parameter_name

__all__ = [
    "ARTIFACT_URI_PATTERN",
    "ARTIFACT_URI_SCHEMES",
    "PRICE_SOURCE_URL_PATTERN",
    "SIP_URI_PATTERN",
    "WEBRTC_URL_PATTERN",
    "WEBSOCKET_URL_PATTERN",
    "UrlRule",
    "check_artifact_uri",
    "check_price_source_url",
    "check_sip_uri",
    "check_webrtc_url",
    "check_websocket_url",
    "is_loopback_host_uri",
    "percent_decode_once",
]

# The RFC 3986 characters: unreserved, reserved and "%" (escapes are checked separately).
_URI_CHARACTERS = "A-Za-z0-9._~:/?#\\[\\]@!$&'()*+,;=%-"
_URI_TEXT = re.compile(f"[{_URI_CHARACTERS}]+")
_NO_FRAGMENT = _URI_CHARACTERS.replace("#", "")

WEBSOCKET_URL_PATTERN = f"^wss?://[{_NO_FRAGMENT}]+$"
WEBRTC_URL_PATTERN = f"^https?://[{_NO_FRAGMENT}]+$"
SIP_URI_PATTERN = f"^sips?:[{_NO_FRAGMENT}]+$"
_NO_QUERY = _NO_FRAGMENT.replace("?", "")
ARTIFACT_URI_PATTERN = f"^(?:(?:https?|s3|gs)://[{_NO_FRAGMENT}]+|file:///[{_NO_QUERY}]+)$"
"""The scheme set and the character set of ``Artifact.uri`` (the rest of the rules is code-only)."""

ARTIFACT_URI_SCHEMES: Final = ("http", "https", "s3", "gs", "file")

_HEX = frozenset("0123456789abcdefABCDEF")
_IP_LITERAL = re.compile(r"\[[0-9A-Fa-f:.]+\]")
_REG_NAME = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=%-]+")
_SIP_HOST = re.compile(r"[A-Za-z0-9._~%-]+")  # RFC 3986 unreserved and escapes
_PORT = re.compile("[0-9]*")
_SIP_PORT = re.compile("[0-9]+")
_PARAMETER_SEPARATORS = re.compile("[&;]")


def _error(error_type: str, message: str, **context: str) -> PydanticCustomError:
    return PydanticCustomError(error_type, message, context or None)


def percent_decode_once(text: str) -> str:
    """Decode every ``%XX`` escape of ``text`` once and read the bytes as UTF-8.

    Raises ``ValueError`` for a malformed escape (``%``, ``%4``, ``%zz``) or bytes that are not
    UTF-8 (``%ff``, ``%C3``, overlong forms such as ``%C0%AF``). ``%2573`` decodes to ``%73``.
    """
    out = bytearray()
    index = 0
    while index < len(text):
        char = text[index]
        if char == "%":
            pair = text[index + 1 : index + 3]
            if len(pair) != 2 or not set(pair) <= _HEX:
                raise ValueError("malformed percent escape")
            out.append(int(pair, 16))
            index += 3
        else:
            out.extend(char.encode("utf-8", "surrogatepass"))
            index += 1
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("percent escapes that are not UTF-8") from None


# --------------------------------------------------------------------------------- shared steps


def _check_characters(url: str, what: str) -> None:
    if _URI_TEXT.fullmatch(url) is None:
        raise _error(
            "url_characters",
            f"a {what} must be an RFC 3986 URI: ASCII letters, digits and URI punctuation only, "
            "with no white space, control or non-ASCII characters (percent-encode them)",
        )
    try:
        percent_decode_once(url)
    except ValueError:
        raise _error(
            "url_percent_encoding",
            f"the {what} holds a malformed percent escape or escapes that are not UTF-8",
        ) from None


def _check_scheme(url: str, schemes: tuple[str, ...], what: str) -> str:
    scheme, separator, rest = url.partition(":")
    expected = " or ".join(f"{s}:" for s in schemes)
    if separator and scheme.lower() in schemes and scheme not in schemes:
        raise _error("url_scheme", f"the {what} scheme must be lower case ({expected})")
    if not separator or scheme not in schemes:
        raise _error("url_scheme", f"a {what} must start with {expected}")
    return rest


def _check_parameter_names(text: str, where: str, what: str) -> None:
    for segment in _PARAMETER_SEPARATORS.split(text):
        name = segment.partition("=")[0]
        if name and is_credential_parameter_name(percent_decode_once(name)):
            if values_hidden():  # inside a hiding model no part of the URL is quoted (N42)
                raise _error(
                    "url_credential_parameter",
                    f"the {what} carries a credential: a {where} name is a credential parameter "
                    "(ADR 0007 Decision 6b); store a reference to the key, never the key",
                )
            # The name is shown as found in the URL: it is a member of the credential name set
            # (up to case, escapes and '-'/'_'), never the value.
            raise _error(
                "url_credential_parameter",
                f"the {what} carries a credential: {where} name '{{parameter}}' is a credential "
                "parameter (ADR 0007 Decision 6b); store a reference to the key, never the key",
                parameter=name,
            )


def _check_patterns(parts: Iterable[tuple[str, str]], what: str) -> None:
    for part, text in parts:
        if text and (
            contains_credential_pattern(text)
            or contains_credential_pattern(percent_decode_once(text))
        ):
            raise _error(
                "url_credential_pattern",
                f"the {what} carries a credential: a known credential pattern in the {part} "
                "(ADR 0007 Decision 6b); a presigned or keyed URL is runtime-only",
            )


# ---------------------------------------------------------------------- ws, wss, http, https


def _split_host_port(authority: str, what: str) -> str:
    if authority.startswith("["):
        end = authority.find("]")
        host, rest = authority[: end + 1], authority[end + 1 :]
        if end < 0 or _IP_LITERAL.fullmatch(host) is None:
            raise _error("url_host", f"the {what} has a malformed IP literal host")
    else:
        host, colon, port = authority.partition(":")
        rest = colon + port
        if not host or _REG_NAME.fullmatch(host) is None:
            raise _error("url_host", f"a {what} needs a host")
    if rest and (not rest.startswith(":") or _PORT.fullmatch(rest[1:]) is None):
        raise _error("url_port", f"the {what} port must be digits")
    return host


def _check_hierarchical(url: str, schemes: tuple[str, ...], what: str) -> str:
    _check_characters(url, what)
    rest = _check_scheme(url, schemes, what)
    if not rest.startswith("//"):
        raise _error("url_absolute", f"a {what} must be absolute: scheme://host/...")
    if "#" in rest:
        raise _error("url_fragment", f"a {what} must not have a fragment (#...)")
    rest = rest[2:]
    end = min((i for i in (rest.find("/"), rest.find("?")) if i >= 0), default=len(rest))
    authority, tail = rest[:end], rest[end:]
    if "@" in authority:
        raise _error(
            "url_userinfo",
            f"the {what} carries userinfo (user or password before @ in the authority); "
            "credentials are never stored in a URL",
        )
    host = _split_host_port(authority, what)
    path, _mark, query = tail.partition("?")
    _check_parameter_names(query, "query parameter", what)
    _check_patterns((("host", host), ("path", path), ("query", query)), what)
    return url


def check_websocket_url(url: str) -> str:
    """A ``ws``/``wss`` endpoint URL without credentials (returned unchanged)."""
    return _check_hierarchical(url, ("ws", "wss"), "WebSocket URL")


def check_webrtc_url(url: str) -> str:
    """A WebRTC signalling URL (``http``/``https``) without credentials (returned unchanged)."""
    return _check_hierarchical(url, ("http", "https"), "WebRTC signalling URL")


# ----------------------------------------------------------------------------------------- sip


def check_sip_uri(uri: str) -> str:
    """A ``sip``/``sips`` URI without credentials (returned unchanged).

    Layout: ``sip:[user@]host[:port][;parameters][?headers]``. The user part is an address
    (``sip:1001@pbx``); a password in it is rejected.
    """
    what = "SIP URI"
    _check_characters(uri, what)
    rest = _check_scheme(uri, ("sip", "sips"), what)
    if "#" in rest:
        raise _error("url_fragment", "a SIP URI must not have a fragment (#...)")
    head, _mark, headers = rest.partition("?")
    user = ""
    if "@" in head:
        user, _at, head = head.partition("@")
        if not user or "@" in head:
            raise _error("sip_userinfo", "a SIP URI has one user part before a single @")
        if ":" in user or "%3a" in user.lower():
            raise _error(
                "sip_password",
                "the SIP URI carries a password in its userinfo (user:password@); "
                "credentials are never stored in a URI",
            )
    hostport, _semicolon, parameters = head.partition(";")
    host, colon, port = hostport.partition(":")
    if hostport.startswith("["):
        end = hostport.find("]")
        host, rest_port = hostport[: end + 1], hostport[end + 1 :]
        colon, port = rest_port[:1], rest_port[1:]
        if end < 0 or _IP_LITERAL.fullmatch(host) is None or (rest_port and colon != ":"):
            raise _error("url_host", "the SIP URI has a malformed IP literal host")
    elif not host or _SIP_HOST.fullmatch(host) is None:
        raise _error("url_host", "a SIP URI needs a host")
    if colon and _SIP_PORT.fullmatch(port) is None:
        raise _error("url_port", "the SIP URI port must be digits")
    _check_parameter_names(parameters, "URI parameter", what)
    _check_parameter_names(headers, "header", what)
    _check_patterns(
        (
            ("SIP user part", user),
            ("host", host),
            ("URI parameters", parameters),
            ("headers", headers),
        ),
        what,
    )
    return uri


# --------------------------------------------------------------------------- artifact URIs

_SCHEME = re.compile("[A-Za-z][A-Za-z0-9+.-]*")
_FILE_RULE = "file URI must be file:/// with an absolute path"


def _check_file_uri(rest: str, what: str) -> None:
    """``rest`` follows ``file:``. Only ``///`` and an absolute path, without a query, dot
    segments or an empty path (ADR 0007 Decision 6c, details confirmed by the owner)."""
    if not rest.startswith("///"):
        raise _error(
            "file_uri",
            f"{_FILE_RULE} (no host, localhost included; not the file:/path or file:path form)",
        )
    path = rest[2:]
    if "?" in path:
        raise _error("file_uri", f"{_FILE_RULE}: a file URI has no query")
    if path == "/":
        raise _error("file_uri", f"{_FILE_RULE}: the path is empty")
    if any(segment in (".", "..") for segment in path[1:].split("/")):
        raise _error("file_uri", f"{_FILE_RULE}: no . or .. segment")
    _check_patterns((("path", path),), what)


def check_artifact_uri(uri: str) -> str:
    """An ``Artifact.uri`` (returned unchanged): ADR 0007 Decisions 6, 6a, 6b and 6c.

    Absolute, ASCII, valid escapes; scheme ``http``, ``https``, ``s3``, ``gs`` (with ``//`` and a
    host or bucket, no userinfo, no credential parameter name, no credential pattern) or ``file``
    (``file:///`` and an absolute path); no fragment for any scheme. Loopback hosts are accepted
    here (Decision 6d: the export rules of P1-14 reject them in shared and benchmark exports).
    """
    what = "URI (Artifact.uri)"
    _check_characters(uri, what)
    scheme, separator, rest = uri.partition(":")
    if not separator or _SCHEME.fullmatch(scheme) is None:
        raise _error(
            "url_relative",
            "an artifact URI must be absolute, <scheme>:..., with the scheme http, https, s3, gs "
            "or file (a relative reference is not allowed)",
        )
    if scheme not in ARTIFACT_URI_SCHEMES:
        case = " and lower case" if scheme.lower() in ARTIFACT_URI_SCHEMES else ""
        raise _error(
            "url_scheme",
            f"scheme not allowed: an artifact URI scheme is one of http, https, s3, gs, file{case}",
        )
    if "#" in rest:
        raise _error(
            "url_fragment",
            "fragment not allowed: an artifact URI has no fragment (#...); a range of a recording "
            "is an EvidenceRef of kind artifact_range",
        )
    if scheme == "file":
        _check_file_uri(rest, what)
        return uri
    return _check_hierarchical(uri, (scheme,), what)


# ------------------------------------------------------------------------ price source URLs

PRICE_SOURCE_URL_PATTERN = f"^https?://[{_URI_CHARACTERS}]+$"
"""The scheme set and the character set of ``PriceProvenance.url`` (the rest is code-only)."""


def check_price_source_url(url: str) -> str:
    """A ``PriceProvenance.url`` (returned unchanged): ADR 0007 Decision 6e, ADR 0014 Decision 11.

    An absolute URI with the lowercase scheme ``http`` or ``https``, ``//`` and a host, ASCII
    only, no white space or control characters, valid escapes. Unlike endpoint and artifact URIs a
    fragment is allowed (price pages use anchors). Rejected: userinfo; a credential parameter name
    (Decision 6b: percent-decoded once, case-folded, ``-`` read as ``_``) in the query or in the
    fragment, both split at ``&`` and ``;``, where a piece without ``=`` is a name too (N66 (a),
    N70); a known credential pattern in the host, path, query or fragment, as written or
    percent-decoded once; and a loopback host by the definition of Decision 6d
    (``is_loopback_host_uri``, the function behind ``artifact.is_loopback_uri``), because a price
    source is a public page (N66 (b)). Private, link-local and unspecified addresses are accepted.
    Messages name the rule and the URL part (``userinfo``, ``query``, ``path``, ``fragment``,
    ``loopback host``) and never a part of the URL inside a model that hides its input.
    """
    what = "price source URL"
    _check_characters(url, what)
    rest = _check_scheme(url, ("http", "https"), what)
    if not rest.startswith("//"):
        raise _error("url_absolute", f"a {what} must be absolute: http(s)://host/...")
    rest, _hash, fragment = rest[2:].partition("#")
    if "#" in fragment:
        raise _error("url_fragment", f"the {what} fragment holds a second # (not an RFC 3986 URI)")
    end = min((i for i in (rest.find("/"), rest.find("?")) if i >= 0), default=len(rest))
    authority, tail = rest[:end], rest[end:]
    if "@" in authority:
        raise _error(
            "url_userinfo",
            f"the {what} carries userinfo (user or password before @ in the authority); "
            "credentials are never stored in a URL",
        )
    host = _split_host_port(authority, what)
    path, _mark, query = tail.partition("?")
    _check_parameter_names(query, "query parameter", what)
    _check_parameter_names(fragment, "fragment parameter", what)
    _check_patterns(
        (("host", host), ("path", path), ("query", query), ("fragment", fragment)), what
    )
    if is_loopback_host_uri(url):
        raise _error(
            "url_loopback_host",
            f"the {what} names a loopback host (ADR 0007 Decision 6d); a price source is a "
            "public page",
        )
    return url


# ------------------------------------------------------------------------------- loopback hosts

_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_DIGITS = {10: frozenset("0123456789"), 8: frozenset("01234567"), 16: frozenset("0123456789abcdef")}
_IPV6_LOOPBACK = ipaddress.IPv6Address("::1")
_IPV4_LOOPBACK = ipaddress.IPv4Network("127.0.0.0/8")


def _ipv4_number(part: str) -> int | None:
    """The WHATWG URL Standard "IPv4 number parser" (hex ``0x``, octal leading ``0``, decimal);
    ``None`` for failure."""
    if not part:
        return None
    radix = 10
    if part[:2] in ("0x", "0X"):
        part, radix = part[2:], 16
    elif len(part) > 1 and part[0] == "0":
        part, radix = part[1:], 8
    if not part:
        return 0
    if not set(part.lower()) <= _DIGITS[radix]:
        return None
    return int(part, radix)


def _ends_in_a_number(host: str) -> bool:
    """The WHATWG URL Standard "ends in a number checker"."""
    parts = host.split(".")
    if parts[-1] == "":
        if len(parts) == 1:
            return False
        parts.pop()
    last = parts[-1]
    if last and set(last) <= _DIGITS[10]:
        return True
    return _ipv4_number(last) is not None


def _whatwg_ipv4(host: str) -> int | None:
    """The WHATWG URL Standard "IPv4 parser": the address as an integer, or ``None``."""
    parts = host.split(".")
    if parts[-1] == "" and len(parts) > 1:
        parts.pop()
    if len(parts) > 4:
        return None
    numbers = [_ipv4_number(part) for part in parts]
    if any(number is None for number in numbers):
        return None
    values = [number for number in numbers if number is not None]
    if any(number > 255 for number in values[:-1]) or values[-1] >= 1 << (8 * (5 - len(values))):
        return None
    address = values[-1]
    for index, number in enumerate(values[:-1]):
        address += number << (8 * (3 - index))
    return address


def _is_loopback_host(host: str) -> bool:
    if host.startswith("["):
        if not host.endswith("]") or "%" in host:
            return False  # a zone identifier is not part of a URL host (WHATWG)
        try:
            address = ipaddress.IPv6Address(host[1:-1])
        except ValueError:
            return False
        mapped = address.ipv4_mapped
        return address == _IPV6_LOOPBACK or (mapped is not None and mapped in _IPV4_LOOPBACK)
    name = host.translate(_ASCII_LOWER)
    if name.endswith("."):
        name = name[:-1]  # one trailing dot, never more (D1)
    if not name or name.endswith("."):
        return False  # an empty label: not localhost, and not an IPv4 address after one dot
    if _ends_in_a_number(name):
        address_number = _whatwg_ipv4(name)
        return address_number is not None and address_number >> 24 == 127
    return name == "localhost" or (name.endswith(".localhost") and name != ".localhost")


def is_loopback_host_uri(uri: str) -> bool:
    """True iff ``uri`` is an ``http``/``https`` URI whose host is a loopback host (ADR 0007
    Decision 6d). Pure; never raises for a string. The host is read as written and
    percent-decoded once; a host that cannot be decoded is read as written only. Userinfo is not
    part of the host. For any other scheme (``s3``, ``gs``: the authority is a bucket; ``file``)
    the answer is false."""
    if not isinstance(uri, str):
        raise TypeError(f"a URI is a str, not {type(uri).__name__}")
    scheme, separator, rest = uri.partition(":")
    if not separator or scheme.translate(_ASCII_LOWER) not in ("http", "https"):
        return False
    if not rest.startswith("//"):
        return False
    rest = rest[2:]
    end = min(
        (i for i in (rest.find("/"), rest.find("?"), rest.find("#")) if i >= 0), default=len(rest)
    )
    host_port = rest[:end].rpartition("@")[2]
    if host_port.startswith("["):
        close = host_port.find("]")
        host = host_port[: close + 1] if close >= 0 else host_port
    else:
        host = host_port.partition(":")[0]
    candidates = [host]
    with contextlib.suppress(ValueError):  # undecodable: the written host only
        candidates.append(percent_decode_once(host))
    return any(_is_loopback_host(candidate) for candidate in candidates)


# ----------------------------------------------------------------------------- pydantic type


@dataclass(frozen=True)
class UrlRule:
    """``Annotated[str, UrlRule(check, pattern)]``: a strict string checked by ``check``.

    The JSON Schema is ``{"type": "string", "pattern": pattern}``. The check's messages never
    contain the value, and the error context holds none.
    """

    check: Callable[[str], str]
    pattern: str

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            self.check, core_schema.str_schema(strict=True)
        )

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {"type": "string", "pattern": self.pattern}
