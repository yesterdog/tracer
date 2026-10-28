# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Pinned hosts and key binding for the judge (ADR 0030 rules 14 and 15; ADR 0024 amendment A12;
BL-142).

The owner writes the judge allowlist: one exact ``https://host[:port]/path`` per line with the
profile and the NAME of the key variable. The judge configuration may name only an entry. Before
any key is read the entry must match (URL and profile), the profile must be allowed for a real
run, and every DNS answer of a public entry must be public; the key is then read by its variable
name and bound to that entry. A literal loopback address is the one place where plain ``http`` is
allowed. The file is a private regular file read without following a link. A rebinding race between
the resolution check and the connection is not claimed closed (BL-326).
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tracer_evalcore.errors import InputRefusedError
from tracer_evalproviders import judgeprofiles
from tracer_evalproviders.secrets import ApiKey, load_named_key
from tracer_evalschema.credentials import contains_credential_pattern

__all__ = [
    "KEY_ENV_PATTERN",
    "MAX_JUDGE_ALLOWLIST_BYTES",
    "BoundEndpoint",
    "JudgeAllowlistError",
    "JudgeEndpoint",
    "Resolver",
    "bind_checked",
    "bind_endpoint",
    "check_endpoint",
    "find_judge_endpoint",
    "parse_judge_allowlist",
    "read_judge_allowlist",
    "system_resolver",
]

MAX_JUDGE_ALLOWLIST_BYTES: Final = 65_536
KEY_ENV_PATTERN: Final = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
Resolver = Callable[[str, int], Sequence[str]]
_FIELDS: Final = ("profile", "key_env")
_NAME_HOST: Final = re.compile(r"[A-Za-z0-9.-]{1,253}")
_V6_HOST: Final = re.compile(r"\[([0-9A-Fa-f:.]{2,45})\]")
_PATH: Final = re.compile(r"/[A-Za-z0-9._~/-]{0,255}")
_DEFAULT_PORT: Final = {"http": 80, "https": 443}


class JudgeAllowlistError(InputRefusedError):
    """The allowlist, an entry or a lookup was refused. ``code`` is the reason and the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class JudgeEndpoint:
    url: str
    scheme: str
    host: str
    port: int
    path: str
    profile: str
    key_env: str | None


@dataclass(frozen=True)
class BoundEndpoint:
    """An allowlist entry that matched, with its profile and its key (read last, by name)."""

    endpoint: JudgeEndpoint
    profile: judgeprofiles.JudgeProfile
    api_key: ApiKey | None


def system_resolver(host: str, port: int) -> list[str]:
    """The addresses the system resolver gives a name (``OSError`` if it cannot)."""
    return [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_loopback(host: str) -> bool:
    address = _literal(host)
    return address is not None and address.is_loopback


def _parse_url(url: str) -> tuple[str, str, str, int, str]:
    """``(canonical url, scheme, host, port, path)`` or a refusal."""
    if type(url) is not str or "?" in url or "#" in url or "\\" in url:
        raise JudgeAllowlistError("allowlist_url_invalid")
    scheme, sep, rest = url.partition("://")
    scheme = scheme.lower()
    if not sep or scheme not in ("http", "https"):
        raise JudgeAllowlistError("allowlist_url_invalid")
    authority, slash, tail = rest.partition("/")
    path = slash + tail
    if "@" in authority or not authority or not path:
        raise JudgeAllowlistError("allowlist_url_invalid")
    port_text: str | None = None
    bracket = _V6_HOST.match(authority)
    if bracket is not None:
        host = bracket.group(1).lower()
        after = authority[bracket.end() :]
        if after:
            if not after.startswith(":"):
                raise JudgeAllowlistError("allowlist_url_invalid")
            port_text = after[1:]
        if _literal(host) is None or ":" not in host:
            raise JudgeAllowlistError("allowlist_url_invalid")
    else:
        name, colon, port_part = authority.partition(":")
        if _NAME_HOST.fullmatch(name) is None:
            raise JudgeAllowlistError("allowlist_url_invalid")
        host = name.lower()
        if colon:
            port_text = port_part
    port = _DEFAULT_PORT[scheme]
    if port_text is not None:
        if not port_text.isascii() or not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            raise JudgeAllowlistError("allowlist_url_invalid")
        port = int(port_text)
    if _PATH.fullmatch(path) is None:
        raise JudgeAllowlistError("allowlist_url_invalid")
    segments = path.split("/")[1:]
    if any(s in (".", "..") for s in segments) or any(s == "" for s in segments[:-1]):
        raise JudgeAllowlistError("allowlist_url_invalid")
    if scheme == "http" and not _is_loopback(host):
        raise JudgeAllowlistError("allowlist_scheme_not_allowed")
    shown = f"[{host}]" if ":" in host else host
    suffix = "" if port == _DEFAULT_PORT[scheme] else f":{port}"
    return f"{scheme}://{shown}{suffix}{path}", scheme, host, port, path


def _strip_comment(raw: str) -> str:
    """A ``#`` starts a comment at the start of the line or after white space only."""
    return re.split(r"(?:^|(?<=\s))#", raw, maxsplit=1)[0]


def parse_judge_allowlist(text: str) -> tuple[JudgeEndpoint, ...]:
    """The entries of the file text; the first fault refuses the whole file."""
    entries: list[JudgeEndpoint] = []
    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        head, *pairs = line.split()
        fields: dict[str, str] = {}
        for pair in pairs:
            name, equals, value = pair.partition("=")
            if not equals or name not in _FIELDS or name in fields or not value:
                raise JudgeAllowlistError("allowlist_field_invalid")
            fields[name] = value
        profile = fields.get("profile")
        if profile is None:
            raise JudgeAllowlistError("allowlist_field_missing")
        if profile not in judgeprofiles.PROFILE_NAMES:
            raise JudgeAllowlistError("allowlist_profile_unknown")
        key_env = fields.get("key_env")
        loopback_profile = profile == "local_loopback/1"
        if key_env is None and not loopback_profile:
            raise JudgeAllowlistError("allowlist_field_missing")
        url, scheme, host, port, path = _parse_url(head)
        if loopback_profile and not _is_loopback(host):
            raise JudgeAllowlistError("allowlist_profile_host_mismatch")
        if key_env is not None:
            if loopback_profile:
                raise JudgeAllowlistError("allowlist_key_env_forbidden")
            if KEY_ENV_PATTERN.fullmatch(key_env) is None or contains_credential_pattern(key_env):
                raise JudgeAllowlistError("allowlist_key_env_invalid")
        entries.append(JudgeEndpoint(url, scheme, host, port, path, profile, key_env))
    if len({e.url for e in entries}) != len(entries):
        raise JudgeAllowlistError("allowlist_entry_repeated")
    hosts_of: dict[str, set[tuple[str, int]]] = {}
    for entry in entries:
        if entry.key_env is not None:
            hosts_of.setdefault(entry.key_env, set()).add((entry.host, entry.port))
    if any(len(hosts) > 1 for hosts in hosts_of.values()):
        raise JudgeAllowlistError("allowlist_key_env_shared")
    return tuple(entries)


def _read_all(descriptor: int, limit: int) -> bytes:
    """Up to ``limit`` bytes of an open file; ``os.read`` may return fewer than are there."""
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_judge_allowlist(path: str | os.PathLike[str]) -> tuple[JudgeEndpoint, ...]:
    """Read the file under the rules of rule 14: a private regular file, no link of any kind."""
    location = Path(path)
    try:
        info = os.lstat(location)
    except OSError:
        raise JudgeAllowlistError("allowlist_unreadable") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise JudgeAllowlistError("allowlist_not_a_regular_file")
    if info.st_mode & 0o077:
        raise JudgeAllowlistError("allowlist_mode_too_open")
    if info.st_size > MAX_JUDGE_ALLOWLIST_BYTES:
        raise JudgeAllowlistError("allowlist_too_large")
    try:
        descriptor = os.open(location, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_ino, opened.st_dev) != (info.st_ino, info.st_dev):
                raise JudgeAllowlistError("allowlist_not_a_regular_file")
            data = _read_all(descriptor, MAX_JUDGE_ALLOWLIST_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(data) > MAX_JUDGE_ALLOWLIST_BYTES:
            raise JudgeAllowlistError("allowlist_too_large")
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise JudgeAllowlistError("allowlist_unreadable") from None
    return parse_judge_allowlist(text)


def find_judge_endpoint(entries: Sequence[JudgeEndpoint], url: str, profile: str) -> JudgeEndpoint:
    """The entry for ``url`` with ``profile``: ``host_not_allowed`` if no entry has that URL (a URL
    that does not normalise cannot have one), ``profile_mismatch`` if the entry has another
    profile."""
    try:
        wanted = _parse_url(url)[0]
    except JudgeAllowlistError:
        raise JudgeAllowlistError("host_not_allowed") from None
    found = [entry for entry in entries if entry.url == wanted]
    if not found:
        raise JudgeAllowlistError("host_not_allowed")
    if found[0].profile != profile:
        raise JudgeAllowlistError("profile_mismatch")
    return found[0]


def _public(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    return parsed.is_global and not parsed.is_multicast


def _check_resolution(endpoint: JudgeEndpoint, resolver: Resolver) -> None:
    """Rule 14: the addresses of a public entry are public; a literal loopback entry is exempt."""
    if _is_loopback(endpoint.host):
        return
    if _literal(endpoint.host) is not None:
        answers: Sequence[str] = [endpoint.host]
    else:
        try:
            answers = resolver(endpoint.host, endpoint.port)
        except OSError:
            raise JudgeAllowlistError("dns_failed") from None
    if not answers:
        raise JudgeAllowlistError("dns_no_answer")
    if not all(_public(answer) for answer in answers):
        raise JudgeAllowlistError("dns_answer_not_public")


def check_endpoint(
    entries: Sequence[JudgeEndpoint],
    *,
    url: str,
    profile: str,
    resolver: Resolver | None = None,
    first_request_gate: bool = False,
) -> JudgeEndpoint:
    """The first three steps of ``bind_endpoint`` (the entry, its profile, the DNS answers) and
    never the key: the check of step 2 of the command, before any key is read."""
    entry = find_judge_endpoint(entries, url, profile)
    chosen = judgeprofiles.get_profile(entry.profile)
    judgeprofiles.check_real_run(chosen, first_request_gate=first_request_gate)
    _check_resolution(entry, system_resolver if resolver is None else resolver)
    return entry


def bind_checked(
    entry: JudgeEndpoint,
    *,
    environ: Mapping[str, str] | None = None,
    env_file: str | os.PathLike[str] | None = None,
) -> BoundEndpoint:
    """Read the key of a checked entry by its variable name (step 6 of the command)."""
    key = None
    if entry.key_env is not None:
        key = load_named_key(entry.key_env, environ=environ, env_file=env_file)
    return BoundEndpoint(entry, judgeprofiles.get_profile(entry.profile), key)


def bind_endpoint(
    entries: Sequence[JudgeEndpoint],
    *,
    url: str,
    profile: str,
    environ: Mapping[str, str] | None = None,
    env_file: str | os.PathLike[str] | None = None,
    resolver: Resolver | None = None,
    first_request_gate: bool = False,
) -> BoundEndpoint:
    """Match the entry, allow its profile, check its DNS answers, then read its key by name.

    Each step refuses before the next: a refusal leaves the environment, the env file and (before
    the resolution step) the resolver untouched."""
    entry = check_endpoint(
        entries, url=url, profile=profile, resolver=resolver, first_request_gate=first_request_gate
    )
    return bind_checked(entry, environ=environ, env_file=env_file)
