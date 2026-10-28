# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""``tracer labels``: the human-label loop of the judge (ADR 0030 rule 22).

``packet`` writes a blind packet (what a person needs to label each item, never the judge's
verdict); ``check`` validates a labels file and keeps a content-addressed private copy;
``agreement`` prints the counts of the judge against the labels (and of two labellers against each
other). Files are local, private (0600 in a 0700 directory) and never overwritten. Counts only.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import stat
from pathlib import Path

from tracer_cli import judgeplan
from tracer_cli.cli import Environment, Refusal, _read_text
from tracer_cli.judgecmd import load_judged_bundle
from tracer_cli.judgesummary import judge_profile_of
from tracer_evalcore.golden import parse_golden_set
from tracer_evalcore.judgelabels import (
    MAX_LABELS_BYTES,
    JudgeLabelsError,
    agreement_counts,
    agreement_lines,
    build_packet,
    judged_items,
    parse_judge_labels,
)
from tracer_evalcore.judgerun import check_golden_against_bundle
from tracer_evalcore.simoutcome import SIM_OUTCOME_METADATA_KEY

__all__ = ["LABELS_DIR_NAME", "run_labels"]

LABELS_DIR_NAME = "labels"


def _private_dir(out_dir: Path) -> Path:
    directory = out_dir / LABELS_DIR_NAME
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(FileExistsError):
        os.mkdir(directory, 0o700)
    info = os.lstat(directory)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise Refusal("labels_directory_not_private")
    if info.st_mode & 0o077:
        os.chmod(directory, 0o700)
    return directory


def _write_new(path: Path, data: bytes) -> bool:
    """Write a new private file; ``False`` if the same bytes are already there."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        info = os.lstat(path)
        if stat.S_ISREG(info.st_mode) and path.read_bytes() == data:
            return False
        raise Refusal("labels_file_exists") from None
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    return True


def _packet(args: argparse.Namespace, environment: Environment) -> int:
    bundle = load_judged_bundle(Path(args.out), args.bundle)
    profile = judge_profile_of(bundle)
    if profile is None or not judged_items(bundle):
        raise Refusal("run_has_no_judge_definitions")
    golden = judgeplan.domain_errors(
        lambda: parse_golden_set(_read_text(args.golden, 1_048_576, "golden set"))
    )
    if profile["golden_set"]["sha256"] != golden.sha256:
        raise Refusal("golden_set_differs")
    judgeplan.domain_errors(lambda: check_golden_against_bundle(golden, bundle))
    call_end = bundle.run.metadata[SIM_OUTCOME_METADATA_KEY]["connected_ms"] or 0
    packet = build_packet(bundle, golden, call_end_ms=call_end)
    directory = _private_dir(Path(args.out))
    path = directory / f"packet-{bundle.id}.json"
    data = (json.dumps(packet, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise Refusal("packet_exists") from None
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    environment.stdout.write(f"packet: {path}\n")
    environment.stdout.write(f"items: {len(packet['items'])}\n")
    environment.stdout.write("blind: the judge's verdict is not in the packet\n")
    return 0


def _check(args: argparse.Namespace, environment: Environment) -> int:
    text = _read_text(args.labels, MAX_LABELS_BYTES, "labels")
    document = judgeplan.domain_errors(lambda: parse_judge_labels(text))
    bundle = load_judged_bundle(Path(args.out), document["bundle_id"])
    known = {i.item_id for i in judged_items(bundle)}
    if not known:
        raise Refusal("run_has_no_judge_definitions")
    if any(label["item_id"] not in known for label in document["labels"]):
        raise Refusal("judge_labels_item_unknown")
    data = text.encode("utf-8")
    directory = _private_dir(Path(args.out))
    path = directory / f"labels-{hashlib.sha256(data).hexdigest()}.json"
    created = _write_new(path, data)
    total = len(document["labels"])
    labelled = sum(1 for label in document["labels"] if label["verdict"] is not None)
    out = environment.stdout
    out.write("labels: ok\n")
    out.write(f"bundle: {document['bundle_id']}\n")
    out.write(f"labeller: {document['labeller']}\n")
    out.write(f"labelled: {labelled} of {total} items\n")
    out.write(f"unlabelled: {total - labelled} of {total} items\n")
    out.write(f"stored: {path}{'' if created else ' (already stored)'}\n")
    return 0


def _read_private(path: Path) -> str:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise Refusal("labels_file_not_private")
    return _read_text(str(path), MAX_LABELS_BYTES, "labels")


def _agreement(args: argparse.Namespace, environment: Environment) -> int:
    bundle = load_judged_bundle(Path(args.out), args.bundle)
    items = judged_items(bundle)
    if not items:
        raise Refusal("run_has_no_judge_definitions")
    directory = Path(args.out) / LABELS_DIR_NAME
    found = []
    if directory.is_dir():
        for path in sorted(directory.glob("labels-*.json")):
            document = judgeplan.domain_errors(lambda p=path: parse_judge_labels(_read_private(p)))  # type: ignore[misc]
            if document["bundle_id"] == str(bundle.id):
                found.append(document)
    if not found:
        raise Refusal("labels_none_for_bundle")
    found.sort(key=lambda d: d["labeller"])
    try:
        report = agreement_counts(str(bundle.id), items, found)
    except JudgeLabelsError as error:
        raise Refusal(str(error)) from None
    environment.stdout.write("".join(f"{line}\n" for line in agreement_lines(report)))
    return 0


def run_labels(args: argparse.Namespace, environment: Environment) -> int:
    if args.labels_command == "packet":
        return _packet(args, environment)
    if args.labels_command == "check":
        return _check(args, environment)
    return _agreement(args, environment)
