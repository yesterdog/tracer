# tracer: open-source eval framework for Indic voice AI agents

Start here when resuming work. Read `docs/HANDOFF.md` first, then `docs/phase1/SCOPE.md` (task table, test names, gate), `docs/phase1/api-surface.md` and `docs/adr/README.md`. Before relying on the handoff's status, run `uv sync --locked --all-groups && uv run --locked pytest -q packages/evalschema/tests` and check `git status`.

Standing rules from the project owner:
- Only the AI agent is evaluated. The caller may be a human or a simulator. No IVR, no human hand-over. Roles are `caller`, `agent`, `unknown`.
- Never assume or extrapolate. Missing input means `not_applicable`; missing cost means `not_available`; never 0 or false.
- Quality over effort. No cut lists and no "too heavy for v0.0.1" reasoning; if a task slips, the date moves.
- Design decisions are auto-approved on the recommended option (owner instruction, 2026-10-23). Record each in the decision log with the rejected options and trade-off; do not wait for an answer. Rules here may be changed on a recommendation; log the change. Do not use a multi-question widget.
- Git commit and push are authorised when the code is ready and no keys are published (owner, 2026-10-23). Before every commit: scan the tree and staged diff for key values and key patterns, confirm `.env.local` and `packages/evalschema/tests/fixtures/private/` are not staged, and run `scripts/guard_private_fixtures.py`.
- Sending data to frontier-lab APIs, extra spend and data deletion (cascade) are allowed. The private call fixture stays local-only. Licensing: prefer open source, keep the automated check, do not dwell on copyleft.
- Never read, copy or quote the private call fixture (a real helpline call export the owner keeps outside the repository) or the owner's personal keys notes file. The Sarvam dev key lives only in the git-ignored `.env.local`. Never commit anything under `packages/evalschema/tests/fixtures/private/`. Never paste keys into chat or files.
- Never weaken, skip or delete a test to make it pass. Do not run `ruff --unsafe-fixes` on test files.
- Use `uv run` for Python (the system `python3` is too old).

The project agents are in `.claude/agents/` (staff-reviewer, schema-architect, dev, qa, indic-linguistics, security-licensing). Phase loop: reviewer scopes, qa writes failing tests, architect/dev implement, qa runs, reviewer gates.
