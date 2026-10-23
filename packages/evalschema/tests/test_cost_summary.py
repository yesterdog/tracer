# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""P1-25, part 2: CostSummary and CostExclusion (ADR 0014 Decisions 16 and 16a, P-40, P-53, P-54).

Assumed public API (docs/phase1/api-surface.md section 23), ``tracer_evalschema.cost``: the entity
``CostSummary`` (``csum``, a record of fact: ``domain``, ``total?``, ``included``, ``excluded``,
``completeness``), the value object ``CostExclusion{ref, reason, covered_by}`` and three pure
functions: ``summarize_costs(records, *, domain, summary_id, created_at)`` builds the summary of
cost records (the precedence of P-54 lives here), ``check_cost_summary(summary, records)`` is the
bundle-level check of a summary against its records (N22), both raise ``ValueError`` with the
fragments named in section 23.2, and ``CostSummary`` itself checks the rules that need no records
(total present iff something is included, completeness, ``covered_by`` inside ``included``).

Names are SCOPE section 5 names unless the docstring says "Proposed name". A failure of these
tests is about the cost module only.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any

import pytest

from ._support import api
from ._support_b2 import assert_rejected, new_id, property_enum, registered_class, schema_of
from ._support_b4 import (
    EXCLUSION_REASONS,
    cost_class,
    cost_fn,
    make_catalog,
    record_computed_doc,
    record_na_doc,
    record_reported_doc,
    reported_raw_doc,
    summary_doc,
    usd,
    when,
)


def _uses(count: int) -> list[str]:
    return [new_id("use") for _ in range(count)]


def _make(doc: dict[str, Any]) -> Any:
    return cost_class("CostRecord").model_validate(doc)


def _computed(usage_ids: list[str], amount: str = "12.5", **over: Any) -> Any:
    return _make(
        record_computed_doc(make_catalog(), usage_record_ids=usage_ids, amount=usd(amount), **over)
    )


def _reported(usage_ids: list[str], amount: str = "11", by: str = "provider") -> Any:
    return _make(
        record_reported_doc(usage_record_ids=usage_ids, amount=usd(amount), reported_by=by)
    )


def _na(reason: str, usage_ids: list[str], **over: Any) -> Any:
    return _make(record_na_doc(reason, usage_record_ids=usage_ids, **over))


def _unsupported(usage_ids: list[str], amount_raw: str = "1000", label: str = "INR") -> Any:
    raw = reported_raw_doc(amount_raw=amount_raw, currency_raw=label)
    return _na("unsupported_currency", usage_ids, reported_raw=raw)


def _summarize(records: list[Any], domain: str = "evaluation") -> Any:
    return cost_fn("summarize_costs")(
        records,
        domain=domain,
        summary_id=api("ids").CostSummaryId.new(),
        created_at=when("2026-10-23T00:00:00Z"),
    )


def _check(summary: Any, records: list[Any]) -> None:
    cost_fn("check_cost_summary")(summary, records)


def _exclusion(ref: Any, reason: str, covered_by: list[Any] | None = None) -> dict[str, Any]:
    return {"ref": str(ref), "reason": reason, "covered_by": [str(c) for c in covered_by or []]}


def _summary(**over: Any) -> Any:
    return cost_class("CostSummary").model_validate(summary_doc(**over))


def _flatten(summary: Any) -> dict[str, Any]:
    dumped: dict[str, Any] = summary.model_dump(mode="json")
    return {
        "included": dumped["included"],
        "excluded": dumped["excluded"],
        "total": dumped["total"],
        "completeness": dumped["completeness"],
    }


def _raises(fragment: str | tuple[str, ...], call: Callable[[], object]) -> None:
    """``call`` raises a ``ValueError`` whose message holds one of the fragments (lower case)."""
    fragments = (fragment,) if isinstance(fragment, str) else fragment
    with pytest.raises(ValueError, match=r"(?s).+") as caught:
        call()
    text = str(caught.value).lower()
    assert any(f in text for f in fragments), (fragments, str(caught.value))


# ------------------------------------------------------------------------------- shapes


def test_cost_exclusion_shape_and_reasons() -> None:
    """Proposed name (ADR 0014 Decision 16a, ADR 0003 Decision 1, ADR 0001 Decision 8):
    ``CostExclusion`` is a value object with exactly ``ref`` (a ``cst_`` ID), ``reason`` and
    ``covered_by`` (``cst_`` IDs). ``reason`` is the record's ``na_reason`` (ten reasons since
    revision 16: ``amount_unparseable`` N59 and ``currency_conflict``), ``duplicate_of_reported``
    or (revision 15, N53, detail D8) ``overlaps_reported``: twelve members, an ``enum``.
    ``duplicate_of_reported`` needs a non-empty ``covered_by`` (rejected at ``covered_by``); the
    other reasons accept both."""
    model = registered_class("CostExclusion")
    assert set(model.model_fields) == {"ref", "reason", "covered_by"}
    assert sorted(property_enum(model, "reason")) == sorted(EXCLUSION_REASONS)
    assert len(property_enum(model, "reason")) == 12
    ref, other = new_id("cst"), new_id("cst")
    for reason in EXCLUSION_REASONS:
        if reason != "duplicate_of_reported":
            assert model.model_validate({"ref": ref, "reason": reason, "covered_by": []})
            assert model.model_validate({"ref": ref, "reason": reason, "covered_by": [other]})
    assert model.model_validate(
        {"ref": ref, "reason": "duplicate_of_reported", "covered_by": [other]}
    )
    assert_rejected(
        model, {"ref": ref, "reason": "duplicate_of_reported", "covered_by": []}, field="covered_by"
    )
    for bad in ("estimated", "No_Usage", "", None, "covered"):
        assert_rejected(model, {"ref": ref, "reason": bad, "covered_by": []}, field="reason")
    ids = api("ids")
    for bad_ref in (str(ids.RunId.new()), str(ids.CostRecordId.new().uuid), "", None):
        assert_rejected(
            model, {"ref": bad_ref, "reason": "no_usage", "covered_by": []}, field="ref"
        )
    assert_rejected(
        model,
        {"ref": ref, "reason": "no_usage", "covered_by": [str(ids.UsageRecordId.new())]},
        field="covered_by",
    )
    assert_rejected(model, {"ref": ref, "reason": "no_usage", "covered_by": [], "note": "x"})
    for forbidden in ("id", "schema_version", "created_at", "metadata"):
        assert forbidden not in model.model_fields


def test_covered_by_repeated_ids_rejected() -> None:
    """N78 (owner auto-approved, api-surface 23.10): ``CostExclusion.covered_by`` lists distinct
    records. A repeated ID is a ``ValidationError`` located at ``covered_by`` whatever the reason
    (``duplicate_of_reported`` included), adjacent or not, with the repeat among other IDs, and
    also when the exclusion sits inside a ``CostSummary`` (located at ``excluded``). Distinct IDs,
    one ID and the empty list stay valid, and the order of distinct IDs is kept."""
    model = registered_class("CostExclusion")
    ref, a, b, c = (new_id("cst") for _ in range(4))
    for reason in EXCLUSION_REASONS:
        assert_rejected(
            model, {"ref": ref, "reason": reason, "covered_by": [a, a]}, field="covered_by"
        )
    for repeated in ([a, b, a], [a, b, b], [a, a, b], [a, b, c, b], [a, b, c, a, b, c]):
        assert_rejected(
            model,
            {"ref": ref, "reason": "unsupported_currency", "covered_by": repeated},
            field="covered_by",
        )
    for ok in ([], [a], [a, b], [b, a], [a, b, c]):
        loaded = model.model_validate(
            {"ref": ref, "reason": "unsupported_currency", "covered_by": ok}
        )
        assert [str(i) for i in loaded.covered_by] == ok
    assert model.model_validate(
        {"ref": ref, "reason": "duplicate_of_reported", "covered_by": [a, b]}
    )
    cover = new_id("cst")
    assert_rejected(
        cost_class("CostSummary"),
        summary_doc(
            included=[cover],
            excluded=[_exclusion(ref, "unsupported_currency", [cover, cover])],
            total=usd("5"),
        ),
        field="excluded",
    )


def test_cost_summary_shape_and_enums() -> None:
    """Proposed name (ADR 0014 Decision 16, ADR 0003 Decision 1): a summary is an entity
    (``csum``) with exactly the entity fields plus ``domain``, ``total``, ``included``,
    ``excluded`` and ``completeness``. There is one ``total``, a USD ``Money`` (no per-currency
    ``totals`` list). ``domain`` is the two-member enum and ``completeness`` is exactly complete,
    partial or none. ``included`` holds distinct ``cst_`` IDs, ``excluded`` distinct
    ``CostExclusion`` objects, and a record is not in both; ``covered_by`` may name only records of
    ``included``. All rejected at the field."""
    model = cost_class("CostSummary")
    entity_fields = {"id", "schema_version", "created_at", "metadata", "metadata_flags"}
    assert set(model.model_fields) == entity_fields | {
        "domain",
        "total",
        "included",
        "excluded",
        "completeness",
    }
    assert "totals" not in model.model_fields
    assert {"domain", "completeness"} <= set(schema_of(model)["required"])
    assert "total" not in schema_of(model)["required"]
    assert sorted(property_enum(model, "completeness")) == ["complete", "none", "partial"]
    assert sorted(property_enum(model, "domain")) == ["agent_under_test", "evaluation"]
    for bad in ("full", "Complete", "", None, True):
        assert_rejected(model, summary_doc(completeness=bad), field="completeness")
    for bad in ("agent", "", None):
        assert_rejected(model, summary_doc(domain=bad), field="domain")
    assert _summary().total.currency == "USD"
    for bad_total in (usd("12.5") | {"currency": "EUR"}, "12.5", 12.5, [usd("12.5")]):
        assert_rejected(model, summary_doc(total=bad_total), field="total")
    one = new_id("cst")
    assert_rejected(model, summary_doc(included=[one, one]), field="included")
    assert_rejected(model, summary_doc(included=["not-an-id"]), field="included")
    assert_rejected(model, summary_doc(included=[new_id("use")]), field="included")
    gone = new_id("cst")
    assert_rejected(
        model,
        summary_doc(
            excluded=[_exclusion(gone, "no_usage"), _exclusion(gone, "no_price_for_model")]
        ),
        field="excluded",
    )
    assert_rejected(
        model, summary_doc(included=[one], excluded=[_exclusion(one, "no_usage")]), field="excluded"
    )
    assert_rejected(model, summary_doc(excluded=["cst_x"]), field="excluded")
    assert_rejected(
        model,
        summary_doc(included=[one], excluded=[_exclusion(gone, "no_usage", [new_id("cst")])]),
        field="excluded",
    )
    assert _summary(
        included=[one], excluded=[_exclusion(gone, "no_usage", [one])], completeness="complete"
    )
    assert_rejected(model, summary_doc(totals=[usd("1")]))


# --------------------------------------------------------------------- total and completeness


def test_cost_summary_total_absent_when_none_included() -> None:
    """ADR 0014 Decision 16, I16, P-53, A-14: ``total`` is present if and only if at least one
    record is included. A summary with nothing included has no total, never a total of "0" (which
    would read as free) and never any other amount, rejected at ``total``; an included record
    without a total is rejected too. Nothing included means completeness ``none``. Summaries built
    from records follow the same rule, also for no records and for only not-available records."""
    model = cost_class("CostSummary")
    gone = new_id("cst")
    empty = _summary(included=[], excluded=[], total=None, completeness="none")
    assert empty.total is None
    assert empty.included == []
    assert json.loads(empty.model_dump_json())["total"] is None
    only_excluded = _summary(
        included=[],
        excluded=[_exclusion(gone, "no_price_for_model")],
        total=None,
        completeness="none",
    )
    assert only_excluded.total is None
    for bad_total in (usd("0"), usd("12.5")):
        assert_rejected(
            model, summary_doc(included=[], total=bad_total, completeness="none"), field="total"
        )
    assert_rejected(model, summary_doc(total=None), field="total")
    doc = summary_doc()
    del doc["total"]
    assert_rejected(model, doc, field="total")
    for bad in ("partial", "complete"):
        assert_rejected(
            model, summary_doc(included=[], total=None, completeness=bad), field="completeness"
        )
    assert_rejected(model, summary_doc(completeness="none"), field="completeness")
    none_records = _summarize([])
    assert none_records.total is None
    assert none_records.completeness == "none"
    assert none_records.included == []
    assert none_records.excluded == []
    only_na = _summarize([_na("no_price_for_model", _uses(1)), _na("no_usage", [])])
    assert only_na.total is None
    assert only_na.completeness == "none"
    assert only_na.included == []
    assert len(only_na.excluded) == 2
    assert json.loads(only_na.model_dump_json())["total"] is None


@pytest.mark.parametrize(
    ("included", "covered_flags", "expected"),
    [
        (0, [], "none"),
        (0, [False], "none"),
        (0, [False, False], "none"),
        (1, [], "complete"),
        (2, [], "complete"),
        (1, [False], "partial"),
        (1, [True], "complete"),
        (2, [True, True], "complete"),
        (1, [True, False], "partial"),
        (1, [False, False], "partial"),
        (2, [False, True, True], "partial"),
    ],
)
def test_completeness_rules(included: int, covered_flags: list[bool], expected: str) -> None:
    """ADR 0014 Decision 16, P-40 and Decision 16a (refined by P-54): ``complete`` iff at least one
    record is included and no exclusion is uncovered (an exclusion is covered when its
    ``covered_by`` is non-empty); ``none`` iff nothing is included; ``partial`` otherwise. The
    summary alone decides it: the stored value must equal the computed one, and either other value
    is rejected at ``completeness``."""
    model = cost_class("CostSummary")
    included_ids = [new_id("cst") for _ in range(included)]
    excluded = [
        _exclusion(new_id("cst"), "unsupported_currency", included_ids[:1] if covered else [])
        for covered in covered_flags
    ]
    base: dict[str, Any] = {
        "included": included_ids,
        "excluded": excluded,
        "total": usd("12.5") if included else None,
    }
    assert model.model_validate(summary_doc(**base, completeness=expected)).completeness == expected
    for other in {"complete", "partial", "none"} - {expected}:
        assert_rejected(model, summary_doc(**base, completeness=other), field="completeness")


# ------------------------------------------------------------------------------- totals


def test_cost_summary_single_usd_total_with_exclusions() -> None:
    """ADR 0014 Decision 16, I16: the summary has one USD ``total`` equal to the exact sum of the
    amounts of the included records, and lists every excluded record with its reason (including
    unsupported_currency and currency_not_reported). The sum is exact (0.1 + 0.2 is 0.3, 18-digit
    amounts add without loss). ``check_cost_summary`` rejects a total that differs in the last
    digit. Included and excluded keep the order of the input records."""
    first = _computed(_uses(1), "0.18518505")
    second = _computed(_uses(1), "0.5")
    no_price = _na("no_price_for_model", _uses(1))
    foreign = _unsupported(_uses(1))
    silent = _na(
        "currency_not_reported",
        _uses(1),
        reported_raw={"reported_by": "provider", "amount_raw": "9", "currency_source": "none"},
    )
    records = [first, no_price, second, foreign, silent]
    summary = _summarize(records)
    assert [str(i) for i in summary.included] == [str(first.id), str(second.id)]
    assert [(str(e.ref), e.reason) for e in summary.excluded] == [
        (str(no_price.id), "no_price_for_model"),
        (str(foreign.id), "unsupported_currency"),
        (str(silent.id), "currency_not_reported"),
    ]
    assert all(e.covered_by == [] for e in summary.excluded)
    assert summary.total.amount == "0.68518505"
    assert summary.total.currency == "USD"
    assert summary.completeness == "partial"
    assert summary.domain == "evaluation"
    assert isinstance(summary.total, api("primitives").Money)
    _check(summary, records)
    tampered = _summary(**{**_flatten(summary), "total": usd("0.68518506"), "domain": "evaluation"})
    _raises("total", lambda: _check(tampered, records))
    exact = _summarize([_computed(_uses(1), "0.1"), _computed(_uses(1), "0.2")])
    assert exact.total.amount == "0.3"
    assert exact.completeness == "complete"
    fine = _summarize(
        [_computed(_uses(1), "0.000000000000000001"), _computed(_uses(1), "0.999999999999999999")]
    )
    assert fine.total.amount == "1"
    many = _summarize([_computed(_uses(1), "0.000000000000000001") for _ in range(3)])
    assert many.total.amount == "0.000000000000000003"
    zero = _summarize([_computed(_uses(1), "0")])
    assert zero.total is not None
    assert zero.total.amount == "0"
    assert zero.completeness == "complete"
    assert [str(i) for i in _summarize(list(reversed(records))).included] == [
        str(second.id),
        str(first.id),
    ]


# ------------------------------------------------------------------------------ P-54


def test_usage_record_in_at_most_one_included_cost() -> None:
    """ADR 0014 Decision 16a, I27, P-54: within one summary each ``UsageRecord`` ID appears in the
    ``usage_record_ids`` of at most one included record. ``check_cost_summary`` rejects a summary
    that includes two records sharing a usage (also a partial overlap), and ``summarize_costs``
    refuses two available records of the same basis for one usage instead of choosing (the schema
    has no tie-break). Records with disjoint usage, and one record with several usages, are
    fine."""
    shared, other, third = _uses(3)
    a = _computed([shared])
    b = _computed([shared], "3")
    both = _summary(included=[str(a.id), str(b.id)], total=usd("15.5"))
    _raises("usage", lambda: _check(both, [a, b]))
    overlap_a = _computed([shared, other], "1")
    overlap_b = _computed([other, third], "2")
    partial = _summary(included=[str(overlap_a.id), str(overlap_b.id)], total=usd("3"))
    _raises("usage", lambda: _check(partial, [overlap_a, overlap_b]))
    _raises("usage", lambda: _summarize([a, b]))
    two_reported = [_reported([shared]), _reported([shared], "4")]
    _raises("usage", lambda: _summarize(two_reported))
    disjoint_a, disjoint_b = _computed(_uses(1), "1"), _computed(_uses(1), "2")
    summary = _summarize([disjoint_a, disjoint_b])
    assert summary.total.amount == "3"
    _check(summary, [disjoint_a, disjoint_b])
    many = _computed(_uses(3), "4")
    assert _summarize([many]).total.amount == "4"
    _check(_summarize([many]), [many])


def test_reported_non_usd_and_computed_usd_same_usage_counted_once() -> None:
    """ADR 0014 Decision 16a, I27, P-54, the golden example: one usage ``u1``; record A is the
    provider's own report "1000" labelled INR (not_available, unsupported_currency, the raw values
    kept verbatim); record B is computed "12.5" USD from a USD catalog entry. The summary includes
    B only, its total is "12.5", A is excluded with reason unsupported_currency and ``covered_by``
    naming B, and the summary is complete (no usage is missing from the total). The same holds for
    a reported cost without a currency, and for either input order."""
    (u1,) = _uses(1)
    unsupported = _unsupported([u1], "1000", "INR")
    silent = _na(
        "currency_not_reported",
        [u1],
        reported_raw={"reported_by": "provider", "amount_raw": "1000", "currency_source": "none"},
    )
    for a in (unsupported, silent):
        b = _computed([u1], "12.5")
        for records in ([a, b], [b, a]):
            summary = _summarize(records)
            dumped = _flatten(summary)
            assert dumped["included"] == [str(b.id)]
            assert dumped["total"] == usd("12.5")
            assert dumped["excluded"] == [
                {"ref": str(a.id), "reason": a.na_reason, "covered_by": [str(b.id)]}
            ]
            assert dumped["completeness"] == "complete"
            _check(summary, records)
        assert a.reported_raw.amount_raw == "1000"
        assert a.amount is None
    golden = _summarize([_unsupported([u1], "1000", "INR"), _computed([u1], "12.5")])
    assert golden.excluded[0].reason == "unsupported_currency"


def test_available_reported_preferred_over_computed_duplicate() -> None:
    """ADR 0014 Decision 16a, I27, P-54: when an available reported USD record and an available
    computed record cover the same usage, the reported one is included and the computed one is
    excluded with reason ``duplicate_of_reported`` and ``covered_by`` naming the reported record;
    the total is the reported amount and the summary is complete. A telemetry-reported record
    counts like a provider-reported one. ``check_cost_summary`` rejects the reverse choice, a
    summary that includes the computed record and omits or sets aside the reported one, and a
    summary that includes both."""
    (u1,) = _uses(1)
    for by in ("provider", "telemetry_platform"):
        reported = _reported([u1], "11", by)
        computed = _computed([u1], "12.5")
        for records in ([reported, computed], [computed, reported]):
            summary = _summarize(records)
            assert _flatten(summary) == {
                "included": [str(reported.id)],
                "excluded": [_exclusion(computed.id, "duplicate_of_reported", [reported.id])],
                "total": usd("11"),
                "completeness": "complete",
            }
            _check(summary, records)
    reported, computed = _reported([u1], "11"), _computed([u1], "12.5")
    records = [reported, computed]
    flipped = _summary(
        included=[str(computed.id)],
        excluded=[_exclusion(reported.id, "duplicate_of_reported", [computed.id])],
        total=usd("12.5"),
    )
    _raises("reported", lambda: _check(flipped, records))
    omitted = _summary(included=[str(computed.id)], excluded=[], total=usd("12.5"))
    _raises(("reported", "excluded"), lambda: _check(omitted, records))
    including_both = _summary(included=[str(reported.id), str(computed.id)], total=usd("23.5"))
    _raises("usage", lambda: _check(including_both, records))
    twin = _computed([u1], "13")
    fake_duplicate = _summary(
        included=[str(twin.id)],
        excluded=[_exclusion(computed.id, "duplicate_of_reported", [twin.id])],
        total=usd("13"),
    )
    _raises("reported", lambda: _check(fake_duplicate, [computed, twin]))
    wrong_cover = _summary(
        included=[str(reported.id)],
        excluded=[_exclusion(computed.id, "no_usage", [reported.id])],
        total=usd("11"),
    )
    _raises(("reason", "available", "duplicate"), lambda: _check(wrong_cover, records))


def test_cost_exclusion_covered_by_names_included_records_only() -> None:
    """ADR 0014 Decision 16a, I27, P-54: ``covered_by`` lists the included records of the same
    summary that cover the excluded record's usage. It is non-empty if and only if every usage of
    the excluded record is covered by included records, and every listed record is included in
    the same summary and shares at least one usage with the excluded record. The summary model
    rejects a listed record that is not included (at ``excluded``); ``check_cost_summary`` rejects
    a listed record that shares no usage, a claim of coverage with usage left over, and an empty
    list for a fully covered record. Several included records can cover one excluded record."""
    u1, u2, u9 = _uses(3)
    excluded = _unsupported([u1])
    cover = _computed([u1], "5")
    other = _computed([u9], "6")
    outsider = new_id("cst")
    model = cost_class("CostSummary")
    assert_rejected(
        model,
        summary_doc(
            included=[str(cover.id)],
            excluded=[_exclusion(excluded.id, "unsupported_currency", [outsider])],
            total=usd("5"),
        ),
        field="excluded",
    )
    no_overlap = _summary(
        included=[str(cover.id), str(other.id)],
        excluded=[_exclusion(excluded.id, "unsupported_currency", [other.id])],
        total=usd("11"),
    )
    _raises("covered_by", lambda: _check(no_overlap, [excluded, cover, other]))
    wide = _unsupported([u1, u2])
    leftover = _summary(
        included=[str(cover.id)],
        excluded=[_exclusion(wide.id, "unsupported_currency", [cover.id])],
        total=usd("5"),
        completeness="complete",
    )
    _raises("covered_by", lambda: _check(leftover, [wide, cover]))
    forgotten = _summary(
        included=[str(cover.id)],
        excluded=[_exclusion(excluded.id, "unsupported_currency", [])],
        total=usd("5"),
        completeness="partial",
    )
    _raises("covered_by", lambda: _check(forgotten, [excluded, cover]))
    right = _summary(
        included=[str(cover.id)],
        excluded=[_exclusion(excluded.id, "unsupported_currency", [cover.id])],
        total=usd("5"),
    )
    _check(right, [excluded, cover])
    left, right_half = _computed([u1], "2"), _computed([u2], "3")
    unrelated = _computed([u9], "4")
    both_halves = _summarize([wide, left, unrelated, right_half])
    covered = [str(c) for c in both_halves.excluded[0].covered_by]
    assert covered == [str(left.id), str(right_half.id)]
    assert both_halves.completeness == "complete"
    assert both_halves.total.amount == "9"
    _check(both_halves, [wide, left, unrelated, right_half])


def test_completeness_ignores_covered_exclusions() -> None:
    """ADR 0014 Decision 16a, I27, P-40: an exclusion whose usage is fully covered by included
    records does not make a summary partial, because its usage is already in the total. An
    exclusion that is only partly covered, or not covered at all, is uncovered and gives
    ``partial``, and a partial sum is never reported for it (its ``covered_by`` is empty)."""
    u1, u2, u3 = _uses(3)
    covered = _unsupported([u1])
    cover = _computed([u1], "7")
    assert _summarize([covered, cover]).completeness == "complete"
    partly = _unsupported([u1, u2])
    result = _summarize([partly, cover])
    assert result.completeness == "partial"
    assert result.excluded[0].covered_by == []
    _check(result, [partly, cover])
    uncovered = _na("no_price_for_model", [u3])
    result = _summarize([cover, uncovered])
    assert result.completeness == "partial"
    assert result.total.amount == "7"
    mixed = _summarize([covered, cover, uncovered])
    assert mixed.completeness == "partial"
    by_ref = {str(e.ref): e for e in mixed.excluded}
    assert [str(c) for c in by_ref[str(covered.id)].covered_by] == [str(cover.id)]
    assert by_ref[str(uncovered.id)].covered_by == []
    no_usage = _na("no_usage", [])
    with_none = _summarize([cover, no_usage])
    assert with_none.completeness == "partial"
    assert with_none.excluded[0].covered_by == []


def test_check_cost_summary_other_rules() -> None:
    """Proposed name (ADR 0014 Decision 16, ADR 0009 Decision 5 and N22: cross-record rules are
    bundle-level checks): ``check_cost_summary`` rejects, with a ``ValueError`` whose message holds
    the fragment in brackets, a record of another domain [domain], an included record that is not
    available [available], an ID that is not among the supplied records [record], an exclusion
    whose reason is not the record's ``na_reason`` [reason], an available record that is neither
    included nor excluded and a not-available one likewise [excluded], and a stored total that is
    not the exact sum [total]. ``summarize_costs`` refuses a record of another domain [domain].
    P readings, section 23.3 N53."""
    a = _computed(_uses(1), "1")
    b = _computed(_uses(1), "2")
    n = _na("no_price_for_model", _uses(1))
    good = _summarize([a, b, n])
    _check(good, [a, b, n])
    agent = _make(
        record_reported_doc(domain="agent_under_test", usage_record_ids=_uses(1), amount=usd("1"))
    )
    mixed = _summary(included=[str(a.id), str(agent.id)], total=usd("2"))
    _raises("domain", lambda: _check(mixed, [a, agent]))
    _raises("domain", lambda: _summarize([a, agent]))
    other_domain = _summarize([agent], domain="agent_under_test")
    assert other_domain.domain == "agent_under_test"
    assert other_domain.total.amount == "1"
    included_na = _summary(included=[str(a.id), str(n.id)], total=usd("1"))
    _raises("available", lambda: _check(included_na, [a, n]))
    _raises("record", lambda: _check(good, [a, n]))
    wrong_reason = _summary(
        included=[str(a.id), str(b.id)],
        excluded=[_exclusion(n.id, "no_usage")],
        total=usd("3"),
        completeness="partial",
    )
    _raises("reason", lambda: _check(wrong_reason, [a, b, n]))
    unaccounted_na = _summary(
        included=[str(a.id), str(b.id)], total=usd("3"), completeness="complete"
    )
    _raises("excluded", lambda: _check(unaccounted_na, [a, b, n]))
    unaccounted_available = _summary(included=[str(a.id)], total=usd("1"))
    _raises("excluded", lambda: _check(unaccounted_available, [a, b]))
    bad_total = _summary(
        included=[str(a.id), str(b.id)],
        excluded=[_exclusion(n.id, "no_price_for_model")],
        total=usd("3.1"),
        completeness="partial",
    )
    _raises("total", lambda: _check(bad_total, [a, b, n]))


# --------------------------------------------------------------- N53 and its remainder


def test_computed_partially_overlapping_reported_excluded_not_split() -> None:
    """N53 (owner, option A; ADR 0014 Decision 16b; api-surface 23.6 group E item 1): usage ``u1``,
    ``u2``; an available reported record ``R`` on ``u1``; an available computed record ``C`` on
    ``u1`` and ``u2``. ``summarize_costs`` includes ``R`` only (total is ``R``'s amount, never a
    split of ``C``) and excludes ``C`` whole with reason ``overlaps_reported`` and an empty
    ``covered_by``; the summary is partial, in either input order. Two reported records that
    cover only part of ``C`` give the same. ``check_cost_summary`` accepts such a summary and
    rejects ``overlaps_reported`` on a fully covered computed record (that is
    ``duplicate_of_reported``), on a computed record that shares no usage with an included reported
    record, and on a reported or a not-available record. A computed record that overlaps only a
    not-available reported record is not an overlap case. Proposed meaning of the fragments: the
    message names the reason, the overlap, the duplicate or ``covered_by``."""
    u1, u2, u3, u9 = _uses(4)
    reported = _reported([u1], "11")
    computed = _computed([u1, u2], "12.5")
    for records in ([reported, computed], [computed, reported]):
        summary = _summarize(records)
        assert _flatten(summary) == {
            "included": [str(reported.id)],
            "excluded": [_exclusion(computed.id, "overlaps_reported", [])],
            "total": usd("11"),
            "completeness": "partial",
        }
        _check(summary, records)
    second = _reported([u2], "4")
    wide = _computed([u1, u2, u3], "20")
    result = _summarize([reported, second, wide])
    assert [str(i) for i in result.included] == [str(reported.id), str(second.id)]
    assert [(e.reason, e.covered_by) for e in result.excluded] == [("overlaps_reported", [])]
    assert result.total.amount == "15"
    assert result.completeness == "partial"
    _check(result, [reported, second, wide])
    unsupported_report = _unsupported([u1, u2])
    not_overlap = _summarize([unsupported_report, computed])
    assert [e.reason for e in not_overlap.excluded] == ["unsupported_currency"]
    assert not_overlap.completeness == "complete"
    fragments = ("reason", "overlap", "duplicate", "covered_by", "available")
    full = _computed([u1], "7")
    stray = _computed([u9], "8")
    na = _na("no_price_for_model", [u1, u2])
    other_reported = _reported([u9], "3")
    wide_reported = _reported([u1, u2], "5")
    bad_summaries = [
        (
            _summary(
                included=[str(reported.id)],
                excluded=[_exclusion(computed.id, "overlaps_reported", [reported.id])],
                total=usd("11"),
            ),
            [reported, computed],
        ),
        (
            _summary(
                included=[str(reported.id)],
                excluded=[_exclusion(wide_reported.id, "overlaps_reported", [])],
                total=usd("11"),
                completeness="partial",
            ),
            [reported, wide_reported],
        ),
        (
            _summary(
                included=[str(reported.id)],
                excluded=[_exclusion(full.id, "overlaps_reported", [])],
                total=usd("11"),
                completeness="partial",
            ),
            [reported, full],
        ),
        (
            _summary(
                included=[str(reported.id)],
                excluded=[_exclusion(full.id, "overlaps_reported", [reported.id])],
                total=usd("11"),
            ),
            [reported, full],
        ),
        (
            _summary(
                included=[str(reported.id)],
                excluded=[_exclusion(stray.id, "overlaps_reported", [])],
                total=usd("11"),
                completeness="partial",
            ),
            [reported, stray],
        ),
        (
            _summary(
                included=[str(reported.id)],
                excluded=[_exclusion(other_reported.id, "overlaps_reported", [])],
                total=usd("11"),
                completeness="partial",
            ),
            [reported, other_reported],
        ),
        (
            _summary(
                included=[str(reported.id)],
                excluded=[_exclusion(na.id, "overlaps_reported", [])],
                total=usd("11"),
                completeness="partial",
            ),
            [reported, na],
        ),
    ]
    for stored, records in bad_summaries:
        _raises(fragments, functools.partial(_check, stored, records))
    wrong_reason = _summary(
        included=[str(reported.id)],
        excluded=[_exclusion(computed.id, "no_usage", [])],
        total=usd("11"),
        completeness="partial",
    )
    _raises(fragments, lambda: _check(wrong_reason, [reported, computed]))


def test_record_without_usage_never_covered_and_input_order_kept() -> None:
    """N53 remainder (owner, accepted; ADR 0014 Decision 16b; api-surface 23.6 group S): (i) an
    available reported record ``R`` on ``u1`` and a not-available ``no_usage`` record ``N``
    without usage: ``summarize_costs`` excludes ``N`` with ``covered_by == []`` and gives
    ``partial``, and ``check_cost_summary`` rejects a stored summary in which ``N``'s
    ``covered_by`` names ``R`` (fragment ``covered_by``). (ii) An available reported record
    ``A0`` without usage and an available computed record ``C`` on ``u1`` are both included and the
    total is their exact sum (the documented residual risk of N51: no double count is
    detected). (iii) For two permutations of the same records ``included`` and ``excluded``
    follow the input order exactly (never sorted), and ``check_cost_summary`` accepts a stored
    summary whose ``included``, ``excluded`` and ``covered_by`` lists are permuted."""
    u1, u2, u3 = _uses(3)
    reported = _reported([u1], "11")
    nothing = _na("no_usage", [])
    summary = _summarize([reported, nothing])
    assert [(str(e.ref), e.reason, e.covered_by) for e in summary.excluded] == [
        (str(nothing.id), "no_usage", [])
    ]
    assert summary.completeness == "partial"
    _check(summary, [reported, nothing])
    claims = _summary(
        included=[str(reported.id)],
        excluded=[_exclusion(nothing.id, "no_usage", [reported.id])],
        total=usd("11"),
        completeness="complete",
    )
    _raises("covered_by", lambda: _check(claims, [reported, nothing]))
    free_reported = _reported([], "2")
    computed = _computed([u1], "3")
    for records in ([free_reported, computed], [computed, free_reported]):
        both = _summarize(records)
        assert [str(i) for i in both.included] == [str(r.id) for r in records]
        assert both.total.amount == "5"
        assert both.completeness == "complete"
        assert both.excluded == []
        _check(both, records)
    wide = _unsupported([u1, u2])
    left, right = _computed([u1], "2"), _computed([u2], "3")
    unrelated = _computed([u3], "4")
    missing = _na("no_price_for_model", _uses(1))
    silent = _na("no_usage", [])
    records = [missing, left, wide, unrelated, silent, right]
    forward = _summarize(records)
    assert [str(i) for i in forward.included] == [str(r.id) for r in (left, unrelated, right)]
    assert [str(e.ref) for e in forward.excluded] == [str(r.id) for r in (missing, wide, silent)]
    wide_cover = next(e for e in forward.excluded if str(e.ref) == str(wide.id))
    assert [str(c) for c in wide_cover.covered_by] == [str(left.id), str(right.id)]
    _check(forward, records)
    backward = _summarize(list(reversed(records)))
    assert [str(i) for i in backward.included] == [str(r.id) for r in (right, unrelated, left)]
    assert [str(e.ref) for e in backward.excluded] == [str(r.id) for r in (silent, wide, missing)]
    back_cover = next(e for e in backward.excluded if str(e.ref) == str(wide.id))
    assert [str(c) for c in back_cover.covered_by] == [str(right.id), str(left.id)]
    _check(backward, list(reversed(records)))
    dumped = _flatten(forward)
    permuted = _summary(
        included=list(reversed(dumped["included"])),
        excluded=list(reversed(dumped["excluded"])),
        total=dumped["total"],
        completeness=dumped["completeness"],
    )
    _check(permuted, records)  # included reversed, covered_by kept in its own order
    other_way = _summary(
        included=dumped["included"],
        excluded=[{**e, "covered_by": list(reversed(e["covered_by"]))} for e in dumped["excluded"]],
        total=dumped["total"],
        completeness=dumped["completeness"],
    )
    _check(other_way, records)  # covered_by reversed, included kept
