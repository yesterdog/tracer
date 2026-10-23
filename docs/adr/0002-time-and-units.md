# ADR 0002: Time, durations and numeric units

- Status: Accepted (SCOPE.md P1-04b and Entity specs; revision 3: timeline origin and `wall_time` accepted by the user). P-04 decided by the user in revision 5 (accepted as recommended). Revision 7: input edge cases of `UtcDatetime` fixed by schema-architect (batch 1 item 7); the importer-side precision question is escalated to the user (E3 in `docs/phase1/api-surface.md` section 16). Revision 8: the rule for tightened `*_ms` fields (Decision 2, batch 2 item 6). Revision 11: the JSON form of `TimelineOrigin.ref` and where the transcript-level rule is checked (Decision 3, batch 3 findings N21 and N39). Revision 15: `UsageRecord.occurred_at` (Decision 4a, N43 decided by the owner). Open [Proposed] items: none.
- Date: 2026-10-22 (revision 5: P-04 folded into Decision 1)
- Task: P1-00a (implemented by P1-04b, P1-10b, P1-10c)

## Context
Voice evaluation needs two kinds of time. **Wall-clock instants** record when a run, turn or price took effect. **Relative offsets and durations** record turn boundaries in audio, time to first byte (TTFB), and latency. Sources disagree on format: Pipecat turn events may carry naive ISO timestamps (timezone not verified), F8 lines carry `+00:00` timestamps whose meaning (start, end or log time) is unknown while the file name uses local IST, and providers bill audio in fractional seconds. A float silently loses precision, a naive timestamp silently takes on the reader's timezone, and an offset without a stated origin cannot be compared with another offset. All three are guesses.

## Decision
1. **`UtcDatetime`** is the only wall-clock type. It must be timezone-aware on input. Any offset is converted to UTC, and the value serializes with a `Z` suffix. Naive datetimes are rejected by the type. **Canonical form (P-04):** it always serializes as `YYYY-MM-DDTHH:MM:SS.ffffffZ` with exactly 6 fractional digits, so one instant has one spelling and one digest; `:60` leap seconds are rejected (Python `datetime` cannot represent them, so accepting them would silently change the value). **Other inputs (revision 7):** a string with more than 6 fractional digits is rejected, never truncated or rounded; JSON numbers and Python `int`/`float` are rejected, so no epoch value is ever read as an instant (pydantic accepts them by default, so the type must refuse them explicitly); `bool` and `datetime.date` objects that are not `datetime` instances are rejected, because a date is not an instant and choosing midnight would be an assumption. All rejections apply in Python and JSON mode.
2. **`*_ms` fields** (`start_ms`, `end_ms`, `duration_ms`, `ttfb_ms`, `offset_ms`) are JSON integers >= 0, in milliseconds. They are relative offsets or durations, never wall-clock values. Floats are rejected. **Tightened fields (revision 8, batch 2 item 6):** a `*_ms` property always carries the `Ms` base schema `{"type": "integer", "minimum": 0}` unchanged. A field that needs stricter bounds may only add keywords (`exclusiveMinimum`, `maximum`) and never replaces the base. The first such field is `Evaluator.max_duration_ms`: `{"type": "integer", "minimum": 0, "exclusiveMinimum": 0, "maximum": 14400000}`, which accepts 1 ms to 4 h (checked with jsonschema 4.26.0, Draft 2020-12). I10 and its walk therefore stay exact.
3. **Timeline origin (accepted in revision 3).** Every offset states its origin through a `TimelineOrigin{kind: artifact|telemetry|observer, ref?, started_at?: UtcDatetime}` value object:
   - `Transcript.timeline_origin` is required if and only if any Turn or Segment of that transcript has a `start_ms` or `end_ms`. All offsets in the transcript use it.
   - `ToolCallEvent.timeline_origin` is required if and only if `offset_ms` is present.
   - `kind=artifact` means sample 0 of the artifact named by `ref` (an `art_` ID, required for this kind). `kind=telemetry` and `kind=observer` mean the pipeline start reported by that source; `ref` then holds the source's trace or session reference as an ExternalRef when the source provides one, and `started_at` holds the wall-clock start when the source provides it.
   - `ref` is one field (revision 11, `docs/phase1/api-surface.md` section 22.3 N39): in JSON an `art_` ID is a string and an `ExternalRef` an object; the member is chosen by the input's JSON type, not by the order of a union. The transcript-level rule is checked with the transcript's turns by `check_transcript` (ADR 0011 Decision 1), because a Transcript does not embed its turns (N21).
   - `EvidenceRef` with `kind=artifact_range` is measured from sample 0 of the referenced artifact by definition and needs no separate origin.
   - Two offsets are comparable only if their origins are equal. Code that compares offsets from different origins must convert through `started_at` (both present) or report the comparison as not possible.
4. **Wall time on a Turn (accepted in revision 3).** `Turn.started_at` is removed. A wall-clock instant attached to a turn is `Turn.wall_time: WallTime{at: UtcDatetime, meaning: start|end|logged|unknown}`. `meaning` records what the source asserts the instant is; a source that does not say uses `unknown`. `wall_time` is an annotation, not timing: it never sets `timing_source` and is never used to synthesize `start_ms`/`end_ms` (ADR 0011).
4a. **Time of a usage event (revision 15, N43 decided by the owner, option A).** `UsageRecord.occurred_at: UtcDatetime?` is the instant of the billable event as the source reports it (or as our runner records a call it made). It is absent when the source does not state it and is never filled with the ingest time, the run's start or "now". It selects the price date (ADR 0014 Decisions 8 and 15).
5. **Billable quantities** that are not whole counts (audio seconds) are `DecimalString` (ADR 0014), >= 0 (revision 16, N45 (a) decided by the owner: no usage quantity is negative). Token and request counts are integers >= 0. Seconds used for billing and milliseconds used for timing are different types on purpose.
6. **Ingestion of bad time values never crashes and never guesses.** An adapter that sees a naive timestamp leaves the field `None` and appends an IngestWarning with the raw value. It never assumes UTC or local time. The same rule applies, for now, to a source instant finer than a microsecond (for example OTLP `*UnixNano`). Whether importers may instead truncate it to microseconds with a warning that keeps the raw value is escalated to the user (E3, decide before P1-29). A file name's local time is never used as the content timezone.
7. **Metric units** are numeric units without currency (ADR 0010). Money is never a metric unit.

## Rejected alternatives
- **Accept naive datetimes and assume UTC.** This guesses the timezone. A wrong assumption shifts every latency computed against telemetry. Rejected under the no-fabrication rule (ADR 0018).
- **Store local time plus an offset.** Comparing and sorting requires normalizing anyway, and a timezone label is not needed to evaluate anything. UTC plus `Z` is unambiguous.
- **Epoch floats for instants.** Float precision varies with magnitude, epoch numbers cannot be read in exports, and the unit (s or ms) is ambiguous.
- **Float seconds for offsets.** Floats do not round-trip exactly through every JSON implementation, and they invite unit confusion. Integer ms is exact and enough for conversational latency.
- **Microsecond integers for offsets.** False precision: audio alignment and telemetry are not accurate below 1 ms.
- **One time type for both billing and timing.** Billing needs exact decimals with rounding increments; timing needs integer arithmetic. Merging them forces one domain to accept the other's weakness.
- **An implicit, run-wide origin for all offsets.** Our STT transcript is aligned to our recording while an agent transcript is timed by the agent's pipeline; the two zero points differ by an unknown amount. An implicit shared origin would make cross-source latency look precise when it is not.
- **Keeping `Turn.started_at`.** For F8 the single per-line timestamp may be a start, an end or a log time. Storing it as `started_at` asserts a start time, which is a guessed value under ADR 0018.

## Consequences
- Every adapter must find the source timezone or record a warning. F7 and F8 tests exercise this.
- Every adapter that emits offsets must also emit the origin, or leave the offsets absent.
- Phase 2 storage: `timestamptz` (Postgres) and an ISO text column in the canonical form (SQLite). Integer ms columns are `bigint`.

## Invariants (testable)
- I1. A naive datetime is rejected by `UtcDatetime`. Test: `test_utc_rejects_naive`.
- I2. `2026-10-22T10:00:00+05:30` serializes as `2026-10-22T04:30:00...Z`. Test: `test_utc_normalizes_offset_to_Z`.
- I3. A naive timestamp from an ingested source becomes an ingest warning, not an exception and not an assumed timezone. Test: `test_f7_naive_timestamp_warning_not_crash`.
- I4. The F8 per-line timestamp is preserved as `wall_time{meaning: unknown}` and never used to synthesize `start_ms`/`end_ms`. Test: `test_f8_single_timestamp_is_wall_time_timing_none`.
- I5. Durations and TTFB on spans are non-negative. Test: `test_stage_span_nonneg`.
- I6. `end_ms >= start_ms` when both are present. Test: `test_turn_end_ge_start`.
- I7. Billable seconds are non-negative decimal strings (revision 16, N45 (a)); token counts are non-negative integers. Tests: `test_audio_seconds_decimal`, `test_token_counts_int_nonneg`, `test_fractional_tokens_rejected`.
- I8. `retain_until` is a UTC datetime. Test: `test_retain_until_utc`.
- I9. Every `*_ms` field rejects `1.5` and `-1`. Test: `test_ms_fields_reject_float_and_negative`.
- I10. Every JSON Schema property ending in `_ms` has `"type": "integer"` and `"minimum": 0`. Test: `test_ms_field_names_are_integers_schema` (C).
- I11. `timeline_origin` is present iff offsets are present (Transcript and ToolCallEvent). Test: `test_timeline_origin_required_iff_offsets_present`.
- I12. `kind=artifact` requires an `art_` ref. Test: `test_timeline_origin_artifact_requires_artifact_ref`.
- I13. Turn has no `started_at`; `WallTime.meaning` is exactly start|end|logged|unknown. Tests: `test_turn_has_no_started_at_field`, `test_all_closed_enums_exact`.
- I14. UTC values serialize with exactly 6 fractional digits and leap seconds are rejected. Tests: `test_utc_serialization_canonical_six_digits`, `test_utc_rejects_leap_second`.
- I15. More than 6 fractional digits, numbers (epoch values), booleans and date-only objects are rejected (revision 7). Test: `test_utc_rejects_excess_precision_numbers_and_dates`.

## Decided items (formerly [Proposed]; all decided by the user in revision 5)
- **P-04. Canonical UTC serialization and leap seconds. Decided by the user (revision 5): accepted as recommended.** Decision: `UtcDatetime` always serializes as `YYYY-MM-DDTHH:MM:SS.ffffffZ` (exactly 6 fractional digits), and `:60` leap seconds are rejected. Reason: JCS digests (ADR 0006) and byte-identical exports need one spelling per instant; without a fixed width, `10:00:00Z` and `10:00:00.000Z` are the same instant with different digests. Python `datetime` cannot represent `:60`, so accepting it would silently change the value. Had it been decided otherwise: the serializer must still pick one canonical width (any fixed choice works) or digests become input-dependent; tests `test_utc_serialization_canonical_six_digits` and `test_utc_rejects_leap_second` change their expected values accordingly.
