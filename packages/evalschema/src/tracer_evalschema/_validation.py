# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Located errors for rules that span several fields.

A model validator that checks a rule across fields reports each violation at the field it is
about (``pass_rule``, ``unit``, ...), not at the model. It collects ``Problem`` values and calls
``raise_problems``, which raises one ``ValidationError``. Pydantic merges a ``ValidationError``
raised inside a validator into the outer error and prefixes the locations, so the locations stay
correct when the model is embedded (verified on pydantic 2.14.1).

Messages name the rule; they never contain the rejected value (the value is the error's
``input``, which models that hide their input leave out).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

__all__ = ["Problem", "raise_problems"]


@dataclass(frozen=True)
class Problem:
    """One violated rule: where (a location inside the model), what kind, and the message."""

    loc: tuple[str | int, ...]
    error_type: str
    message: str
    value: Any = None


def raise_problems(title: str, problems: Sequence[Problem]) -> None:
    """Raise one ``ValidationError`` holding every problem; return if there is none."""
    if not problems:
        return
    details = [
        InitErrorDetails(
            type=PydanticCustomError(problem.error_type, problem.message),
            loc=problem.loc,
            input=problem.value,
        )
        for problem in problems
    ]
    raise ValidationError.from_exception_data(title, details)
