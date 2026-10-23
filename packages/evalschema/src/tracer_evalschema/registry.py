# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Walks over every model of the package, for the contract tests and the schema export.

A model belongs to the package when it is a module-level class of a public ``tracer_evalschema``
module. Private modules (``_hiding``, ``_constraints``, ...) hold implementation bases and helpers,
never schema models, and are not walked. Models built at run time
(``primitives.make_money_model()``) and classes defined elsewhere (test doubles) are not included.
"""

from __future__ import annotations

import importlib
import pkgutil

from pydantic import BaseModel

import tracer_evalschema
from tracer_evalschema.base import EntityBase
from tracer_evalschema.ids import ID_PREFIXES

__all__ = ["entity_classes", "model_classes", "value_object_classes"]


def model_classes() -> list[type[BaseModel]]:
    """Every pydantic model defined at module level in a public module of the package, in a stable
    order."""
    found: list[type[BaseModel]] = []
    for info in sorted(pkgutil.iter_modules(tracer_evalschema.__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"tracer_evalschema.{info.name}")
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__ == module.__name__
                and value not in found
            ):
                found.append(value)
    return found


def entity_classes() -> dict[str, type[EntityBase]]:
    """Registered prefix -> entity class (classes that set a registered ``id_prefix``)."""
    entities: dict[str, type[EntityBase]] = {}
    for model in model_classes():
        if not issubclass(model, EntityBase):
            continue
        prefix = model.__dict__.get("id_prefix")
        if not isinstance(prefix, str) or prefix not in ID_PREFIXES:
            continue
        if prefix in entities:
            raise ValueError(
                f"prefix {prefix!r} is claimed by {entities[prefix].__name__} and {model.__name__}"
            )
        entities[prefix] = model
    return entities


def value_object_classes() -> dict[str, type[BaseModel]]:
    """Class name -> class for every model that is not an entity."""
    return {model.__name__: model for model in model_classes() if not issubclass(model, EntityBase)}
