# Phase 3 batch J3 contract: the cli no-network guard and the postgres-marked tests (C3)

Author: qa, 2026-10-27. Source: the lead decision of the decision log entry "PHASE 3 BATCH J IMPLEMENTED ... LEAD DECISION"; the dev's finding that the autouse `_no_network` fixture of `packages/cli/tests/conftest.py` (ADR 0024 rule 17) also blocks `test_pipeline_bundle_stores_on_both_backends[postgres]`. qa changes no source; the conftest and the helper below are test infrastructure written in staging for the dev to move.

## 0. Files (nothing under packages/ was created or edited)

Staged at `docs/phase3/evidence/red/batch-j3/staging/tests/cli/` -> `packages/cli/tests/` (no `__init__.py`):

| File | Role | Collected |
|---|---|---|
| `test_cli_network_guard_postgres.py` | the new tests (default suite, no server) | 36 |
| `cli_network_guard.py` | NEW helper, not collected; the loopback-only guard | - |
| `conftest.py` | REPLACES the existing conftest: named spec change (`spec-change.diff`, 45 lines) | - |

Dev: `git mv -f` the three files. The helper is a support module like `cli_batch*_support.py`.

## 1. Behaviour (the lead decision, made exact)

1. A test that carries the `postgres` marker (on the function, the class, the module, or a parameter via `pytest.param(..., marks=...)`; `request.node.get_closest_marker("postgres")`) has the four socket paths (`socket.socket.connect`, `connect_ex`, `socket.create_connection`, `socket.getaddrinfo`) replaced by the loopback-only versions of `cli_network_guard`; every other test keeps the full guard, loopback included. Only the `postgres` marker exempts (`local_only` does not).
2. Loopback-only: a host is accepted if it is the name `localhost` or a literal IP that is a loopback address (127.0.0.0/8, `::1`, `::ffff:127.x`, a `%scope` suffix allowed). Any other host name is refused BEFORE any DNS query. `localhost` is resolved with the real resolver and every address must be loopback, else refused. Refused: `0.0.0.0`, `::`, the empty host, private and public addresses, `localhost.example.com`, `evil-localhost`, `127.0.0.1.example.com`, None, a non-tuple address, a one-element address, and every non-TCP family (AF_UNIX). The error is the same `AssertionError("a unit test tried to use the network (ADR 0024 rule 17)")` as the full guard (the existing tests match on `network`).
3. The asyncio patches (`BaseEventLoop.create_connection`, `sock_connect`) stay in force for every test. Note: `sock_connect` is overridden by the selector loop, so that patch never reached it; there the socket-level guard decides (loopback allowed in a marked test, elsewhere refused). Pinned as it is.
4. `SARVAM_API_KEY` is still deleted for every test (pinned with a key set in the subprocess environment).
5. The existing AST test `test_unit_tests_open_no_socket_each_phase3_package_has_the_guard` (evalcore) reads the conftest text: the six `monkeypatch.setattr` calls and `_refuse`/`_refuse_async` (raising only `AssertionError`) stay in the conftest. That is why the unmarked branch keeps the literal setattr calls and the marked branch calls `install_loopback_only`. A refactor that moves them to the helper would break that test.

Exact texts: the staged `conftest.py` and `cli_network_guard.py`. Conftest core:

```python
@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("postgres") is not None:
        install_loopback_only(monkeypatch)
    else:
        monkeypatch.setattr(socket.socket, "connect", _refuse)
        ... (the other three, as before)
    monkeypatch.setattr(asyncio.BaseEventLoop, "create_connection", _refuse_async)
    monkeypatch.setattr(asyncio.BaseEventLoop, "sock_connect", _refuse_async)
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
```

## 2. The other conftests

`packages/evalcore/tests/conftest.py` and `packages/evalproviders/tests/conftest.py` have the same full guard, and no test in either directory carries the `postgres` marker (AST scan; pinned by `test_no_postgres_marked_test_exists_where_the_guard_stays_total[evalcore|evalproviders]`, which also pins that their conftests mention neither `postgres` nor `get_closest_marker`). `packages/evaldb/tests/conftest.py` has no socket guard. So the problem exists only in the cli directory. `test_phase3_ci_postgres_cli.py` (evalcore) mentions the marker only in strings.

## 3. Tests (36 collected)

Direct, on the helper, with a listener the test opens itself on 127.0.0.1: `test_loopback_guard_lets_the_four_socket_paths_reach_a_loopback_listener`; `test_loopback_guard_refuses_every_other_address_before_any_lookup` (14 addresses x 4 paths); `test_loopback_guard_refuses_non_tcp_shapes`; `test_loopback_guard_refuses_localhost_when_it_resolves_away` (real resolver faked to answer 192.0.2.7); `test_is_loopback_ip_reads_literals_only` (15 literals).
Through the real conftest, in a subprocess pytest over an inline file (loopback listener only): `test_the_real_conftest_exempts_only_postgres_marked_tests_and_only_loopback` (7 inline tests: marked reaches loopback; param mark; marked refused elsewhere and on both asyncio paths; unmarked refused on loopback on all four paths; `local_only` not exempt; key cleared) and `test_the_inline_run_has_teeth_it_fails_under_a_conftest_that_exempts_everything`.

## 4. Proof

- Red on the current tree: 34 failed, 2 passed on Python 3.11, 3.12, 3.13, 3.14 (`red.py3.1x.log`). 32 fail with `ModuleNotFoundError: cli_network_guard`; the two conftest-level tests fail for the real reason (the marked test's loopback connection raises "a unit test tried to use the network"; the teeth test sees no marker branch). The 2 that pass are the evalcore and evalproviders pins.
- Oracle (new scratch directory, the staged conftest and helper): 36 passed on all four versions (`oracle.py3.1x.log`); the whole `packages/cli/tests` plus the evalcore and evalproviders socket-guard tests: 655 passed, 1 deselected (the postgres parameter) on all four (`oracle-cli-and-guard-tests.py3.1x.log`); ruff and mypy strict clean.
- Mutants (`mutants.txt`): 20, 19 killed, 1 equivalent (M11, an unchecked `create_connection`: the real function still calls the patched `getaddrinfo` and `connect`, which refuse). Killed include: an exemption of everything for marked tests (M2), of any host (M1, M6, M10, M15, M16), loopback for unmarked tests (M3), no guard for unmarked tests (M4), inverted marker (M8), other markers exempt (M20), private ranges (M5), unix sockets (M9), unverified `localhost` (M7, M19), key not cleared (M13), asyncio unguarded (M14).
- Not proven here: a run against a real Postgres server (pg8000 uses `socket.create_connection`, which the helper allows for 127.0.0.1); that is the hosted legs and the lead's local pg15/pg18 run.

## 5. Ambiguities (the lead auto-approves)

1. `localhost` by name is allowed and verified by resolution; rejected: literals only (a connection string with `localhost` would fail in a developer's run).
2. 127.0.0.0/8 and `::ffff:127.x` count as loopback, not only 127.0.0.1; rejected: three exact strings (stricter, but `127.0.0.2` is still this machine).
3. AF_UNIX refused (a socket-directory Postgres URL is not used by CI); rejected: allow, because a path can name any local service.
4. The exemption keys on the marker, not on the environment variable: a developer without Postgres never sees a socket opened, and a marked test cannot leave the machine.
