# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-04a: typed identifiers, the frozen prefix registry, the UUIDv7 generator (ADR 0001).

Assumed public API (docs/phase1/api-surface.md): ``tracer_evalschema.ids`` with ``ID_PREFIXES``,
one ``<Entity>Id`` class per registered prefix, ``id_type()``, ``Uuid7Generator``, ``uuid7()``,
``from_external()``, ``ExternalRef``, ``validate_new_prefix()``; ``tracer_evalschema.registry``
for the whole-model walks. Imports are lazy so a missing module fails the test, not collection.
"""

from __future__ import annotations

import copy
import functools
import inspect
import itertools
import json
import random
import re
import subprocess
import sys
import textwrap
import typing
import uuid
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from ._support import api

# A literal copy of the table in ADR 0001 Decision 4 (prefix -> entity). Do not generate this from
# the implementation: the point is that the two are compared. Revision 15 (N47, api-surface 23.6
# group B item 8): the prefix `pce` is withdrawn because `PriceEntry` is a value object, so the
# table has 26 entries; this is a specification change of the literal table, not a weakening.
ADR_PREFIXES: dict[str, str] = {
    "prj": "Project",
    "agt": "AgentTarget",
    "per": "Persona",
    "tpf": "TestProfile",
    "evl": "Evaluator",
    "met": "MetricDefinition",
    "mbd": "MetricBinding",
    "rub": "Rubric",
    "snap": "ConfigSnapshot",
    "run": "Run",
    "art": "Artifact",
    "tsc": "Transcript",
    "turn": "Turn",
    "exc": "Exchange",
    "tcl": "ToolCallEvent",
    "spn": "StageSpan",
    "res": "MetricResult",
    "vrd": "RunVerdict",
    "dts": "Dataset",
    "dti": "DatasetItem",
    "pcat": "PricingCatalog",
    "use": "UsageRecord",
    "cst": "CostRecord",
    "csum": "CostSummary",
    "rbnd": "RunBundle",
    "exp": "ExportBundle",
}

# ADR 0001 Decision 8 (value objects, including the additions of revisions 5, 6, 7 and 8).
# `Endpoint` and `AppliesWhenCondition` are left out of the existence check: the ADRs describe them
# as families discriminated by a tag, so a single class of that name is not implied (see
# api-surface.md; revision 8 lists the three endpoint classes).
VALUE_OBJECT_NAMES = [
    "Money",
    "ContentDigest",
    "KeyRef",
    "PrivacyMarker",
    "ConsentRecord",
    "RetentionPolicy",
    "SealedPayload",
    "RedactionRecord",
    "AudioFormat",
    "ChannelMap",
    "Segment",
    "ScriptedTurn",
    "ExpectedToolCall",
    "ExternalRef",
    "EvidenceRef",
    "IngestWarning",
    "WallTime",
    "TimelineOrigin",
    "TestProfileRef",
    "PricingCatalogRef",
    "PriceEntry",  # revision 15 (N47): a value object of PricingCatalog, no entity fields
    "ProviderConfigRef",
    "JudgeInfo",
    "VerdictCheck",
    "UsageQuantity",
    "BillingRules",
    "PriceProvenance",
    "PricingSnapshot",
    "ReportedCostRaw",
    "DeclaredStackEntry",
    "MetadataFlag",
    "CallerExternalId",
    "RangeRule",
    "EqualsTrueRule",
    "OneOfRule",
    "PerTurnCounts",
    "CostRounding",
    # revision 6 (P-54) and revision 7 (ADR 0001 Decision 8)
    "CostExclusion",
    "VersionedRef",
    "BidiFlaggedText",
    "LanguageLabelWarning",
    "ObservedLanguage",
    "PerTurnSummary",
    # revision 8 (ADR 0001 Decision 8, batch 2): the typed references, the judge model reference,
    # the shapes of scale and aggregation, the rubric item, the lint hit and the three endpoint
    # classes (`Endpoint` itself is their union alias, not a class)
    "PersonaRef",
    "MetricDefinitionRef",
    "MetricBindingRef",
    "ModelRef",
    "MetricParameter",
    "RatingScale",
    "EnumScale",
    "PerConversationAggregation",
    "PerTurnShareAggregation",
    "RubricItem",
    "LiteralProfileValue",
    "WebSocketEndpoint",
    "WebRtcEndpoint",
    "SipEndpoint",
    # revision 10 (ADR 0001 Decision 8; api-surface 21.4: "VALUE_OBJECT_NAMES gains ScorerInfo and
    # LanguageDeclaration once they exist")
    "ScorerInfo",
    "LanguageDeclaration",
    # batch 3 (api-surface section 22): value objects the batch 3 entities embed
    "ChannelAssignment",
    "ResultError",
    "JudgeTurnVerdict",
    "WeightedExclusion",
]
ENTITY_FIELDS = {"id", "schema_version", "created_at", "metadata"}
# Revisions 7 and 8 (ADR 0001 Decision 8 and I14, api-surface section 18 N1 and section 19.6 N6): a
# value object has no identity fields of its own. The exceptions are a rule, not a list:
# - a value object named `<Entity>Ref`, where `<Entity>` is a registered entity class name, may have
#   an `id` typed `<Entity>Id` (the typed ID of the REFERENCED entity);
# - `VersionedRef.id`, the generic reference of the bundle seam, holds a typed ID of any kind;
# - the `schema_version` of the nested model behind `SealedPayload.aad_fields` is the version of a
#   sealed plaintext (ADR 0015 Decision 4).
# The references that must exist at the gate (revision 7 had three, revision 8 adds the typed
# references of batch 2). The three typed references of batch 2 are subclasses of `VersionedRef`
# that narrow `id` (api-surface 19.4.1).
GENERIC_REFERENCE = ("VersionedRef", "id")
EXPECTED_TYPED_REFS = (
    "TestProfileRef",
    "PricingCatalogRef",
    "PersonaRef",
    "MetricDefinitionRef",
    "MetricBindingRef",
    "AgentTargetRef",  # batch 3 (api-surface section 22): Run.agent_target
)
NARROWED_VERSIONED_REFS = (
    "PersonaRef",
    "MetricDefinitionRef",
    "MetricBindingRef",
    "AgentTargetRef",
)

ID_PREFIX_NAMES = tuple(ADR_PREFIXES)

RUN_PATTERN = r"^run_[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


def _id_class(prefix: str) -> Any:
    return api("ids").id_type(prefix)


def _adapter(prefix: str) -> TypeAdapter[Any]:
    return TypeAdapter(_id_class(prefix))


def _hex_of(uuid_value: uuid.UUID) -> str:
    return str(uuid_value)


# --------------------------------------------------------------------------- wire form


def test_id_roundtrip_prefixed() -> None:
    ids = api("ids")
    assert set(ids.ID_PREFIXES) == set(ADR_PREFIXES), "registry must be loaded before the walk"
    for prefix in ADR_PREFIXES:
        cls = _id_class(prefix)
        adapter = TypeAdapter(cls)
        first = cls.new()
        wire = str(first)
        assert wire == wire.lower()
        assert re.fullmatch(
            rf"{prefix}_[0-9a-f]{{8}}-[0-9a-f]{{4}}-7[0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}",
            wire,
        ), wire
        parsed = adapter.validate_python(wire)
        assert parsed == first
        assert hash(parsed) == hash(first)
        assert adapter.dump_python(parsed, mode="json") == wire
        assert adapter.dump_json(parsed) == f'"{wire}"'.encode()
        assert adapter.validate_json(f'"{wire}"') == first
        assert adapter.validate_python(first) == first, "an instance of the same kind is accepted"


def test_id_rejects_wrong_kind() -> None:
    ids = api("ids")
    prefixes = list(ids.ID_PREFIXES)
    assert len(prefixes) == 26
    for expected, other in itertools.permutations(prefixes, 2):
        adapter = _adapter(expected)
        wire = str(_id_class(other).new())
        with pytest.raises(ValidationError):
            adapter.validate_python(wire)
        with pytest.raises(ValidationError):
            adapter.validate_json(f'"{wire}"')
    # an instance of another kind is not coerced either
    with pytest.raises(ValidationError):
        _adapter("run").validate_python(_id_class("evl").new())


def test_id_rejects_uppercase() -> None:
    adapter = _adapter("run")
    body = "0197b2a4-7c3e-7a1f-9b2d-3c4d5e6f7a8b"
    assert adapter.validate_python("run_" + body).uuid == uuid.UUID(body)
    for bad in (
        ("run_" + body).upper(),
        "RUN_" + body,
        "Run_" + body,
        "run_" + body.upper(),
        "run_" + body[:-1] + "B",
        "run_" + "0197B2A4" + body[8:],
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)
        with pytest.raises(ValidationError):
            adapter.validate_json(f'"{bad}"')


def test_id_rejects_uuid_v4_with_prefix() -> None:
    adapter = _adapter("run")
    for bad_uuid in (uuid.uuid4(), uuid.uuid1(), uuid.UUID(int=0), uuid.UUID(int=(1 << 128) - 1)):
        with pytest.raises(ValidationError):
            adapter.validate_python(f"run_{bad_uuid}")
    # version 7 but the variant bits are not 10xx
    good = str(_id_class("run").new()).split("_", 1)[1]
    for variant in ("0", "7", "c", "f"):
        broken = good[:19] + variant + good[20:]
        assert broken[14] == "7"
        with pytest.raises(ValidationError):
            adapter.validate_python(f"run_{broken}")
    # version nibble 8 (UUIDv8) is also not ours
    with pytest.raises(ValidationError):
        adapter.validate_python("run_" + good[:14] + "8" + good[15:])


def test_id_rejects_bare_uuid_on_wire() -> None:
    adapter = _adapter("run")
    good = _id_class("run").new()
    bare = str(good.uuid)
    with pytest.raises(ValidationError):
        adapter.validate_python(bare)
    with pytest.raises(ValidationError):
        adapter.validate_json(f'"{bare}"')
    with pytest.raises(ValidationError):
        adapter.validate_python(good.uuid)
    with pytest.raises(ValidationError):
        adapter.validate_python(bare.replace("-", ""))
    with pytest.raises(ValidationError):
        adapter.validate_python(f"run{bare}")
    with pytest.raises(ValidationError):
        adapter.validate_python(f"run_{bare.replace('-', '')}")


def test_id_rejects_trailing_newline_and_whitespace() -> None:
    """Proposed name. ``$`` in a regex accepts a trailing newline; an ID must be exact."""
    adapter = _adapter("run")
    good = str(_id_class("run").new())
    for bad in (good + "\n", " " + good, good + " ", "\t" + good, good + "\r\n", good + "\x00"):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)


def test_id_is_not_a_str_subclass() -> None:
    """Proposed name. ADR 0001 Decision 1: the typed ID exposes a uuid.UUID, it is not a str."""
    cls = _id_class("run")
    assert not issubclass(cls, str)
    assert not issubclass(cls, uuid.UUID)
    value = cls.new()
    assert isinstance(value.uuid, uuid.UUID)
    assert value != str(value), "an ID never equals its own wire string"


def test_id_uuid_property_is_uuid_UUID() -> None:
    for prefix in ADR_PREFIXES:
        value = _id_class(prefix).new()
        assert type(value.uuid) is uuid.UUID
        assert value.uuid.version == 7
        assert str(value) == f"{prefix}_{value.uuid}"
        again = TypeAdapter(_id_class(prefix)).validate_python(str(value))
        assert again.uuid == value.uuid
        assert value.prefix == prefix


# ---------------------------------------------------------------------------- external


def test_from_external_mints_v7_and_records_external_ref() -> None:
    ids = api("ids")
    foreign = [
        str(uuid.uuid4()),
        "call_9f8e7d6c5b4a",
        "7c9e6679-7425-40de-944b-e07fc1f90ae7",
        "  spaced value  ",
    ]
    minted: set[str] = set()
    for value in foreign:
        new_id, ref = ids.from_external(ids.RunId, system="voicera", kind="call_id", value=value)
        assert isinstance(new_id, ids.RunId)
        assert new_id.uuid.version == 7
        assert value not in str(new_id), "the external value never becomes our ID"
        assert (ref.system, ref.kind, ref.value) == ("voicera", "call_id", value)
        assert ref.value == value, "the external value is stored verbatim as data"
        minted.add(str(new_id))
    assert len(minted) == len(foreign), "every call mints a fresh ID"
    # the same external value minted twice gives two IDs: de-duplication is Phase 2 (P-02)
    first, _ = ids.from_external(ids.RunId, system="s", kind="k", value="same")
    second, _ = ids.from_external(ids.RunId, system="s", kind="k", value="same")
    assert first != second
    # the ExternalRef is a value object that round-trips
    ref_type = type(ref)
    assert ref_type(**ref.model_dump()) == ref
    assert ref_type.model_validate_json(ref.model_dump_json()) == ref


def test_from_external_signature_is_id_type_then_keyword_only_parts() -> None:
    """Proposed name. ADR 0001 Decision 7 (revision 7):
    ``from_external(id_type, *, system, kind, value) -> (TypedId, ExternalRef)``."""
    ids = api("ids")
    params = list(inspect.signature(ids.from_external).parameters.values())
    assert [p.name for p in params] == ["id_type", "system", "kind", "value"]
    assert params[0].kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
    for param in params[1:]:
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, param.name
        assert param.default is inspect.Parameter.empty, f"{param.name} must be required"
    with pytest.raises(TypeError):
        ids.from_external(ids.RunId, "voicera", "call_id", "abc")
    with pytest.raises(TypeError):
        ids.from_external(system="voicera", kind="call_id", value="abc")
    with pytest.raises(TypeError):
        ids.from_external(ids.RunId, system="voicera", kind="call_id")
    # the old ADR spelling ``from_external(kind, system, value)`` is gone: ``kind`` is the
    # ExternalRef.kind, and the minted entity kind comes from ``id_type``
    new_id, ref = ids.from_external(ids.EvaluatorId, system="s", kind="call_id", value="v")
    assert isinstance(new_id, ids.EvaluatorId)
    assert new_id.prefix == "evl"
    assert ref.kind == "call_id"


def test_from_external_returns_a_pair_of_typed_id_and_external_ref_for_every_kind() -> None:
    """Proposed name. The minted ID has the requested kind, whatever the kind is."""
    ids = api("ids")
    for prefix in ID_PREFIX_NAMES:
        cls = _id_class(prefix)
        result = ids.from_external(cls, system="sys", kind="k", value=f"v-{prefix}")
        assert isinstance(result, tuple)
        assert len(result) == 2
        new_id, ref = result
        assert type(new_id) is cls
        assert type(ref) is ids.ExternalRef
        assert (ref.system, ref.kind, ref.value) == ("sys", "k", f"v-{prefix}")
        assert set(type(ref).model_fields) == {"system", "kind", "value"}


_PURITY_CHILD = textwrap.dedent(
    """
    import json
    import sys

    from tracer_evalschema import ids

    ids.from_external(ids.RunId, system="warm", kind="up", value="up")  # lazy imports happen here
    events = []
    WATCHED = (
        "open", "socket.", "sqlite3.", "subprocess.", "os.system", "os.exec", "os.spawn",
        "os.remove", "os.rename", "os.mkdir", "os.listdir", "os.scandir", "shutil.",
    )

    def hook(event, args):
        if event.startswith(WATCHED):
            events.append(event)

    sys.addaudithook(hook)
    for number in range(3):
        ids.from_external(ids.RunId, system="s", kind="k", value="same")
    print(json.dumps(events))
    """
)


def _module_state(module: Any) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for name, value in vars(module).items():
        if isinstance(value, (dict, list, set)):
            state[name] = copy.copy(value)
        elif hasattr(value, "cache_info"):
            state[name] = value.cache_info().currsize
    return state


def test_from_external_is_pure() -> None:
    """Proposed name. ADR 0001 Decision 7: it neither looks up nor stores anything (the P-02
    lookup is the importer's job, in Phase 2). Checked three ways: the same call gives different
    IDs, no module-level container or cache of ``ids`` changes, and (in a fresh interpreter, after
    a warm-up call) no file, socket, database or process event happens."""
    ids = api("ids")
    before = _module_state(ids)
    seen: set[str] = set()
    for _ in range(50):
        new_id, ref = ids.from_external(ids.RunId, system="s", kind="k", value="same")
        seen.add(str(new_id))
        assert ref.value == "same"
    assert len(seen) == 50, "no lookup by (system, kind, value): every call mints"
    assert _module_state(ids) == before, "nothing is stored in a module-level container"
    # the arguments are strings that are used as given, never normalized
    odd = "  Mixed Case\u200d\n"
    assert ids.from_external(ids.RunId, system=" S ", kind="K\t", value=odd)[1].value == odd
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _PURITY_CHILD],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == [], "from_external performed I/O"


# ---------------------------------------------------------------------------- generator


def _timestamp_ms(value: uuid.UUID) -> int:
    return value.int >> 80


def test_uuid7_monotonic_10k_same_ms() -> None:
    ids = api("ids")
    fixed = 1_800_000_000_000
    gen = ids.Uuid7Generator(clock_ms=lambda: fixed)
    produced = [gen.next() for _ in range(10_000)]
    assert all(isinstance(u, uuid.UUID) for u in produced)
    ints = [u.int for u in produced]
    assert all(a < b for a, b in itertools.pairwise(ints)), "must be strictly increasing"
    assert len(set(ints)) == 10_000
    assert all(u.version == 7 for u in produced)
    stamps = [_timestamp_ms(u) for u in produced]
    assert stamps[0] == fixed
    assert all(a <= b for a, b in itertools.pairwise(stamps))
    # a clock that goes backwards must not break monotonicity (RFC 9562 section 6.2)
    now = {"ms": fixed + 5}
    back = ids.Uuid7Generator(clock_ms=lambda: now["ms"])
    made = []
    for ms in (fixed + 5, fixed + 5, fixed + 1, fixed, fixed + 6, fixed + 6):
        now["ms"] = ms
        made.append(back.next().int)
    assert all(a < b for a, b in itertools.pairwise(made))
    # the module-level helper is monotonic too
    burst = [ids.uuid7().int for _ in range(2_000)]
    assert all(a < b for a, b in itertools.pairwise(burst))


def test_uuid7_rfc9562_layout() -> None:
    ids = api("ids")
    clock = 1_700_000_123_456
    gen = ids.Uuid7Generator(clock_ms=lambda: clock)
    value = gen.next()
    assert _timestamp_ms(value) == clock, "bits 0-47 are the Unix epoch milliseconds"
    assert (value.int >> 76) & 0xF == 0x7, "bits 48-51 are the version, 0b0111"
    assert (value.int >> 62) & 0x3 == 0b10, "bits 64-65 are the variant, 0b10"
    assert value.version == 7
    assert value.variant == uuid.RFC_4122
    # the shared generator reads the real clock
    import time

    before = int(time.time() * 1000)
    live = ids.uuid7()
    after = int(time.time() * 1000)
    assert before - 1 <= _timestamp_ms(live) <= after + 1_000
    assert live.version == 7
    assert live.variant == uuid.RFC_4122


def test_uuid7_layout_matches_stdlib_when_available() -> None:
    """Asserts on every Python: against uuid.uuid7() where it exists, and its absence otherwise,
    so the test is never skipped (ADR 0001 I15)."""
    ids = api("ids")
    ours = ids.uuid7()
    if hasattr(uuid, "uuid7"):
        theirs = uuid.uuid7()
        assert ours.version == theirs.version == 7
        assert ours.variant == theirs.variant
        assert abs(_timestamp_ms(ours) - _timestamp_ms(theirs)) < 5_000
    else:
        assert not hasattr(uuid, "uuid7")
        assert ours.version == 7
        assert ours.variant == uuid.RFC_4122


# ----------------------------------------------------------------------------- registry


def test_prefix_registry_unique() -> None:
    ids = api("ids")
    registry = ids.ID_PREFIXES
    assert len(registry) == len(set(registry))
    assert len(set(registry.values())) == len(registry), "two kinds share a prefix"
    for prefix in registry:
        assert re.fullmatch(r"[a-z]{2,6}", prefix), prefix
    classes = {prefix: _id_class(prefix) for prefix in registry}
    assert len(set(classes.values())) == len(classes), "one ID class per prefix"
    with pytest.raises(TypeError):
        registry["new"] = "Thing"  # the constant is read-only


def test_prefix_registry_frozen_matches_adr() -> None:
    ids = api("ids")
    assert dict(ids.ID_PREFIXES) == ADR_PREFIXES
    for prefix, entity in ADR_PREFIXES.items():
        assert _id_class(prefix).__name__ == f"{entity}Id"


def _levenshtein(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def test_new_prefixes_not_confusable() -> None:
    """P-03: a prefix added after the frozen table is >= 2 edits from every prefix, no anagrams."""
    ids = api("ids")
    errors = api("errors")
    registry = ids.ID_PREFIXES
    # accepted: far from everything and not an anagram
    for fine in ("qzx", "widget", "zzzz", "wbk"):
        assert fine not in registry
        assert ids.validate_new_prefix(fine) is None
    # rejected: one edit away from an existing prefix
    one_edit = {"ruu": "run", "pen": "per", "rbn": "run", "trn": "turn", "evk": "evl"}
    for candidate, near in one_edit.items():
        assert _levenshtein(candidate, near) == 1
        with pytest.raises(errors.ConfusablePrefixError) as caught:
            ids.validate_new_prefix(candidate)
        assert near in str(caught.value) or candidate in str(caught.value)
    # rejected: an anagram of an existing prefix, even at distance 2 (spn / snp)
    for candidate in ("snp", "nur", "esu", "tcs"):
        with pytest.raises(errors.ConfusablePrefixError):
            ids.validate_new_prefix(candidate)
    # rejected: already registered, or not a lowercase letter prefix
    for candidate in ("run", "pcat", "Run", "ru n", "ru_n"):
        with pytest.raises((errors.ConfusablePrefixError, ValueError)):
            ids.validate_new_prefix(candidate)
    # the check is against the registry passed in, defaulting to the frozen one
    assert ids.validate_new_prefix("ruu", existing={"abc": "Thing"}) is None


def test_frozen_registry_confusable_pairs_are_the_known_five() -> None:
    """Proposed name. ADR 0001 Decision 4/P-03 names three grandfathered one-edit pairs
    (run/rub, exc/exp, dts/dti), but the frozen table also contains agt/art (one edit) and
    tsc/cst (anagram). This pins the real set so a sixth pair cannot slip in; the ADR text must be
    corrected by staff-reviewer (see the QA report)."""
    pairs = {
        frozenset((a, b))
        for a, b in itertools.combinations(ADR_PREFIXES, 2)
        if _levenshtein(a, b) < 2 or sorted(a) == sorted(b)
    }
    assert pairs == {
        frozenset(p)
        for p in (("run", "rub"), ("exc", "exp"), ("dts", "dti"), ("agt", "art"), ("tsc", "cst"))
    }
    ids = api("ids")
    assert dict(ids.ID_PREFIXES) == ADR_PREFIXES


# ------------------------------------------------------------- the rule for new prefixes (I16)
#
# ADR 0001 Decision 4 and P-03 (revision 7): a prefix added after the freeze is 2-6 lowercase
# ASCII letters (^[a-z]{2,6}$), not registered, at Levenshtein distance >= 2 from every registered
# prefix, and not an anagram of one. The expected verdicts below are computed WITHOUT a distance
# routine: from the set of all strings within one edit of a prefix plus all its permutations.

LETTERS = "abcdefghijklmnopqrstuvwxyz"
PREFIX_SYNTAX = re.compile(r"[a-z]{2,6}")
FROZEN_CONFUSABLE_PREFIXES = {"run", "rub", "exc", "exp", "dts", "dti", "agt", "art", "tsc", "cst"}


def _within_one_edit(word: str) -> set[str]:
    out = {word}
    for i in range(len(word) + 1):
        out.update(word[:i] + ch + word[i:] for ch in LETTERS)  # insertion
    for i in range(len(word)):
        out.add(word[:i] + word[i + 1 :])  # deletion
        out.update(word[:i] + ch + word[i + 1 :] for ch in LETTERS)  # substitution
    return out


@functools.cache
def _confusable_with_frozen_table() -> frozenset[str]:
    forbidden: set[str] = set()
    for prefix in ADR_PREFIXES:
        forbidden |= _within_one_edit(prefix)
        forbidden |= {"".join(letters) for letters in itertools.permutations(prefix)}
    return frozenset(forbidden)


def _outcome(candidate: str) -> str:
    ids = api("ids")
    errors = api("errors")
    try:
        result = ids.validate_new_prefix(candidate)
    except errors.ConfusablePrefixError:
        return "confusable"
    except ValueError:
        return "value-error"
    assert result is None
    return "accepted"


def test_new_prefix_syntax_is_two_to_six_lowercase_letters() -> None:
    """Proposed name. ``^[a-z]{2,6}$``, as a full match (a trailing newline is not a letter)."""
    forbidden = _confusable_with_frozen_table()
    for fine in ("qz", "wbk", "zzzz", "qzxwv", "qzxwvu"):
        assert PREFIX_SYNTAX.fullmatch(fine), f"bad premise: {fine}"
        assert fine not in forbidden, f"bad premise: {fine}"
        assert _outcome(fine) == "accepted", fine
    for bad in (
        "",
        "q",
        "qzxwvut",  # 7 letters
        "qzxwvutsr",
        "Qzx",
        "qzX",
        "QZX",
        "qz1",
        "q_z",
        "q-z",
        "q z",
        " qz",
        "qz ",
        "qz\n",
        "qz\x00",
        "q\u00e9z",  # a non-ASCII letter
        "\u0915\u0916",  # Devanagari
        "\uff51\uff5a",  # fullwidth q z
    ):
        assert _outcome(bad) in ("confusable", "value-error"), repr(bad)
    # an empty registry makes every syntactically valid prefix acceptable, and only those
    ids = api("ids")
    errors = api("errors")
    assert ids.validate_new_prefix("run", existing={}) is None
    with pytest.raises((errors.ConfusablePrefixError, ValueError)):
        ids.validate_new_prefix("Run", existing={})


def test_new_prefix_distance_and_anagram_rule_matches_an_independent_enumeration() -> None:
    """Proposed name. Every string that is registered, within one edit of a registered prefix
    (insertion, deletion or substitution) or a permutation of one is rejected with
    ConfusablePrefixError; a seeded sample of everything else (including 2-edit neighbours of
    registered prefixes, the boundary) is accepted."""
    forbidden = _confusable_with_frozen_table()
    syntactic = sorted(c for c in forbidden if PREFIX_SYNTAX.fullmatch(c))
    assert len(syntactic) > 2_000, "the enumeration must be non-trivial"
    wrongly_accepted = [c for c in syntactic if _outcome(c) != "confusable"]
    assert not wrongly_accepted, f"confusable but not rejected: {wrongly_accepted[:15]}"

    rng = random.Random(20261022)  # noqa: S311 (a seeded sample, not cryptography)
    registered = sorted(ADR_PREFIXES)
    sample: set[str] = set()
    while len(sample) < 1_500:  # free-form prefixes
        sample.add("".join(rng.choice(LETTERS) for _ in range(rng.randint(2, 6))))
    while len(sample) < 3_000:  # two random edits away from a registered prefix: the boundary
        word = rng.choice(registered)
        for _ in range(2):
            kind = rng.choice(("insert", "delete", "substitute"))
            at = rng.randrange(len(word) + 1)
            if kind == "insert":
                word = word[:at] + rng.choice(LETTERS) + word[at:]
            elif kind == "delete" and len(word) > 2:
                word = word[:at] + word[at + 1 :]
            elif at < len(word):
                word = word[:at] + rng.choice(LETTERS) + word[at + 1 :]
        if PREFIX_SYNTAX.fullmatch(word):
            sample.add(word)
    mismatches = []
    for candidate in sorted(sample):
        expected = "confusable" if candidate in forbidden else "accepted"
        got = _outcome(candidate)
        if got != expected:
            mismatches.append((candidate, expected, got))
    assert not mismatches, f"verdicts differ from the rule: {mismatches[:15]}"
    assert any(c not in forbidden for c in sample if _levenshtein(c, "run") == 2)


def test_new_prefix_rejects_every_permutation_of_every_registered_prefix() -> None:
    """Proposed name. The anagram rule: every permutation of every registered prefix is rejected,
    including the many that are two or more edits from every registered prefix (so only the
    anagram rule catches them, for example ``rpj`` for ``prj``)."""
    errors = api("errors")
    ids = api("ids")
    only_anagram = []
    for prefix in ADR_PREFIXES:
        for letters in itertools.permutations(prefix):
            candidate = "".join(letters)
            with pytest.raises(errors.ConfusablePrefixError):
                ids.validate_new_prefix(candidate)
            if candidate != prefix and all(
                _levenshtein(candidate, other) >= 2 for other in ADR_PREFIXES
            ):
                only_anagram.append(candidate)
    assert {"rpj", "jpr", "atg"} <= set(only_anagram), "premise: these are anagram-only cases"
    assert len(only_anagram) >= 20


def test_validate_new_prefix_flags_exactly_the_five_frozen_pairs() -> None:
    """Proposed name. Each frozen prefix is offered as a 'new' prefix against the other 25: it is
    confusable exactly when it belongs to one of the five pairs run/rub, exc/exp, dts/dti,
    agt/art (one edit) and tsc/cst (anagram). Offered against the full table it is a duplicate."""
    ids = api("ids")
    errors = api("errors")
    frozen = dict(ids.ID_PREFIXES)
    flagged = set()
    for prefix in frozen:
        rest = {k: v for k, v in frozen.items() if k != prefix}
        try:
            ids.validate_new_prefix(prefix, existing=rest)
        except errors.ConfusablePrefixError:
            flagged.add(prefix)
        with pytest.raises(errors.ConfusablePrefixError):
            ids.validate_new_prefix(prefix)
        with pytest.raises(errors.ConfusablePrefixError) as caught:
            ids.validate_new_prefix(prefix, existing={prefix: frozen[prefix]})
        assert "registered" in str(caught.value), "an exact duplicate is 'already registered'"
    assert flagged == FROZEN_CONFUSABLE_PREFIXES


# ---------------------------------------------------------------------------- JSON Schema


def test_jsonschema_pattern_rejects_wrong_kind() -> None:
    jsonschema = typing.cast(Any, __import__("jsonschema"))
    ids = api("ids")
    run_schema = TypeAdapter(ids.RunId).json_schema()
    assert run_schema["type"] == "string"
    assert run_schema["pattern"] == RUN_PATTERN, "ADR 0001 Decision 3 spells this pattern out"
    for prefix in ADR_PREFIXES:
        schema = TypeAdapter(_id_class(prefix)).json_schema()
        validator = jsonschema.Draft202012Validator(schema)
        assert schema["pattern"].startswith(f"^{prefix}_")
        own = str(_id_class(prefix).new())
        assert validator.is_valid(own)
        for other in ADR_PREFIXES:
            if other != prefix:
                assert not validator.is_valid(str(_id_class(other).new())), (prefix, other)
        assert not validator.is_valid(own.upper())
        assert not validator.is_valid(own.split("_", 1)[1]), "bare UUID"
        assert not validator.is_valid(f"{prefix}_{uuid.uuid4()}"), "v4 behind the right prefix"


# ----------------------------------------------------------- whole-model walks (gate-bound)


def _annotation_leaves(annotation: Any) -> list[Any]:
    args = typing.get_args(annotation)
    if not args:
        return [annotation]
    leaves: list[Any] = []
    for arg in args:
        leaves.extend(_annotation_leaves(arg))
    return leaves


# `*_id` fields that are not references to our entities (ADR 0001 I12 says "every field named
# *_id", but KeyRef.key_id, ToolCallEvent.external_call_id and Run.caller_external_id are opaque
# external or key identifiers). The list is deliberately short; see the QA report.
NON_ENTITY_ID_FIELDS = {"key_id", "external_call_id", "caller_external_id"}


def test_every_id_reference_field_has_registered_prefix() -> None:
    registry = api("registry")
    ids = api("ids")
    for model in registry.model_classes():
        for name, info in model.model_fields.items():
            wanted = name.endswith("_id") or name.endswith("_ids") or name == "ref_id"
            if not wanted or name in NON_ENTITY_ID_FIELDS:
                continue
            leaves = [
                leaf for leaf in _annotation_leaves(info.annotation) if isinstance(leaf, type)
            ]
            typed: list[Any] = [leaf for leaf in leaves if issubclass(leaf, ids.TypedId)]
            assert typed, f"{model.__name__}.{name} is not a typed ID (found {leaves})"
            for leaf in typed:
                assert leaf.prefix in ids.ID_PREFIXES, f"{model.__name__}.{name}: {leaf}"


def test_every_entity_has_registered_prefix() -> None:
    registry = api("registry")
    ids = api("ids")
    base = api("base")
    entities = registry.entity_classes()
    assert len(ids.ID_PREFIXES) == len(entities) == 26  # `pce` withdrawn in revision 15 (N47)
    assert "PriceEntry" not in {cls.__name__ for cls in entities.values()}
    assert set(entities) == set(ids.ID_PREFIXES), (
        "every registered prefix needs exactly one EntityBase subclass and vice versa; "
        f"missing {sorted(set(ids.ID_PREFIXES) - set(entities))}, "
        f"unregistered {sorted(set(entities) - set(ids.ID_PREFIXES))}"
    )
    for prefix, cls in entities.items():
        assert issubclass(cls, base.EntityBase)
        assert cls.id_prefix == prefix
        assert cls.__name__ == ids.ID_PREFIXES[prefix]
    # no EntityBase subclass escapes the registry (test doubles live outside the package)
    stray = [
        m.__name__
        for m in registry.model_classes()
        if issubclass(m, base.EntityBase)
        and m not in (base.EntityBase, base.VersionedEntity)
        and m not in entities.values()
    ]
    assert not stray, f"EntityBase subclasses without a registered prefix: {stray}"


def _model_leaves(annotation: Any) -> list[Any]:
    """The classes named in an annotation (``X | None``, ``list[X]``, ``Annotated[X, ...]``)."""
    return [leaf for leaf in _annotation_leaves(annotation) if isinstance(leaf, type)]


def _typed_reference_target(model_name: str, model: Any) -> str | None:
    """``<Entity>`` if ``model`` is a value object named ``<Entity>Ref`` for a registered entity
    class whose ``id`` is typed exactly ``<Entity>Id``; otherwise ``None``."""
    ids = api("ids")
    prefix_of = {name: prefix for prefix, name in ids.ID_PREFIXES.items()}
    entity = model_name.removesuffix("Ref")
    if entity == model_name or entity not in prefix_of or "id" not in model.model_fields:
        return None
    typed = [
        leaf
        for leaf in _model_leaves(model.model_fields["id"].annotation)
        if issubclass(leaf, ids.TypedId)
    ]
    return entity if typed == [ids.id_type(prefix_of[entity])] else None


def test_value_objects_have_no_entity_fields() -> None:
    """Revision 7 (api-surface section 18 N1; ADR 0001 Decision 8 and I14), restated in revision 8
    (section 19.6 N6) as a rule.

    A value object carries no identity fields of its own. The only fields of value objects that
    share an entity field's name are: ``id`` of a ``<Entity>Ref`` typed ``<Entity>Id`` where
    ``<Entity>`` is a registered entity class name (``TestProfileRef``, ``PricingCatalogRef``,
    ``PersonaRef``, ``MetricDefinitionRef``, ``MetricBindingRef`` and later ones by the same rule);
    ``VersionedRef.id`` (a typed ID of any kind); and the ``schema_version`` of the nested model
    behind ``SealedPayload.aad_fields`` (the version of the sealed plaintext). A ``<Entity>Ref``
    whose ``id`` is not typed ``<Entity>Id`` is not an exception."""
    registry = api("registry")
    base = api("base")
    ids = api("ids")
    value_objects = registry.value_object_classes()
    required = dict.fromkeys([*VALUE_OBJECT_NAMES, *EXPECTED_TYPED_REFS])
    missing = [name for name in required if name not in value_objects]
    assert not missing, f"value objects not implemented yet: {missing}"

    sealed = value_objects["SealedPayload"]
    aad_models = [
        leaf
        for leaf in _model_leaves(sealed.model_fields["aad_fields"].annotation)
        if hasattr(leaf, "model_fields")
    ]
    assert len(aad_models) == 1, f"SealedPayload.aad_fields must be one nested model: {aad_models}"
    allowed = {GENERIC_REFERENCE, (aad_models[0].__name__, "schema_version")}
    allowed |= {
        (name, "id")
        for name, model in value_objects.items()
        if _typed_reference_target(name, model) is not None
    }
    for name in EXPECTED_TYPED_REFS:
        assert (name, "id") in allowed, f"{name}.id must be typed {name.removesuffix('Ref')}Id"
    assert len(allowed) >= 7

    present: set[tuple[str, str]] = set()
    for model in registry.model_classes():
        if issubclass(model, base.EntityBase) or model is base.EntityBase:
            continue
        present |= {(model.__name__, f) for f in ENTITY_FIELDS & set(model.model_fields)}
    unexpected = sorted(present - allowed)
    assert not unexpected, f"value objects with entity fields of their own: {unexpected}"
    assert present == allowed, f"all reference fields must exist: missing {allowed - present}"

    # the reference ids hold the typed ID of the referenced entity
    generic = _model_leaves(value_objects[GENERIC_REFERENCE[0]].model_fields["id"].annotation)
    assert [leaf for leaf in generic if issubclass(leaf, ids.TypedId)], "VersionedRef.id is typed"
    # the typed references of batch 2 narrow the generic one
    for name in NARROWED_VERSIONED_REFS:
        assert issubclass(value_objects[name], base.VersionedRef), name
        assert value_objects[name] is not base.VersionedRef

    for name in VALUE_OBJECT_NAMES:
        assert not issubclass(value_objects[name], base.EntityBase), name


# The opaque `*_id` fields: ADR 0001 I12 as amended in revision 7. A `*_id` field may be opaque
# only if its value is assigned by a system other than tracer and is stored verbatim; each is
# named here and adding one is an amendment to the ADR.
OPAQUE_ID_FIELDS = {
    ("KeyRef", "key_id"),
    ("ToolCallEvent", "external_call_id"),
    ("Run", "caller_external_id"),
}


def test_opaque_id_fields_are_exactly_the_adr_allowlist() -> None:
    """Proposed name. Every field named ``*_id``, ``*_ids`` or ``ref_id`` is a typed ID except
    exactly ``KeyRef.key_id``, ``ToolCallEvent.external_call_id`` and ``Run.caller_external_id``.
    ``SealedPayload.aad_fields.subject_id`` is typed. The allowlist is checked per model, so a
    second ``key_id`` somewhere else would be caught (the by-name skip in
    test_every_id_reference_field_has_registered_prefix would not)."""
    registry = api("registry")
    ids = api("ids")
    opaque: set[tuple[str, str]] = set()
    typed_seen: set[tuple[str, str]] = set()
    for model in registry.model_classes():
        for name, info in model.model_fields.items():
            if not (name.endswith(("_id", "_ids")) or name == "ref_id"):
                continue
            leaves = _model_leaves(info.annotation)
            if any(issubclass(leaf, ids.TypedId) for leaf in leaves):
                typed_seen.add((model.__name__, name))
            else:
                opaque.add((model.__name__, name))
    assert opaque == OPAQUE_ID_FIELDS, (
        f"opaque *_id fields: unexpected {sorted(opaque - OPAQUE_ID_FIELDS)}, "
        f"missing {sorted(OPAQUE_ID_FIELDS - opaque)}"
    )
    subject = [pair for pair in typed_seen if pair[1] == "subject_id"]
    assert subject, "SealedPayload.aad_fields.subject_id must exist and be a typed ID"
