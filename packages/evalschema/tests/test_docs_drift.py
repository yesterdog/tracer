# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Generated documentation and policy text must not drift from what they describe.

* A32: ``docs/schema/*.md`` equal the rendering of the shipped JSON Schema files
  (``scripts/generate_field_docs.py --check``, ADR 0008 Decision 9), and the CI workflow runs both
  drift checks (``schema_export --check`` and ``generate_field_docs.py --check``).
* A34: the ``CO-*`` rule ids in the register table of ``SCHEMA_POLICY.md`` are exactly the ids of
  the register ``CODE_ONLY_RULES`` of the test suite.

Each check is also run on a deliberately bad sample so that it cannot pass vacuously.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from ._support import PACKAGE_DIR, ROOT
from ._support_b8 import CODE_ONLY_RULES

yaml: Any = importlib.import_module("yaml")  # pyyaml, test group; untyped, so loaded dynamically

SCRIPT = ROOT / "scripts" / "generate_field_docs.py"
DOCS_DIR = ROOT / "docs" / "schema"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
POLICY = PACKAGE_DIR / "SCHEMA_POLICY.md"


def generator() -> ModuleType:
    """The script as a module (it is not an importable package)."""
    spec = importlib.util.spec_from_file_location("generate_field_docs_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def rendered_pages() -> dict[str, bytes]:
    pages: dict[str, bytes] = generator().render_from_directory(generator().schema_directory())
    return pages


# ------------------------------------------------------------------ A32: field docs match


def test_field_docs_match_generated() -> None:
    """A32 (logic of ``generate_field_docs.py --check``): the pages below ``docs/schema/`` are
    byte-equal to the rendering of the shipped schema files; none is missing, changed or extra.
    The page set is the index, ``value-objects.md`` and one page per registered entity."""
    module = generator()
    pages = rendered_pages()
    assert module.check_docs(pages, DOCS_DIR) == []
    assert sorted(p.name for p in DOCS_DIR.iterdir()) == sorted(pages)
    assert len(pages) == 26 + 2


def test_field_docs_check_command_exits_zero() -> None:
    """The command CI runs, as a subprocess: exit code 0 and nothing on stderr."""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script, no shell
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode, result.stderr.strip()) == (0, "")


def test_field_docs_check_reports_changed_missing_and_extra_pages(tmp_path: Path) -> None:
    """Not vacuous: on a copy of the pages with one page edited, one deleted and one added, the
    check reports exactly those three problems, with the tags the contract names, and the command
    exits 1 with the same lines on stderr."""
    module = generator()
    copy = tmp_path / "schema"
    copy.mkdir()
    for path in DOCS_DIR.iterdir():
        (copy / path.name).write_bytes(path.read_bytes())
    assert module.check_docs(rendered_pages(), copy) == []
    (copy / "Run.md").write_bytes((copy / "Run.md").read_bytes() + b"\nhand edit\n")
    (copy / "Persona.md").unlink()
    (copy / "Stray.md").write_text("not generated\n", encoding="utf-8")
    expected = ["[changed] Run.md", "[missing] Persona.md", "[extra] Stray.md"]
    assert sorted(module.check_docs(rendered_pages(), copy)) == sorted(expected)
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script, no shell
        [sys.executable, str(SCRIPT), "--check", "--out", str(copy)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert sorted(result.stderr.split("\n")[:-1]) == sorted(expected)


# ------------------------------------------------------------------ A32: the CI steps


def run_steps(workflow_text: str) -> list[str]:
    """Every ``run:`` command of every step of every job of a workflow, in order."""
    document = yaml.safe_load(workflow_text)
    steps = [
        step
        for job in document["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step, dict) and "run" in step
    ]
    return [str(step["run"]).strip() for step in steps]


def has_locked_step(steps: list[str], target: str) -> bool:
    """A step that runs ``target`` through ``uv run --locked`` and is not a comment or a no-op."""
    return any(
        step.startswith("uv run --locked") and target in step and "|| true" not in step
        for step in steps
    )


SCHEMA_CHECK = "-m tracer_evalschema.schema_export --check"
DOCS_CHECK = "scripts/generate_field_docs.py --check"


def test_ci_runs_both_generated_file_drift_checks() -> None:
    """A32: the workflow has a step that runs ``schema_export --check`` and a step that runs
    ``generate_field_docs.py --check``, both through ``uv run --locked``, both in the same job as
    ruff (the lint job), and the field-docs step comes after the schema step (the docs are
    rendered from the schema files, so a schema drift is reported first)."""
    text = WORKFLOW.read_text(encoding="utf-8")
    steps = run_steps(text)
    assert has_locked_step(steps, SCHEMA_CHECK), steps
    assert has_locked_step(steps, DOCS_CHECK), steps
    jobs = yaml.safe_load(text)["jobs"]
    lint = [
        str(s["run"]).strip() for s in jobs["lint"]["steps"] if isinstance(s, dict) and "run" in s
    ]
    assert has_locked_step(lint, SCHEMA_CHECK)
    assert has_locked_step(lint, DOCS_CHECK)
    schema_at = next(i for i, s in enumerate(lint) if SCHEMA_CHECK in s)
    docs_at = next(i for i, s in enumerate(lint) if DOCS_CHECK in s)
    assert schema_at < docs_at


@pytest.mark.parametrize(
    "workflow",
    [
        "jobs:\n  lint:\n    steps:\n      - run: uv run --locked ruff check\n",
        "jobs:\n  lint:\n    steps:\n"
        "      # - run: uv run --locked python scripts/generate_field_docs.py --check\n"
        "      - run: uv run --locked ruff check\n",
        "jobs:\n  lint:\n    steps:\n      - run: python scripts/generate_field_docs.py --check\n",
        "jobs:\n  lint:\n    steps:\n"
        "      - run: uv run --locked python scripts/generate_field_docs.py --check || true\n",
        "jobs:\n  lint:\n    steps:\n"
        "      - run: uv run --locked python scripts/generate_field_docs.py --write\n",
    ],
    ids=["absent", "commented-out", "not-locked", "ignored-failure", "write-not-check"],
)
def test_ci_step_checker_rejects_bad_workflows(workflow: str) -> None:
    """Not vacuous: the step finder does not accept a missing, commented-out, unlocked,
    failure-ignoring or ``--write`` step."""
    assert not has_locked_step(run_steps(workflow), DOCS_CHECK)


def test_ci_step_checker_accepts_the_real_step_forms() -> None:
    good = (
        "jobs:\n  lint:\n    steps:\n"
        "      - run: uv run --locked python scripts/generate_field_docs.py --check\n"
        "      - run: uv run --locked python -m tracer_evalschema.schema_export --check\n"
    )
    assert has_locked_step(run_steps(good), DOCS_CHECK)
    assert has_locked_step(run_steps(good), SCHEMA_CHECK)


# ------------------------------------------------------------------ A34: policy vs register

ROW_ID = re.compile(r"^\|\s*`(?P<id>CO-[A-Z0-9-]+)`\s*\|", re.MULTILINE)


def policy_row_ids(text: str) -> list[str]:
    """The ``CO-*`` ids in the first column of table rows, in order of appearance."""
    return [match.group("id") for match in ROW_ID.finditer(text)]


def test_policy_register_table_lists_exactly_the_code_only_rules() -> None:
    """A34: the ``CO-*`` ids of the register table in ``SCHEMA_POLICY.md`` (section 2.3) equal the
    ids of ``CODE_ONLY_RULES``: none missing, none extra, none twice. The test register is
    authoritative; the policy table is the reader's summary of it."""
    ids = policy_row_ids(POLICY.read_text(encoding="utf-8"))
    register = [rule.rule_id for rule in CODE_ONLY_RULES]
    assert len(register) == len(set(register))
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)
    assert set(ids) - set(register) == set(), "in the policy table, not in the register"
    assert set(register) - set(ids) == set(), "in the register, not in the policy table"
    assert len(ids) == len(register) > 0


def test_policy_row_finder_is_not_vacuous() -> None:
    """The finder reads table rows only, not prose mentions, and sees an id missing from a table."""
    table = "| Row | Why |\n| --- | --- |\n| `CO-ONE` | a |\n| `CO-TWO-2` | b |\n"
    assert policy_row_ids(table) == ["CO-ONE", "CO-TWO-2"]
    prose = "The row `CO-ONE` is explained below, and | `CO-X` | is inline.\n"
    assert policy_row_ids(prose) == []
    assert policy_row_ids(table.replace("| `CO-TWO-2` | b |\n", "")) == ["CO-ONE"]
