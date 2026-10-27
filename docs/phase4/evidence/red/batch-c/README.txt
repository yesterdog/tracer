Phase 4 batch C (P4-13, P4-14, P4-15): the caller brain. Evidence of red-first work.
Contract: docs/phase4/api-surface-batch-c.md. Author: qa, 2026-10-27.

What was run, where
-------------------
* Mirror: `git archive` of the committed tree d940404 (so no other agent's untracked staging, no caches, no private
  fixture) unpacked in a scratch directory, `git init`ed and committed there so that the tests that read git state
  can run; the staged files are then `git add`ed, as the dev's tree will be. One environment per Python version
  (3.11.16, 3.12.14, 3.13.15, 3.14.7; `uv sync --locked --all-groups --offline`, one at a time). The staged files were placed at their final paths and formatted there (ruff format,
  ruff check, mypy --strict all clean); the formatted copies are what is staged.
* "red": the staged tests on the CURRENT tree: the three new modules do not exist.
* "oracle": the same tests on the current tree plus three minimal modules (oracle-src.patch):
  tracer_evalcore/callerline.py, prompt.py, brains.py. The oracle is a starting point for the dev, not a spec.
* The sim_mr.json fixture of P4-00f and the new caller_prompt_1.txt were copied to packages/evalcore/tests/fixtures/
  in both mirrors (the dev moves them there, contract section 0).
* Logs are `pytest -q --tb=line -rfE`, scrubbed of home, scratch and temporary paths (`<home>`, `<scratch>`,
  `<tmp>`, `claude-user`, `pytest-of-user`). No log is over 5000 lines. All files here are ASCII.

Files
-----
red.py3.11.log red.py3.12.log red.py3.13.run1.log red.py3.13.run2.log red.py3.14.log
    The 7 new test files (200 items) and the 10 ADR reader files that read the pending sets (191 items, with the
    staged evalcore_phase4_adr_support.py): 199 failed, 192 passed, 1 skipped on every version (3.13 twice,
    identical). The 199 are all new tests; the 192 passes are the 191 ADR reader tests (which pass because the
    ten pending names are now defined, and fail the other way round if the support file is not replaced) and 1 new
    guard that holds on any tree (test_the_reference_oracle_itself_has_teeth). The skip is the opt-in gate.
red-reasons.py3.13.txt
    One line per failing test with its reason: ModuleNotFoundError for tracer_evalcore.prompt (118),
    tracer_evalcore.brains (44) and tracer_evalcore.callerline (37). No failure is an import error of the test itself,
    a missing fixture or a syntax error.
oracle.py3.11.log oracle.py3.12.log oracle.py3.13.log oracle.py3.14.log
    The same 17 files on the oracle: 391 passed, 1 skipped on every version.
oracle-full-suite.py3.<v>.log
    The whole workspace on the oracle (testpaths of pyproject.toml, as `uv run pytest` runs it): 13160 passed,
    1 skipped (the opt-in gate), 1288 deselected by the markers of pyproject.toml, 0 failed, on 3.11, 3.12, 3.13, 3.14.
mutants.txt, oracle-mutants-def.py
    163 mutants of the oracle (Python 3.13, a fresh PYTHONPYCACHEPREFIX each); 149 caught, 14 equivalent with a
    stated reason, 0 survived. The header says which survivors of the first run led to test additions.
oracle-src.patch
    The three oracle modules as a patch against the current tree.
spec-change.diff
    The one named spec change S-C (contract section 10): evalcore_phase4_adr_support.py loses 24 lines (16 names of
    the ten SCOPE tests, 8 comment headers that became empty); nothing is added.
staging/
    tests/evalcore (8 files), tests/cli (1 file), data/caller_prompt_1.txt. Nothing under packages/.

Commands (after the move of contract section 0)
-----------------------------------------------
uv run --locked --all-groups pytest packages/evalcore/tests/test_caller_line_validator.py \
  packages/evalcore/tests/test_caller_prompt.py packages/evalcore/tests/test_llm_brain.py \
  packages/evalcore/tests/test_scripted_and_replay_brains.py packages/evalcore/tests/test_caller_injection_suite.py \
  packages/evalcore/tests/test_caller_brain_purity.py packages/cli/tests/test_sim_adversarial_report_store.py
uv run --locked --all-groups pytest

Not done
--------
* No Python 3.15, no Postgres leg, no hosted CI, no lowest-direct leg (no network).
* No real LLM, TTS or STT engine, no key, no network: every engine is a fake; the socket guard of the conftest files
  was active in every run.
