# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The target allowlist (ADR 0026 rule 16): the operator's explicit statement of what may be called.

One entry per line: a ``ws://``, ``wss://``, ``http://`` or ``https://`` URL, or an E.164 number,
then optional ``name=value`` fields (``bot_holds_telephony_credentials``, ``auth_token_env``,
``origin``). ``#`` starts a comment. The file must be a regular file with no group or other
permission bit and is never followed through a link. A target is compared with an entry exactly,
after one normalisation (``normalise_target``). This module only reads and compares: whether an
entry may be used with a dialect is decided by the connection labels (``tracer_evalsim.ws``).
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tracer_evalsim.errors import EvalsimError, TargetRefusedError
from tracer_evalsim.ws import parse_target_url

__all__ = [
    "MAX_ALLOWLIST_BYTES",
    "AllowlistEntry",
    "AllowlistError",
    "find_entry",
    "normalise_target",
    "parse_allowlist",
    "read_allowlist",
]

MAX_ALLOWLIST_BYTES: Final = 65_536
_FIELDS: Final = ("bot_holds_telephony_credentials", "auth_token_env", "origin")
_E164: Final = re.compile(r"\+[1-9][0-9]{6,14}")
_ENV_NAME: Final = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
_ORIGIN: Final = re.compile(r"(?:https?|wss?)://[a-z0-9.-]+(?::[0-9]{1,5})?")


class AllowlistError(EvalsimError, ValueError):
    """The allowlist file or an entry was refused. ``code`` is the reason and the message."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AllowlistEntry:
    target: str  # the normalised URL, or the E.164 number
    bot_holds_telephony_credentials: bool | None
    auth_token_env: str | None
    origin: str | None


def normalise_target(target: str) -> str:
    """Lower-case scheme and host, the default port written out, the path byte for byte."""
    if _E164.fullmatch(target):
        return target
    try:
        parsed = parse_target_url(target, schemes=("ws", "wss", "http", "https"))
    except TargetRefusedError as error:
        raise AllowlistError(error.code) from None
    host = f"[{parsed.host}]" if ":" in parsed.host else parsed.host
    return f"{parsed.scheme}://{host}:{parsed.port}{parsed.path}"


def parse_allowlist(text: str) -> tuple[AllowlistEntry, ...]:
    entries: list[AllowlistEntry] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        head, *pairs = line.split()
        fields: dict[str, str] = {}
        for pair in pairs:
            name, equals, value = pair.partition("=")
            if not equals or name not in _FIELDS or name in fields or not value:
                raise AllowlistError("allowlist_field_invalid")
            fields[name] = value
        credentials: bool | None = None
        if "bot_holds_telephony_credentials" in fields:
            word = fields["bot_holds_telephony_credentials"]
            if word not in ("true", "false"):
                raise AllowlistError("allowlist_field_invalid")
            credentials = word == "true"
        env = fields.get("auth_token_env")
        if env is not None and _ENV_NAME.fullmatch(env) is None:
            raise AllowlistError("allowlist_field_invalid")
        origin = fields.get("origin")
        if origin is not None and _ORIGIN.fullmatch(origin) is None:
            raise AllowlistError("allowlist_field_invalid")
        entries.append(AllowlistEntry(normalise_target(head), credentials, env, origin))
    return tuple(entries)


def read_allowlist(path: str | os.PathLike[str]) -> tuple[AllowlistEntry, ...]:
    """Read the file under the rules of rule 16; every refusal is an ``AllowlistError``."""
    location = Path(path)
    try:
        info = os.lstat(location)
    except OSError:
        raise AllowlistError("allowlist_unreadable") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise AllowlistError("allowlist_not_a_regular_file")
    if info.st_mode & 0o077:
        raise AllowlistError("allowlist_mode_too_open")
    if info.st_size > MAX_ALLOWLIST_BYTES:
        raise AllowlistError("allowlist_too_large")
    try:
        with open(location, "rb") as handle:
            data = handle.read(MAX_ALLOWLIST_BYTES + 1)
        return parse_allowlist(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError):
        raise AllowlistError("allowlist_unreadable") from None


def find_entry(entries: tuple[AllowlistEntry, ...], target: str) -> AllowlistEntry | None:
    wanted = normalise_target(target)
    found = [entry for entry in entries if entry.target == wanted]
    if len(found) > 1:
        raise AllowlistError("allowlist_entry_repeated")
    return found[0] if found else None
