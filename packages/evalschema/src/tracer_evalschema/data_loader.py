# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""The one place that reads the package data files (``tracer_evalschema/data``).

Files are read through ``importlib.resources``, so they are found in a wheel as well as in a
source checkout. Each file is read at most once per process. Callers that check a data file's
content raise ``errors.DataFileError``; this module only reports unreadable JSON.
"""

from __future__ import annotations

import functools
import json
from importlib import resources
from pathlib import Path
from typing import Any

from tracer_evalschema.errors import DataFileError


def read_json_file(path: Path) -> Any:
    """Parse a JSON data file, raising ``DataFileError`` if it cannot be read or parsed."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise DataFileError(f"cannot read data file {path}: {exc}") from exc


@functools.cache
def package_data(name: str) -> Any:
    """Parse ``tracer_evalschema/data/<name>`` (cached: callers must not mutate the result)."""
    resource = resources.files("tracer_evalschema").joinpath("data", name)
    try:
        return json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise DataFileError(f"cannot read package data file {name}: {exc}") from exc
