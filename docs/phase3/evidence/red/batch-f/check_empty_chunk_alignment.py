# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
# Independent check of the empty-chunk expectation of the synthetic Marathi pair (batch F).
# Usage (repository root):  uv run --locked python -I <this file> <data dir>
# The alignment below is a second implementation of the rule of ADR 0023 (cost pair (edits,
# deletions+insertions), backtrace from the end preferring match, substitution, deletion,
# insertion). It does not import tracer_evalcore.align or .spans; only the public tokeniser gives
# the comparison keys. It prints what the README section "Empty-chunk variant" argues by hand and
# exits 1 on any disagreement with expected_results.json.
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from tracer_evalcore.tokens import TextTurn, tokenise_turns

DATA = Path(sys.argv[1])
expected = json.loads((DATA / "expected_results.json").read_text("ascii"))
lines = (DATA / "export_synthetic_mr.txt").read_text("utf-8").splitlines()[2:]
turns = [re.match(r"^\[[^\]]+\] (?:assistant|user): (.*)$", line).group(1) for line in lines]
bodies = [json.loads((DATA / "stt_empty_chunk" / f"stt_chunk_0{i}.json").read_text("ascii")) for i in range(3)]
stt_turn_texts = [b["transcript"] for b in bodies if b["transcript"].strip()]

export = tokenise_turns([TextTurn(seq=i, text=t) for i, t in enumerate(turns)], "mr-Deva/1")
stt = tokenise_turns([TextTurn(seq=i, text=t) for i, t in enumerate(stt_turn_texts)], "mr-Deva/1")
a, b = list(export.keys), list(stt.keys)
n, m = len(a), len(b)


def add(x, y):
    return (x[0] + y[0], x[1] + y[1])


D = [[(0, 0)] * (m + 1) for _ in range(n + 1)]
for i in range(1, n + 1):
    D[i][0] = (i, i)
for j in range(1, m + 1):
    D[0][j] = (j, j)
for i in range(1, n + 1):
    for j in range(1, m + 1):
        options = [add(D[i - 1][j], (1, 1)), add(D[i][j - 1], (1, 1))]
        options.append(D[i - 1][j - 1] if a[i - 1] == b[j - 1] else add(D[i - 1][j - 1], (1, 0)))
        D[i][j] = min(options)
ops = []
i, j = n, m
while i or j:
    here = D[i][j]
    if i and j and a[i - 1] == b[j - 1] and D[i - 1][j - 1] == here:
        ops.append(("M", i - 1, j - 1)); i, j = i - 1, j - 1
    elif i and j and a[i - 1] != b[j - 1] and add(D[i - 1][j - 1], (1, 0)) == here:
        ops.append(("S", i - 1, j - 1)); i, j = i - 1, j - 1
    elif i and add(D[i - 1][j], (1, 1)) == here:
        ops.append(("D", i - 1, None)); i -= 1
    else:
        ops.append(("I", None, j - 1)); j -= 1
ops.reverse()
want = expected["variants"]["empty_chunk"]
counts = {k: sum(1 for o in ops if o[0] == k) for k in "MSDI"}
problems = []
if (counts["M"], counts["S"], counts["D"], counts["I"]) != tuple(want["alignment"][k] for k in ("hits", "substitutions", "deletions", "insertions")):
    problems.append(f"counts {counts}")
print("minimum cost pair (edits, indels):", D[n][m], "counts:", counts)

tail = [("thik", "\u0920\u0940\u0915"), ("aahe", "\u0906\u0939\u0947"), ("mi", "\u092e\u0940"), ("thaamb", "\u0925\u093e\u0902\u092c")]
stretch = range(42, 88)
for label, word in tail:
    print(f"export occurrences of {label} in tokens 42..87:", [k for k in stretch if a[k] == word])
print("stt tail keys (tokens 41..44):", b[41:45])
chosen = [o for o in ops if o[2] in (41, 42, 43, 44)]
print("chosen pairs for stt tokens 41..44:", chosen)
if [o[1] for o in chosen] != [63, 64, 68, 87]:
    problems.append(f"chosen pairs {chosen}")
hand = [("M", 42, 41), ("M", 43, 42), ("M", 44, 43), ("S", 87, 44)]
outside = [o for o in ops if not (o[1] is not None and 42 <= o[1] <= 87) and o[2] not in (41, 42, 43, 44)]
alt = outside + hand + [("D", k, None) for k in range(45, 87)]
cost = lambda ops_: (sum(1 for o in ops_ if o[0] != "M"), sum(1 for o in ops_ if o[0] in "DI"))
print("cost of the chosen alignment:", cost(ops), " cost of the hand-predicted alignment:", cost(alt))
if cost(ops) != cost(alt):
    problems.append("the hand alignment does not tie")

runs, last = [], -2
for pos, op in enumerate(ops):
    if op[0] == "M":
        continue
    if runs and pos == last + 1:
        runs[-1].append(op)
    else:
        runs.append([op])
    last = pos
spans = []
for run in runs:
    toks = [export.tokens[o[1]] for o in run if o[1] is not None]
    for seq in sorted({t.turn_seq for t in toks}):
        mine = [t for t in toks if t.turn_seq == seq]
        spans.append((seq, min(t.start for t in mine), max(t.end for t in mine), len(mine)))
exp_spans = [(s["turn_seq"], s["start"], s["end"], s["export_tokens"]) for s in want["spans"] if s["export_tokens"]]
print("spans with export tokens:", spans)
if spans != exp_spans:
    problems.append(f"spans {spans} != {exp_spans}")
print("DISAGREEMENT:" if problems else "agrees with expected_results.json (empty_chunk)", *problems, sep="\n  " if problems else " ")
sys.exit(1 if problems else 0)
