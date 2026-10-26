# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The API key at run time (ADR 0024 rules 4 and 5): a redacting wrapper and the loader.

This is the only module that reads the process environment. Errors name the cause by a short
reason key; they never quote the key, a line of an env file or a file name.
"""

from __future__ import annotations

import os
import re
import stat
import warnings
from collections.abc import Mapping
from typing import Any, Final, NoReturn

from tracer_evalcore.stt import SttKeyMissingError
from tracer_evalschema.provider import ProviderConfigRef

__all__ = [
    "CONFIG_REF",
    "ENV_FILE_MAX_BYTES",
    "ENV_VAR_NAME",
    "PLACEHOLDER",
    "ApiKey",
    "EnvFileModeWarning",
    "load_api_key",
    "provider_config_ref",
]

ENV_VAR_NAME: Final = "SARVAM_API_KEY"
CONFIG_REF: Final = "env:SARVAM_API_KEY"
PLACEHOLDER: Final = "<redacted api key>"
ENV_FILE_MAX_BYTES: Final = 65_536

_LINE: Final = re.compile(rb"[ \t]*(?:export[ \t]+)?SARVAM_API_KEY[ \t]*=(.*)", re.DOTALL)
_BLANKS: Final = " \t\r\n"


class ApiKey:
    """A key that cannot be printed, formatted, pickled or copied by accident.

    ``reveal`` is the only reader; the Sarvam client uses it to build the one request header.
    """

    __slots__ = ("_value",)
    _value: str

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or value.strip() == "":
            raise SttKeyMissingError(reason="key_missing")
        if any(not 0x21 <= ord(char) <= 0x7E for char in value):
            raise SttKeyMissingError(reason="key_invalid")
        object.__setattr__(self, "_value", value)

    def reveal(self) -> str:
        return self._value

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("an ApiKey is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("an ApiKey is immutable")

    def __str__(self) -> str:
        return PLACEHOLDER

    def __repr__(self) -> str:
        return f"ApiKey({PLACEHOLDER})"

    def __format__(self, format_spec: str) -> str:
        return PLACEHOLDER

    def __reduce__(self) -> NoReturn:
        raise TypeError("an ApiKey cannot be pickled or copied")

    def __eq__(self, other: object) -> bool:
        return self is other

    def __hash__(self) -> int:
        return id(self)


def _unquote(raw: str) -> str:
    value = raw.strip(_BLANKS)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


class EnvFileModeWarning(UserWarning):
    """The env file can be read by other users. The message holds the mode and nothing else."""


def _read_file(path: str | os.PathLike[str]) -> tuple[bytes | None, str | None]:
    """``(data, None)`` for a regular file within ``ENV_FILE_MAX_BYTES``, else ``(None, reason)``.

    The file is opened non-blocking so that a FIFO cannot hold the command; ``fstat`` on the open
    descriptor decides, so the file cannot change kind between the check and the read.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None, "env_file_unreadable"
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return None, "env_file_not_regular"
        if info.st_mode & 0o077:
            warnings.warn(
                "the env file can be read by other users "
                f"(mode {oct(stat.S_IMODE(info.st_mode))}); run chmod 600 on it",
                EnvFileModeWarning,
                stacklevel=3,
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(ENV_FILE_MAX_BYTES + 1)
    except OSError:
        return None, "env_file_unreadable"
    finally:
        os.close(descriptor)
    if len(data) > ENV_FILE_MAX_BYTES:
        return None, "env_file_too_large"
    return data, None


def _file_value(data: bytes) -> tuple[str, bytes | None]:
    """``(state, value)``: ``missing``, ``ambiguous`` or ``found``; no other line is decoded."""
    found: list[bytes] = []
    for line in data.splitlines():
        match = _LINE.fullmatch(line)
        if match is not None:
            found.append(match.group(1))
    if not found:
        return "missing", None
    if len(found) > 1:
        return "ambiguous", None
    return "found", found[0]


def _decode(value: bytes) -> str | None:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def load_api_key(
    *,
    environ: Mapping[str, str] | None = None,
    env_file: str | os.PathLike[str] | None = None,
) -> ApiKey:
    """The key from ``SARVAM_API_KEY``.

    With ``env_file`` the file is the only source and the environment is not consulted. It must
    be a regular file of at most ``ENV_FILE_MAX_BYTES``; a mode with a group or other bit warns
    (``EnvFileModeWarning``) and is not a refusal. Without
    it the variable is read from ``environ`` (default ``os.environ``). A file that defines the
    variable twice is refused. Nothing is exported and nothing else is read from the file.
    """
    reason: str | None = None
    raw: str | None = None
    if env_file is not None:
        data, refusal = _read_file(env_file)
        if data is None:
            reason = refusal
        else:
            state, value = _file_value(data)
            if state == "ambiguous":
                reason = "key_ambiguous"
            elif value is not None:
                raw = _decode(value)
                if raw is None:
                    reason = "key_invalid"
    else:
        raw = (os.environ if environ is None else environ).get(ENV_VAR_NAME)
    if reason is None:
        text = "" if raw is None else _unquote(raw)
        if text:
            return ApiKey(text)
        reason = "key_missing"
    raise SttKeyMissingError(reason=reason)


def provider_config_ref(model: str) -> ProviderConfigRef:
    """What the Project records: the name of the key, never its value, never a file path."""
    return ProviderConfigRef.model_validate(
        {"provider": "sarvam", "model": model, "config_ref": CONFIG_REF}
    )
