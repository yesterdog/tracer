# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-05: the strict loader (ADR 0008 Decision 6; adversarial A-06, A-08, A-10).

Assumed public API (docs/phase1/api-surface.md) in ``tracer_evalschema.loader``:
``loads_strict(data, *, max_depth=...) -> Any`` and ``load_model(model, data, *, max_depth=...)``;
typed errors ``DuplicateKeyError`` and ``DepthLimitExceeded`` in ``tracer_evalschema.errors``.
"""

from __future__ import annotations

import json
import sys

import pytest

from ._support import api, entity_doc, widget_classes


def test_strict_loader_duplicate_keys() -> None:
    loader = api("loader")
    errors = api("errors")
    assert loader.loads_strict('{"a": 1, "b": [1, 2, {"c": null}]}') == {
        "a": 1,
        "b": [1, 2, {"c": None}],
    }
    assert loader.loads_strict(b'{"a": 1}') == {"a": 1}, "bytes are accepted"
    duplicates = [
        '{"a": 1, "a": 2}',
        '{"a": 1, "a": 1}',
        '{"x": {"k": 1, "k": 2}}',
        '{"x": [{"k": 1, "k": 2}]}',
        '{"a": 1, "\\u0061": 2}',  # the same key once the escape is decoded
        '{"\\u00e9": 1, "\u00e9": 2}',
        '{"a": {"b": 1}, "a": {"b": 1}}',
    ]
    for text in duplicates:
        with pytest.raises(errors.DuplicateKeyError) as caught:
            loader.loads_strict(text)
        assert issubclass(type(caught.value), errors.TracerSchemaError)
    # the key is named in the message, and case differences are not duplicates
    with pytest.raises(errors.DuplicateKeyError, match="dup"):
        loader.loads_strict('{"dup": 1, "dup": 2}')
    assert loader.loads_strict('{"a": 1, "A": 2}') == {"a": 1, "A": 2}
    # the plain json module silently keeps the last value: that is what the loader prevents
    assert json.loads('{"a": 1, "a": 2}') == {"a": 2}


def test_strict_loader_models_reject_duplicate_keys() -> None:
    """Proposed name. The model entry point uses the same strict parsing, not pydantic's."""
    loader = api("loader")
    errors = api("errors")
    widget = widget_classes().Widget
    doc = entity_doc()
    text = json.dumps(doc)
    assert loader.load_model(widget, text).name == "widget"
    duplicated = text[:-1] + ', "name": "second"}'
    with pytest.raises(errors.DuplicateKeyError):
        loader.load_model(widget, duplicated)
    assert widget.model_validate_json(duplicated).name == "second", "pydantic alone takes the last"
    with pytest.raises(errors.SchemaVersionTooNew):
        loader.load_model(widget, json.dumps({**doc, "schema_version": "99.0.0"}))


def test_strict_loader_depth_10k_no_recursionerror() -> None:
    loader = api("loader")
    errors = api("errors")
    assert not issubclass(errors.DepthLimitExceeded, RecursionError)
    assert issubclass(errors.DepthLimitExceeded, errors.TracerSchemaError)
    depth = 10_000
    assert depth > sys.getrecursionlimit(), "the document is deeper than the interpreter allows"
    documents = {
        "arrays": "[" * depth + "]" * depth,
        "objects": '{"a":' * depth + "1" + "}" * depth,
        "mixed": '[{"a":' * (depth // 2) + "1" + "}]" * (depth // 2),
        "unterminated": "[" * depth,
    }
    for name, text in documents.items():
        with pytest.raises(errors.DepthLimitExceeded):  # a RecursionError would fail the test
            loader.loads_strict(text)
        assert name
    # model entry point as well
    widget = widget_classes().Widget
    with pytest.raises(errors.DepthLimitExceeded):
        loader.load_model(widget, "[" * depth + "]" * depth)
    # ordinary nesting is fine, and the limit is configurable
    shallow = "[" * 20 + "1" + "]" * 20
    assert loader.loads_strict(shallow)
    with pytest.raises(errors.DepthLimitExceeded):
        loader.loads_strict("[" * 6 + "]" * 6, max_depth=5)
    assert loader.loads_strict("[[[1]]]", max_depth=5) == [[[1]]]
    assert loader.loads_strict("1", max_depth=1) == 1


def test_strict_loader_malformed_json_is_a_plain_error() -> None:
    """Proposed name. Syntax errors are ordinary ValueErrors, not depth or duplicate errors."""
    loader = api("loader")
    errors = api("errors")
    for text in ("", "{", '{"a":}', "[1,]", "nope", '{"a": 1} trailing'):
        with pytest.raises(ValueError, match=r".") as caught:
            loader.loads_strict(text)
        assert not isinstance(caught.value, (errors.DuplicateKeyError, errors.DepthLimitExceeded))
