# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""``tracer judge``: re-judge a stored judged run, K times, on its fixed answers (ADR 0030 rule 21).

Each repeat is one rescoring bundle of the same run (ADR 0022 rule 5): it shares every record of
the call and carries its own judge results and judge usage. The snapshot fixes the definitions, so
the engine's model must be the definitions' model. Repeats measure the judge, not the agent: the
answers do not change. Caps and exchange files belong to one repeat.
"""

from __future__ import annotations

import argparse
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tracer_cli import judgeplan, store
from tracer_cli.cli import Environment, Refusal, _read_text, _write_recovery
from tracer_cli.judgesummary import judge_lines, judge_profile_of, plan_lines
from tracer_cli.simsummary import stage_usage_text
from tracer_evalcore.judge import JudgeContext
from tracer_evalcore.judgelabels import JudgedItem, judged_items
from tracer_evalcore.judgerecords import JUDGE_KEY_PREFIX, JudgeSetup, with_judge_results
from tracer_evalcore.judgerun import check_golden_against_bundle, judge_bundle
from tracer_evalcore.simoutcome import SIM_OUTCOME_METADATA_KEY, authorisation_reference_sha256
from tracer_evalproviders.cost import compute_usage_cost
from tracer_evalproviders.judgeexchange import (
    ExchangeStore,
    JudgeExchangeModeWarning,
    exchange_directory,
)
from tracer_evalproviders.simcalllog import SimCallLog
from tracer_evalschema.bundle import RunBundle
from tracer_evalschema.ids import RunBundleId

__all__ = ["load_judged_bundle", "run_judge"]


class _Ledger:
    def __init__(self) -> None:
        self.records: list[Any] = []

    def add(self, record: Any) -> None:
        self.records.append(record)


def load_judged_bundle(out_dir: Path, bundle_id: str) -> RunBundle:
    store_file = out_dir / store.STORE_FILE_NAME
    if not store_file.is_file() or store_file.is_symlink():
        raise Refusal("store_not_found")
    try:
        parsed = RunBundleId.parse(bundle_id)
    except ValueError:
        raise Refusal("bundle_id_invalid") from None
    engine = store.open_sqlite_store(out_dir)
    try:
        try:
            return store.load_run(engine, parsed)
        except Exception:
            raise Refusal("bundle_not_found") from None
    finally:
        engine.dispose()


def _key(item: JudgedItem) -> tuple[Any, ...]:
    return (item.status, item.verdict, item.reason_code, item.facts, item.error_code)


def _rescoring_bundle(
    original: RunBundle,
    definitions: list[Any],
    done: Any,
    new_id: RunBundleId,
    created_at: datetime,
) -> RunBundle:
    """The original's records except its judge results and judge usage, plus this repeat's."""
    judge_ids = {d.id for d in definitions}
    kept = [r for r in original.results if r.metric.id not in judge_ids]
    shared_usage = [u for u in original.usage_records if u.stage != "judge"]
    cost = compute_usage_cost([*shared_usage, *done.usage], catalog=None, created_at=created_at)
    base = original.model_copy(
        update={
            "id": new_id,
            "created_at": created_at,
            "results": kept,
            "usage_records": shared_usage,
            "cost_records": list(cost.records),
            "cost_summaries": [cost.summary],
        }
    )
    return with_judge_results(base, done.results, done.usage)


def run_judge(args: argparse.Namespace, environment: Environment) -> int:
    moment = environment.clock()
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("the clock must return an aware datetime")
    created_at = moment.astimezone(UTC)
    out_dir = Path(args.out)

    def read(path: str, role: str) -> str:
        return _read_text(path, 1_048_576, role)

    inputs = judgeplan.domain_errors(lambda: judgeplan.parse_inputs(args, read))
    original = load_judged_bundle(out_dir, args.bundle)
    profile_doc = judge_profile_of(original)
    definitions = [
        d for d in original.snapshot.metric_definitions if d.key.startswith(JUDGE_KEY_PREFIX)
    ]
    if profile_doc is None or not definitions:
        raise Refusal("run_has_no_judge_definitions")
    config = inputs.config
    for definition in definitions:
        ref = definition.judge_model_ref
        if ref is None or ref.provider != config.profile or ref.model != config.params.model:
            raise Refusal("judge_model_differs")
    if profile_doc["golden_set"]["sha256"] != inputs.golden.sha256:
        raise Refusal("golden_set_differs")
    judgeplan.domain_errors(lambda: check_golden_against_bundle(inputs.golden, original))
    if args.dry_run:
        modes: dict[str, int] = {}
        for item in inputs.golden.items:
            modes[item.match_mode] = modes.get(item.match_mode, 0) + 1
        lines = plan_lines(
            engine=inputs.engine,
            model=config.params.model,
            golden_key=inputs.golden.key,
            modes=modes,
            max_tokens=config.params.max_tokens,
            max_requests=args.max_judge_requests,
            max_tokens_total=args.max_judge_tokens,
        )
        lines.append(f"judge repeats: {args.repeats}")
        lines.append("dry run: nothing was sent")
        environment.stdout.write("".join(f"{line}\n" for line in lines))
        return 0

    entry = judgeplan.domain_errors(
        lambda: judgeplan.check_allowlist(
            args, inputs, resolver=getattr(environment.sim, "judge_resolver", None)
        )
    )
    reference = judgeplan.domain_errors(lambda: judgeplan.check_reference(args, inputs))
    judgeplan.domain_errors(lambda: judgeplan.check_caps(args, inputs))
    bound = judgeplan.bind_key(
        entry,
        environ=environment.environ,
        env_file=None if args.env_file is None else Path(args.env_file),
    )
    setup = JudgeSetup(
        golden=inputs.golden,
        model_ref={"provider": config.profile, "model": config.params.model},
        profile_document=profile_doc,
    )
    call_end_ms = original.run.metadata[SIM_OUTCOME_METADATA_KEY]["connected_ms"] or 0
    original_items = judged_items(original)
    out = environment.stdout
    out.write(f"rescoring of: {original.id}\n")
    out.write(f"repeats: {args.repeats}\n")
    exit_code = 0
    try:
        call_log = SimCallLog.open(out_dir, clock=environment.clock)
    except OSError as error:
        raise Refusal(f"output_unavailable ({type(error).__name__})") from None
    ledger_all: list[Any] = []
    try:
        for repeat in range(1, args.repeats + 1):
            new_id = RunBundleId.new()
            ledger = _Ledger()
            exchanges = None
            if inputs.kind == "profile":
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    exchanges = ExchangeStore(exchange_directory(out_dir, new_id))
                for notice in caught:
                    if issubclass(notice.category, JudgeExchangeModeWarning):
                        environment.stderr.write(f"warning: {notice.message}\n")
            engine = judgeplan.build_engine(
                args,
                inputs,
                bound,
                call_log=call_log,
                exchanges=exchanges,
                transport=environment.transport,
                sleep=environment.sleep,
            )
            failed_step = "judge"
            try:
                try:
                    done = judge_bundle(
                        bundle=original,
                        setup=setup,
                        engine=engine,
                        params=config.params,
                        context=JudgeContext(run_id=original.run.id, created_at=created_at),  # type: ignore[arg-type]
                        call_end_ms=call_end_ms,
                        on_usage=ledger.add,
                        created_at=created_at,
                    )
                finally:
                    close = getattr(engine, "close", None)
                    if callable(close):
                        close()
                    ledger_all.extend(ledger.records)
                failed_step = "store"
                rescored = _rescoring_bundle(original, definitions, done, new_id, created_at)
                engine_db = store.open_sqlite_store(out_dir)
                try:
                    store.store_rescoring(engine_db, rescored)
                finally:
                    engine_db.dispose()
            except BaseException as error:
                path = _write_recovery(
                    out_dir,
                    created_at,
                    failed_step=failed_step,
                    error=error,
                    usage=ledger_all,
                    bundle=None,
                )
                if ledger_all:
                    out.write(f"usage recovery: {path if path is not None else 'not written'}\n")
                raise
            out.write(f"repeat {repeat} of {args.repeats}: {rescored.id}\n")
            for label, value in judge_lines(rescored):
                out.write(f"{label}: {value}\n")
            out.write(f"judge usage: {stage_usage_text(rescored, 'judge')}\n")
            changed = sum(
                1
                for before, after in zip(original_items, judged_items(rescored), strict=True)
                if _key(before) != _key(after)
            )
            out.write(
                f"judge changed from the stored run: {changed} of {len(original_items)} items\n"
            )
            if any(r.status == "error" for r in done.results):
                exit_code = 2
    finally:
        call_log.close()
    out.write(f"store: {out_dir / store.STORE_FILE_NAME}\n")
    if reference is not None:
        out.write(
            f"judge authorisation reference: sha256 {authorisation_reference_sha256(reference)}\n"
        )
    return exit_code
