# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Models that hide their input, and the context that keeps shared messages value-free.

ADR 0007 Decision 8. ``hide_input_in_errors=True`` removes the ``input_value`` part of a pydantic
error and nothing else. Decision 8 (c), as extended by the owner's decision on finding N42, also
forbids every shared validator to quote a rejected value in any field of a model that hides its
input: such an error names the field location and the rule only.

The shared validators (typed IDs, key patterns, closed sets, language tags, decimal strings,
instants, ...) are used on their own as well, where quoting the value is a useful hint (for
example ``DecimalString`` names the canonical spelling, ``test_decimal_canonical``). They read
``values_hidden()`` to choose: a model that hides its input sets the flag for the whole of its own
validation, embedded models included, so the outermost hiding model governs.

``HidingModel`` is the base of every model that hides its input. It sets
``hide_input_in_errors=True`` and gives each subclass a wrap validator that sets the flag. The
validator is attached to each subclass under its own name, after the class body, so it is the
subclass's outermost validator and also covers the subclass's own model validators.

This is a private module: the registry does not count its classes as schema models.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from pydantic import BaseModel, ConfigDict, ModelWrapValidatorHandler, model_validator

__all__ = ["HidingModel", "hidden_values", "values_hidden"]

_VALUES_HIDDEN: ContextVar[bool] = ContextVar("tracer_evalschema_values_hidden", default=False)


def values_hidden() -> bool:
    """True while a model that hides its input is being validated (messages must not quote)."""
    return _VALUES_HIDDEN.get()


@contextmanager
def hidden_values() -> Iterator[None]:
    """Run a block as if inside a model that hides its input (shared messages quote nothing)."""
    token = _VALUES_HIDDEN.set(True)
    try:
        yield
    finally:
        _VALUES_HIDDEN.reset(token)


def _validate_with_values_hidden(
    cls: type[BaseModel], data: Any, handler: ModelWrapValidatorHandler[Any]
) -> Any:
    with hidden_values():
        return handler(data)


def _hiding_validator() -> Any:
    wrapped: Any = classmethod(_validate_with_values_hidden)
    return model_validator(mode="wrap")(wrapped)


class HidingModel(BaseModel):
    """Base of the models that hide their input in validation errors (ADR 0007 Decision 8)."""

    model_config = ConfigDict(hide_input_in_errors=True)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # Runs while the class is created, before pydantic collects its decorators: the validator
        # is then the last one of the class's own namespace, which pydantic applies outermost.
        super().__init_subclass__(**kwargs)
        setattr(cls, f"_hide_values_in_messages_{cls.__name__}", _hiding_validator())
