# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P3-18: metric definition templates, scorer registry, rubric file, binding parameters.

Contract: docs/phase3/api-surface-batch-d.md sections 2 to 6. The numbers
``TEST_ONLY_FLAG_MIN_CONFIDENCE`` and ``TEST_ONLY_REVIEW_BUDGET`` are test values, not defaults.
"""

from __future__ import annotations

import ast
import inspect
import json
from decimal import Decimal
from typing import Any

import pytest

from evalcore_batchd_support import (  # type: ignore[import-not-found,unused-ignore]
    ABHI,
    BASE_EXPORT,
    COUNT,
    CREATED_AT,
    EXPORT_LINES,
    KAAM,
    KEYS,
    MER,
    NORMALISER,
    ROOT,
    SHARE,
    SRC,
    STT_PHRASES,
    STT_TEXTS,
    TEST_ONLY_FLAG_MIN_CONFIDENCE,
    TEST_ONLY_REVIEW_BUDGET,
    ChunkScript,
    FakeEngine,
    configuration,
    export_text,
    flags_of,
    mod,
    results_by_key,
    rubric_doc,
    rubric_text,
    run_pipeline,
    scripts,
    sentence,
    spec,
)
from tracer_evalschema.bundle import check_run_bundle
from tracer_evalschema.metrics import PerConversationAggregation

#: The reasons of the rubric file refusals, in the order the parser checks them.
FILE_CODES = (
    "rubric_not_json",
    "rubric_shape",
    "rubric_format_unsupported",
    "rubric_parameter_invalid",
    "rubric_metric_unknown",
    "rubric_metric_repeated",
    "rubric_pass_rule_invalid",
    "rubric_required_without_pass_rule",
    "rubric_budget_missing",
)


# ------------------------------------------------------------------- the three templates


def test_shipped_definitions_have_no_pass_rule() -> None:
    definitions = mod("definitions")
    shipped = definitions.shipped_definitions(created_at=CREATED_AT)
    assert [d.key for d in shipped] == list(KEYS)
    for definition in shipped:
        assert definition.pass_rule is None
        assert definition.version == 1
    assert tuple(definitions.SHIPPED_KEYS) == KEYS
    # a template is built fresh: two calls never share an id
    again = definitions.shipped_definitions(created_at=CREATED_AT)
    assert {d.id for d in shipped}.isdisjoint({d.id for d in again})
    with pytest.raises(ValueError, match="shipped"):
        definitions.shipped_definition("some_other_metric", created_at=CREATED_AT)
    # in a configuration the owner's budget is the only pass rule of the three
    snapshot = configuration().snapshot
    rules = {d.key: d.pass_rule for d in snapshot.metric_definitions if d.key in KEYS}
    assert rules[MER] is None
    assert rules[COUNT] is None
    assert rules[SHARE] is not None


def test_agreement_unit_is_ratio_never_wer_or_cer() -> None:
    definitions = mod("definitions")
    units = {d.key: d.unit for d in definitions.shipped_definitions(created_at=CREATED_AT)}
    assert units == {MER: "ratio", COUNT: "count", SHARE: "ratio"}
    assert "wer" not in units.values()
    assert "cer" not in units.values()
    for name in ("definitions", "scoring", "pipeline", "flagsdoc", "rubricfile", "registry"):
        tree = ast.parse((SRC / f"{name}.py").read_text("utf-8"))
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert not literals & {"wer", "cer", "WER", "CER"}, name


def test_definitions_requires_transcript_and_agent_transcript() -> None:
    definitions = mod("definitions")
    for definition in definitions.shipped_definitions(created_at=CREATED_AT):
        assert definition.kind == "deterministic"
        assert definition.value_type == "numeric"
        assert definition.requires == ["transcript", "agent_transcript"]
        assert definition.languages == ["mr"]
        assert definition.script_sensitive is False
        assert isinstance(definition.aggregation, PerConversationAggregation)
        assert definition.implementation_ref == f"builtin:{definition.key}"
        assert definition.scale is None
        assert definition.prompt_template is None
        assert definition.judge_model_ref is None
        assert [(p.name, p.value_type, p.default) for p in definition.parameters] == [
            ("flag_min_confidence", "number", None),
            ("normaliser", "string", None),
        ]
    assert definitions.PARAMETER_NAMES == ("flag_min_confidence", "normaliser")


def test_unknown_scorer_refuses_before_run(tmp_path: Any) -> None:
    registry = mod("registry")
    definitions = mod("definitions")
    pipeline = mod("pipeline")
    default = registry.DEFAULT_REGISTRY
    assert set(default.refs()) == {f"builtin:{key}" for key in KEYS}
    with pytest.raises(registry.UnknownScorerError):
        default.get("builtin:not_a_scorer")
    with pytest.raises(registry.UnknownScorerError):
        default.get("other:" + MER)
    assert issubclass(registry.UnknownScorerError, ValueError)
    # a registry without the share scorer: refused before anything is built or sent
    reduced = registry.ScorerRegistry(
        e for e in (default.get(f"builtin:{k}") for k in (MER, COUNT))
    )
    with pytest.raises(registry.UnknownScorerError):
        registry.check_runnable(configuration().snapshot.metric_definitions, reduced)
    with pytest.raises(registry.UnknownScorerError):
        definitions.build_configuration(
            spec(), configured_language="mr", created_at=CREATED_AT, registry=reduced
        )
    engine = FakeEngine()
    config = configuration()
    with pytest.raises(registry.UnknownScorerError):
        run_pipeline(tmp_path, engine=engine, config=config, registry=reduced)
    assert engine.calls == []
    assert pipeline.EXIT_SCORED == 0


def test_scorer_registry_entries_carry_the_distribution_version() -> None:
    registry = mod("registry")
    import tracer_evalcore

    for key in KEYS:
        entry = registry.DEFAULT_REGISTRY.get(f"builtin:{key}")
        assert entry.implementation_ref == f"builtin:{key}"
        assert entry.version == tracer_evalcore.__version__
        info = registry.scorer_info(entry)
        assert (info.implementation_ref, info.version) == (f"builtin:{key}", entry.version)
    assert "builtin:" + MER in registry.DEFAULT_REGISTRY
    assert "builtin:nothing" not in registry.DEFAULT_REGISTRY


# ------------------------------------------------------------------- the rubric file


def test_rubric_file_without_required_pass_rule_refused() -> None:
    rubric = mod("rubricfile")

    def expect(doc: Any, code: str) -> None:
        text = doc if isinstance(doc, str) else json.dumps(doc)
        with pytest.raises(rubric.RubricFileError) as caught:
            rubric.parse_rubric_file(text)
        assert caught.value.code == code, (code, caught.value.code)
        assert code in FILE_CODES
        assert isinstance(caught.value, ValueError)

    # the share item required but with no pass rule
    doc = rubric_doc()
    del doc["items"][0]["pass_rule"]
    expect(doc, "rubric_required_without_pass_rule")
    # a required item of another metric has no pass rule either
    doc = rubric_doc()
    doc["items"].append({"metric": MER, "required": True})
    doc["items"].pop(1)
    expect(doc, "rubric_required_without_pass_rule")
    # no item at all, and no share item: no review budget
    doc = rubric_doc()
    doc["items"] = []
    expect(doc, "rubric_budget_missing")
    doc = rubric_doc(optional=(MER, COUNT))
    doc["items"] = doc["items"][1:]
    expect(doc, "rubric_budget_missing")
    # the share is listed but is not required: the budget is not enforced as a required item
    doc = rubric_doc()
    doc["items"][0]["required"] = False
    expect(doc, "rubric_budget_missing")


@pytest.mark.parametrize(
    ("make", "code"),
    [
        (lambda: "", "rubric_not_json"),
        (lambda: "{", "rubric_not_json"),
        (lambda: '{"format": "tracer.rubric/1", "format": "tracer.rubric/1"}', "rubric_not_json"),
        (lambda: json.dumps(rubric_doc()).replace("0.3", "NaN"), "rubric_not_json"),
        (lambda: "[" * 5000 + "]" * 5000, "rubric_not_json"),
        (lambda: "\ufeff" + json.dumps(rubric_doc()), "rubric_not_json"),
        (lambda: json.dumps([rubric_doc()]), "rubric_shape"),
        (lambda: json.dumps("tracer.rubric/1"), "rubric_shape"),
        (lambda: json.dumps({**rubric_doc(), "extra": 1}), "rubric_shape"),
        (
            lambda: json.dumps({k: v for k, v in rubric_doc().items() if k != "items"}),
            "rubric_shape",
        ),
        (
            lambda: json.dumps({k: v for k, v in rubric_doc().items() if k != "format"}),
            "rubric_shape",
        ),
        (
            lambda: json.dumps({**rubric_doc(), "parameters": {"normaliser": NORMALISER}}),
            "rubric_shape",
        ),
        (
            lambda: json.dumps(
                {**rubric_doc(), "parameters": {"flag_min_confidence": 0.3, "normaliser": 5}}
            ),
            "rubric_shape",
        ),
        (lambda: json.dumps(rubric_doc(threshold="0.3")), "rubric_shape"),
        (lambda: json.dumps(rubric_doc(threshold=True)), "rubric_shape"),
        (lambda: json.dumps(rubric_doc(threshold=None)), "rubric_shape"),
        (lambda: json.dumps({**rubric_doc(), "items": "none"}), "rubric_shape"),
        (lambda: json.dumps({**rubric_doc(), "items": [5]}), "rubric_shape"),
        (
            lambda: json.dumps({**rubric_doc(), "items": [{"metric": SHARE}]}),
            "rubric_shape",
        ),
        (
            lambda: json.dumps({**rubric_doc(), "format": "tracer.rubric/2"}),
            "rubric_format_unsupported",
        ),
        (
            lambda: json.dumps({**rubric_doc(), "format": "Tracer.rubric/1"}),
            "rubric_format_unsupported",
        ),
        (lambda: json.dumps({**rubric_doc(), "format": 1}), "rubric_format_unsupported"),
        (lambda: json.dumps(rubric_doc(threshold=1.5)), "rubric_parameter_invalid"),
        (lambda: json.dumps(rubric_doc(threshold=-0.1)), "rubric_parameter_invalid"),
        (lambda: json.dumps(rubric_doc(threshold=1e-30)), "rubric_parameter_invalid"),
        (lambda: json.dumps(rubric_doc(normaliser="mr-Deva/2")), "rubric_parameter_invalid"),
        (lambda: json.dumps(rubric_doc(normaliser="")), "rubric_parameter_invalid"),
        (lambda: json.dumps(rubric_doc(normaliser="hi-Deva/1")), "rubric_parameter_invalid"),
        (
            lambda: json.dumps({**rubric_doc(), "items": [{"metric": "wer", "required": False}]}),
            "rubric_metric_unknown",
        ),
        (
            lambda: json.dumps(
                {
                    **rubric_doc(),
                    "items": [{"metric": "recorded_call_no_outcome_check", "required": False}],
                }
            ),
            "rubric_metric_unknown",
        ),
        (
            lambda: json.dumps(rubric_doc(optional=(MER, MER))),
            "rubric_metric_repeated",
        ),
        (lambda: json.dumps(rubric_doc(budget=1.5)), "rubric_pass_rule_invalid"),
        (lambda: json.dumps(rubric_doc(budget=-0.5)), "rubric_pass_rule_invalid"),
        (lambda: json.dumps(rubric_doc(budget="0.25")), "rubric_pass_rule_invalid"),
        (lambda: json.dumps(rubric_doc(budget=True)), "rubric_pass_rule_invalid"),
        (lambda: json.dumps(rubric_doc(budget=None)), "rubric_pass_rule_invalid"),
    ],
)
def test_rubric_file_refusals_carry_one_stable_code(make: Any, code: str) -> None:
    rubric = mod("rubricfile")
    with pytest.raises(rubric.RubricFileError) as caught:
        rubric.parse_rubric_file(make())
    assert caught.value.code == code
    assert str(caught.value) == code or code in str(caught.value)
    assert isinstance(caught.value, ValueError)


def test_rubric_file_pass_rule_shape_is_range_with_max_only() -> None:
    rubric = mod("rubricfile")
    rules: list[Any] = [
        {"kind": "equals_true"},
        {"kind": "range", "min": 0.1, "inclusive": True},
        {"kind": "range", "min": 0.0, "max": 0.2, "inclusive": True},
        {"kind": "range", "max": 0.2},
        {"kind": "range", "max": 0.2, "inclusive": "yes"},
        {"kind": "range", "max": 0.2, "inclusive": True, "note": "x"},
        {"kind": "one_of", "options": ["a"]},
        {"kind": "range", "inclusive": True},
        {"max": 0.2, "inclusive": True},
        [],
        None,
    ]
    for rule in rules:
        doc = rubric_doc()
        doc["items"][0]["pass_rule"] = rule
        if rule is None:
            doc["items"][0].pop("pass_rule")
        with pytest.raises(rubric.RubricFileError) as caught:
            rubric.parse_rubric_file(json.dumps(doc))
        assert caught.value.code in {
            "rubric_pass_rule_invalid",
            "rubric_required_without_pass_rule",
        }, rule
    # only the share may carry a pass rule: the owner's budget is the one owner rule of Phase 3
    doc = rubric_doc()
    doc["items"][1]["pass_rule"] = {"kind": "range", "max": 0.5, "inclusive": True}
    with pytest.raises(rubric.RubricFileError) as caught:
        rubric.parse_rubric_file(json.dumps(doc))
    assert caught.value.code == "rubric_shape"


def test_rubric_file_is_parsed_exactly_and_without_defaults() -> None:
    rubric = mod("rubricfile")
    parsed = rubric.parse_rubric_file(rubric_text())
    assert parsed.flag_min_confidence == TEST_ONLY_FLAG_MIN_CONFIDENCE
    assert type(parsed.flag_min_confidence) is float
    assert parsed.normaliser == NORMALISER
    assert [(i.metric, i.required) for i in parsed.items] == [
        (SHARE, True),
        (MER, False),
        (COUNT, False),
    ]
    share = parsed.items[0].pass_rule
    assert (share.kind, share.max, share.min, share.inclusive) == (
        "range",
        TEST_ONLY_REVIEW_BUDGET,
        None,
        True,
    )
    assert parsed.items[1].pass_rule is None
    assert rubric.RUBRIC_FORMAT == "tracer.rubric/1"
    # the share alone is a complete file, an integer threshold is a number too
    only = rubric.parse_rubric_file(rubric_text(optional=(), threshold=1, budget=1))
    assert [i.metric for i in only.items] == [SHARE]
    assert only.flag_min_confidence == 1
    # the order of the items is the order of the file
    reordered = rubric_doc()
    reordered["items"].reverse()
    assert [i.metric for i in rubric.parse_rubric_file(json.dumps(reordered)).items] == [
        COUNT,
        MER,
        SHARE,
    ]
    with pytest.raises(TypeError):
        rubric.parse_rubric_file(rubric_text().encode("utf-8"))
    signature = inspect.signature(rubric.parse_rubric_file)
    assert list(signature.parameters) == ["text"]


def test_rubric_spec_has_no_defaults() -> None:
    rubric = mod("rubricfile")
    for cls in (rubric.RubricSpec, rubric.RubricItemSpec):
        for parameter in inspect.signature(cls).parameters.values():
            assert parameter.default is inspect.Parameter.empty, (cls.__name__, parameter.name)


# ------------------------------------------------------------------- binding parameters


def test_missing_binding_param_refuses_before_any_send(tmp_path: Any) -> None:
    definitions = mod("definitions")
    rubric = mod("rubricfile")
    good = {"flag_min_confidence": TEST_ONLY_FLAG_MIN_CONFIDENCE, "normaliser": NORMALISER}

    def refused(params: Any, code: str) -> None:
        with pytest.raises(definitions.BindingParameterError) as caught:
            definitions.check_binding_parameters(params)
        assert caught.value.code == code, (code, caught.value.code)
        assert isinstance(caught.value, ValueError)
        assert str(TEST_ONLY_FLAG_MIN_CONFIDENCE) not in str(caught.value)

    assert definitions.check_binding_parameters({key: dict(good) for key in KEYS}) == (
        definitions.ScoringParameters(Decimal("0.3"), NORMALISER)
    )
    refused({}, "binding_param_missing")
    refused({key: dict(good) for key in KEYS[:2]}, "binding_param_missing")
    refused(
        {**{key: dict(good) for key in KEYS}, COUNT: {"normaliser": NORMALISER}},
        "binding_param_missing",
    )
    refused(
        {**{key: dict(good) for key in KEYS}, COUNT: {"flag_min_confidence": 0.3}},
        "binding_param_missing",
    )
    refused(
        {**{key: dict(good) for key in KEYS}, MER: {**good, "flag_min_confidence": None}},
        "binding_param_missing",
    )
    for bad in (True, False, "0.3", 1.0000001, -0.0001, float("nan"), float("inf"), 1e-30, [0.3]):
        refused(
            {key: {**good, "flag_min_confidence": bad} for key in KEYS}, "binding_param_invalid"
        )
    refused({key: {**good, "normaliser": 3} for key in KEYS}, "binding_param_invalid")
    refused({key: {**good, "normaliser": "mr-Deva/2"} for key in KEYS}, "normaliser_unknown")
    refused(
        {**{key: dict(good) for key in KEYS}, SHARE: {**good, "flag_min_confidence": 0.4}},
        "binding_param_unequal",
    )
    # int 1 and float 1.0 and the decimal 1 are the same number: equal
    equal = definitions.check_binding_parameters(
        {
            MER: {**good, "flag_min_confidence": 1},
            COUNT: {**good, "flag_min_confidence": 1.0},
            SHARE: good | {"flag_min_confidence": 1},
        }
    )
    assert equal.flag_min_confidence == Decimal(1)

    # the builder refuses a spec with a missing value, and nothing is built
    broken = rubric.RubricSpec(None, NORMALISER, spec().items)
    with pytest.raises(definitions.BindingParameterError) as caught:
        definitions.build_configuration(broken, configured_language="mr", created_at=CREATED_AT)
    assert caught.value.code == "binding_param_missing"

    # the pipeline reads the parameters from the bindings of the snapshot: a snapshot whose
    # binding lost its threshold is refused before the engine is asked for anything
    config = configuration()
    binding = config.binding(COUNT)
    damaged = binding.model_copy(update={"params": {"normaliser": NORMALISER}})
    bindings = [damaged if b.id == binding.id else b for b in config.snapshot.metric_bindings]
    bad_config = definitions.RecordedConfiguration(
        config.snapshot.model_copy(update={"metric_bindings": bindings})
    )
    engine = FakeEngine()
    with pytest.raises(definitions.BindingParameterError) as caught:
        run_pipeline(tmp_path, engine=engine, config=bad_config)
    assert caught.value.code == "binding_param_missing"
    assert engine.calls == []


def test_binding_parameters_equal_in_the_three_bindings() -> None:
    definitions = mod("definitions")
    config = configuration()
    for key in KEYS:
        binding = config.binding(key)
        assert binding.enabled is True
        assert binding.params == {
            "flag_min_confidence": TEST_ONLY_FLAG_MIN_CONFIDENCE,
            "normaliser": NORMALISER,
        }
        assert type(binding.params["flag_min_confidence"]) is float
    assert definitions.parameters_of(config.snapshot) == definitions.ScoringParameters(
        Decimal("0.3"), NORMALISER
    )
    # the threshold is read as the decimal of its shortest spelling, not its binary expansion
    other = configuration(threshold=0.1)
    assert definitions.parameters_of(other.snapshot).flag_min_confidence == Decimal("0.1")


def test_build_configuration_refuses_bad_values_and_a_language_without_a_default() -> None:
    definitions = mod("definitions")
    rubric = mod("rubricfile")
    items = spec().items
    for threshold in (True, "0.3", 1.5, -1, float("nan"), Decimal("0.3")):
        with pytest.raises(definitions.BindingParameterError):
            definitions.build_configuration(
                rubric.RubricSpec(threshold, NORMALISER, items),
                configured_language="mr",
                created_at=CREATED_AT,
            )
    with pytest.raises(definitions.BindingParameterError):
        definitions.build_configuration(
            rubric.RubricSpec(0.3, "mr-Deva/9", items),
            configured_language="mr",
            created_at=CREATED_AT,
        )
    for name in ("configured_language", "created_at"):
        parameter = inspect.signature(definitions.build_configuration).parameters[name]
        assert parameter.default is inspect.Parameter.empty
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert "spec" in inspect.signature(definitions.build_configuration).parameters


# ------------------------------------------------------------------- the configuration


def test_configuration_snapshot_is_a_valid_recorded_scoring_snapshot() -> None:
    from tracer_evalschema.snapshot import ConfigSnapshot

    definitions = mod("definitions")
    config = configuration()
    snapshot = config.snapshot
    assert ConfigSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    keys = [d.key for d in snapshot.metric_definitions]
    assert keys == [MER, COUNT, SHARE, definitions.PLACEHOLDER_KEY]
    assert [b.metric.id for b in snapshot.metric_bindings] == [
        d.id for d in snapshot.metric_definitions
    ]
    assert all(b.evaluator_id == snapshot.evaluator.id for b in snapshot.metric_bindings)
    assert [r.id for r in snapshot.evaluator.metric_bindings] == [
        b.id for b in snapshot.metric_bindings
    ]
    assert config.definition(SHARE).pass_rule.max == TEST_ONLY_REVIEW_BUDGET
    assert config.definition(SHARE).pass_rule.inclusive is True
    assert config.binding(SHARE).metric.id == config.definition(SHARE).id
    with pytest.raises(KeyError):
        config.definition("nothing")
    assert config.snapshot.pricing_catalog is None


def test_configuration_rubric_items_and_the_inert_expected_outcome_check() -> None:
    """Finding F13 of the contract: schema 0.0.1 demands an expected-outcome judge check that is
    a required rubric item, so a recorded run carries one that is never run."""
    definitions = mod("definitions")
    config = configuration()
    snapshot = config.snapshot
    placeholder = config.definition(definitions.PLACEHOLDER_KEY)
    assert placeholder.kind == "llm_judge"
    assert placeholder.value_type == "boolean"
    assert placeholder.pass_rule.kind == "equals_true"
    assert placeholder.prompt_variables == ["expected_outcome"]
    assert placeholder.languages == "language_independent"
    assert (placeholder.judge_model_ref.provider, placeholder.judge_model_ref.model) == (
        "none",
        "not_run",
    )
    assert placeholder.implementation_ref is None
    assert config.binding(definitions.PLACEHOLDER_KEY).enabled is True
    by_binding = {
        b.id: d.key
        for b, d in zip(snapshot.metric_bindings, snapshot.metric_definitions, strict=True)
    }
    items = [(by_binding[i.binding.id], i.required, i.weight) for i in snapshot.rubric.items]
    assert items == [
        (definitions.PLACEHOLDER_KEY, True, None),
        (SHARE, True, None),
        (MER, False, None),
        (COUNT, False, None),
    ]
    # the references carry the digest of the record they name
    for item in snapshot.rubric.items:
        binding = next(b for b in snapshot.metric_bindings if b.id == item.binding.id)
        assert item.binding.content_digest == binding.content_digest


def test_configuration_rubric_follows_the_file_for_optional_items() -> None:
    definitions = mod("definitions")
    config = configuration(optional=())
    by_binding = {
        b.id: d.key
        for b, d in zip(
            config.snapshot.metric_bindings, config.snapshot.metric_definitions, strict=True
        )
    }
    assert [by_binding[i.binding.id] for i in config.snapshot.rubric.items] == [
        definitions.PLACEHOLDER_KEY,
        SHARE,
    ]
    # the three scorers are bound whether or not the file lists them
    assert [d.key for d in config.snapshot.metric_definitions][:3] == list(KEYS)
    reordered = rubric_doc()
    reordered["items"].reverse()
    config = definitions.build_configuration(
        mod("rubricfile").parse_rubric_file(json.dumps(reordered)),
        configured_language="mr",
        created_at=CREATED_AT,
    )
    by_binding = {
        b.id: d.key
        for b, d in zip(
            config.snapshot.metric_bindings, config.snapshot.metric_definitions, strict=True
        )
    }
    assert [by_binding[i.binding.id] for i in config.snapshot.rubric.items] == [
        definitions.PLACEHOLDER_KEY,
        COUNT,
        MER,
        SHARE,
    ]


def test_configuration_evaluator_and_persona_say_what_the_run_is() -> None:
    from tracer_evalschema.evaluator import MAX_DURATION_MS

    definitions = mod("definitions")
    snapshot = configuration().snapshot
    evaluator, persona = snapshot.evaluator, snapshot.persona
    text = evaluator.instructions.lower()
    for phrase in ("recorded call", "no call was simulated", "agreement", "speech-to-text"):
        assert phrase in text, phrase
    assert "no outcome is evaluated" in evaluator.expected_outcome.lower()
    assert evaluator.name == definitions.EVALUATOR_NAME
    assert evaluator.language == "mr"
    assert evaluator.max_duration_ms == MAX_DURATION_MS == 14_400_000
    assert evaluator.scripted_turns == []
    assert evaluator.expected_tool_calls == []
    assert evaluator.test_profile is None
    assert persona.language == "mr"
    assert persona.accent is None
    assert evaluator.persona.id == persona.id
    # none of the texts claims a measured length, a score or a simulated caller
    for claim in ("accuracy", "simulated caller", "error rate"):
        assert claim not in text
    # the configured language is carried, never assumed
    other = configuration(language="hi").snapshot
    assert (other.evaluator.language, other.persona.language) == ("hi", "hi")


def test_configuration_digests_verify_and_ids_are_fresh() -> None:
    first, second = configuration(), configuration()
    assert first.snapshot.id != second.snapshot.id
    assert first.snapshot.content_digest != second.snapshot.content_digest
    from tracer_evalschema.digest import verify_digest
    from tracer_evalschema.snapshot import snapshot_digest_domain

    verify_digest(
        snapshot_digest_domain(first.snapshot.model_dump(mode="json", by_alias=True)),
        first.snapshot.content_digest,
    )
    # the same configuration is reused for many runs: it is a value, not a factory
    assert first.snapshot is first.snapshot


def test_recorded_project_and_agent_target_are_honest_records() -> None:
    definitions = mod("definitions")
    from tracer_evalschema.provider import ProviderConfigRef

    project = definitions.recorded_project(name="helpline review", created_at=CREATED_AT)
    assert project.name == "helpline review"
    assert project.provider_configs == []
    ref = ProviderConfigRef(provider="sarvam", model="saaras:v4", config_ref="env:SARVAM_API_KEY")
    with_config = definitions.recorded_project(
        name="helpline review", created_at=CREATED_AT, provider_configs=[ref]
    )
    assert with_config.provider_configs == [ref]
    target = definitions.recorded_agent_target(name="recorded agent", created_at=CREATED_AT)
    assert target.version == 1
    assert (
        target.endpoint.url == definitions.RECORDED_ENDPOINT_URL == "wss://recorded-call.invalid/"
    )
    assert target.languages == []
    assert target.declared_capabilities == []
    assert target.declared_stack == []
    assert project.id != definitions.recorded_project(name="x", created_at=CREATED_AT).id


# ------------------------------------------------------------------- BL-145: agent outputs only


def test_metrics_evaluate_agent_outputs_only(tmp_path: Any) -> None:
    """ADR 0016 Decision 1 as amended (I17): a turn of an agent-produced record is an agent
    output whatever its role, and our own STT transcript is only the comparison side."""
    caller_flagged = scripts(
        texts=(
            sentence(
                STT_TEXTS[0].split(" ")[0],
                STT_TEXTS[0].split(" ")[1],
                STT_TEXTS[0].split(" ")[2],
                STT_TEXTS[0].split(" ")[3],
                ABHI,
                STT_TEXTS[0].split(" ")[5],
            ),
            STT_TEXTS[1],
        ),
        phrases=None,
    )
    run = run_pipeline(tmp_path, engine=FakeEngine(chunk_scripts=caller_flagged))
    bundle = run.bundle
    export = next(t for t in bundle.transcripts if t.source == "agent_export")
    stt = next(t for t in bundle.transcripts if t.source == "our_stt")
    export_turns = {t.seq: t for t in bundle.turns if t.transcript_id == export.id}
    stt_turns = [t for t in bundle.turns if t.transcript_id == stt.id]
    assert {t.role for t in export_turns.values()} == {"agent", "caller"}
    assert {t.role for t in stt_turns} == {"unknown"}
    results = results_by_key(bundle)
    document = flags_of(results[COUNT])
    flagged_seqs = {s["turn_seq"] for s in document["spans"] if s["flagged"]}
    # the caller line of the export (the agent's own recognition of the caller) is evaluated
    assert export_turns[1].role == "caller"
    assert 1 in flagged_seqs
    assert 2 in flagged_seqs
    # no span ever sits on a turn the agent did not produce: every span names an export turn
    assert {s["turn_seq"] for s in document["spans"]} <= set(export_turns)
    stt_only_seqs = {t.seq for t in stt_turns} - set(export_turns)
    assert not stt_only_seqs & {s["turn_seq"] for s in document["spans"]}
    for key in KEYS:
        refs = results[key].evidence_refs
        turn_refs = [r for r in refs if r.kind == "turn"]
        assert turn_refs
        by_id = {t.id: t for t in bundle.turns}
        for ref in turn_refs:
            turn = by_id[ref.ref_id]
            assert turn.transcript_id == export.id
            assert turn.source.startswith("agent_")
            assert turn.source != "our_stt"
        assert {by_id[r.ref_id].seq for r in turn_refs} == flagged_seqs
        # the only other evidence is a range of the recording itself
        assert {r.kind for r in refs} <= {"turn", "artifact_range"}
        assert all(
            r.ref_id in {a.id for a in bundle.artifacts} for r in refs if r.kind == "artifact_range"
        )
    # the metrics read agent_transcript, never the comparison side alone
    for definition in bundle.snapshot.metric_definitions:
        if definition.key in KEYS:
            assert "agent_transcript" in definition.requires
    # and the caller is not evaluated by a verdict: there is none
    assert bundle.verdict is None
    _ = (BASE_EXPORT, EXPORT_LINES, KAAM, STT_PHRASES, ChunkScript, export_text)


# ------------------------------------------------------------------- the example rubric file

EXAMPLE = ROOT / "docs" / "examples" / "rubric-mr.example.json"


def test_shipped_example_rubric_file_loads_builds_and_runs(tmp_path: Any) -> None:
    """The project's example rubric: budget max 1.0 is a labelled PLACEHOLDER (no limit, the rule
    cannot fail) and the threshold 0.5 is a run parameter of the file, not a default of code."""
    rubric = mod("rubricfile")
    text = EXAMPLE.read_text("utf-8")
    document = json.loads(text)
    assert "PLACEHOLDER" in document["note"]
    assert "no limit" in document["note"]
    parsed = rubric.parse_rubric_file(text)
    assert parsed.flag_min_confidence == 0.5
    assert parsed.normaliser == NORMALISER
    share = parsed.items[0]
    assert (share.metric, share.required) == (SHARE, True)
    assert (share.pass_rule.kind, share.pass_rule.max, share.pass_rule.min) == ("range", 1.0, None)
    assert share.pass_rule.inclusive is True
    assert type(share.pass_rule.max) is float
    config = mod("definitions").build_configuration(
        parsed, configured_language="mr", created_at=CREATED_AT
    )
    run = run_pipeline(tmp_path, config=config)
    check_run_bundle(run.bundle)
    assert config.definition(SHARE).pass_rule.max == 1.0
    # every token disagrees: the share is 1.0 and the placeholder budget still cannot fail
    worst = FakeEngine(chunk_scripts=scripts(texts=("q1 q2 q3", "q4 q5"), phrases=None))
    everything = run_pipeline(tmp_path, config=config, engine=worst)
    share_result = results_by_key(everything.bundle)[SHARE]
    assert share_result.value == 1.0
    assert share_result.passed is True
    # the note is the file's, not a record: it is stored nowhere
    assert "PLACEHOLDER" not in run.bundle.model_dump_json()


def test_rubric_file_note_is_optional_text_and_nothing_else() -> None:
    rubric = mod("rubricfile")
    with_note = rubric_doc()
    with_note["note"] = "free text"
    assert rubric.parse_rubric_file(json.dumps(with_note)).normaliser == NORMALISER
    for bad in (5, None, ["x"], {"a": 1}):
        with_note["note"] = bad
        with pytest.raises(rubric.RubricFileError) as caught:
            rubric.parse_rubric_file(json.dumps(with_note))
        assert caught.value.code == "rubric_shape"


def test_no_code_path_supplies_a_review_budget() -> None:
    definitions = mod("definitions")
    rubric = mod("rubricfile")
    items = spec().items
    # a spec without the budget cannot be built into a configuration
    for broken in (
        (),
        (rubric.RubricItemSpec(SHARE, True, None),),
        (rubric.RubricItemSpec(SHARE, False, items[0].pass_rule),),
        (rubric.RubricItemSpec(MER, False, None),),
    ):
        with pytest.raises(rubric.RubricFileError) as caught:
            definitions.build_configuration(
                rubric.RubricSpec(0.5, NORMALISER, broken),
                configured_language="mr",
                created_at=CREATED_AT,
            )
        assert caught.value.code == "rubric_budget_missing"
    # the only module that makes a range rule is the rubric file parser
    for name in ("definitions", "scoring", "pipeline", "flagsdoc", "registry"):
        tree = ast.parse((SRC / f"{name}.py").read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "RangeRule":
                pytest.fail(f"{name}.py builds a range rule")
            if isinstance(node, ast.Constant) and node.value in ("range", "inclusive"):
                pytest.fail(f"{name}.py writes a pass rule")
    parameters = inspect.signature(definitions.build_configuration).parameters
    assert not [n for n in parameters if "budget" in n or "max" in n]


# ------------------------------------------------------------------- the SCOPE rows


def _defined_tests() -> set[str]:
    names: set[str] = set()
    for path in ROOT.glob("packages/*/tests/test_*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.FunctionDef):
                names.add(node.name)
    return names


@pytest.mark.parametrize("task", ["P3-18", "P3-19", "P3-20", "P3-21"])
def test_every_test_named_by_the_scope_rows_of_batch_d_exists(task: str) -> None:
    import re

    rows = [
        line
        for line in (ROOT / "docs" / "phase3" / "SCOPE.md").read_text("utf-8").splitlines()
        if line.startswith(f"| {task} |")
    ]
    assert len(rows) == 1
    named = set(re.findall(r"`(test_\w+)`", rows[0]))
    assert len(named) >= 3
    assert named <= _defined_tests(), sorted(named - _defined_tests())
