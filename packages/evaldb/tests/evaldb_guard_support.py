# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Tracer contributors
"""Helpers of the Phase 2 batch F guard tests (P2-24): a reader of the SQL text a source file sends.

Contract: docs/phase2/api-surface-batch-f.md section 2. This module is imported by
``test_evaldb_guards.py`` (``from evaldb_guard_support import ...``; the tests directory has no
``__init__.py``). It holds no test.

``scan_source`` finds every call that hands SQL text to the database and says whether that text is
a literal, built from pieces (an f-string, a concatenation, ``%`` or ``.format``), or something the
reader cannot trace to a literal (a parameter, a call result, a loop over a call). It follows a
name to the assignments of the enclosing functions and of the module, one level of loop and
unpacking through literal structures. It is a reader of source text, not a data-flow analysis, and
the test that uses it pins the sites it cannot call literal, one by one.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

# The names under which a call hands SQL text on: SQLAlchemy ``text``, ``exec_driver_sql``,
# ``execute`` (a DB-API cursor, a Connection or ``op``), ``executemany``, ``executescript``,
# ``DDL``, and the ``sqltext`` of ``create_check_constraint``.
SINKS = frozenset(
    {
        "text",
        "exec_driver_sql",
        "execute",
        "executemany",
        "executescript",
        "DDL",
        "create_check_constraint",
    }
)
# These also take Core constructs (``conn.execute(sa.select(...))``), so a name bound to a call
# result is not text there.
CORE_SINKS = frozenset({"execute", "executemany", "executescript"})
# For these the SQL is not the first argument.
SKIP_LEADING = {"create_check_constraint": 1}

LITERAL = "literal"
DYNAMIC = "dynamic"
OPAQUE = "opaque"
CONSTRUCT = "construct"  # a call whose result is not text we can read (a Core construct)


@dataclass(frozen=True)
class Site:
    """One call that hands SQL text on, and what its text is."""

    qualname: str  # dotted names of the enclosing functions, or "<module>"
    sink: str
    ordinal: int  # nth sink call of that name inside that function, from 0, in source order
    kind: str  # LITERAL, DYNAMIC, OPAQUE or CONSTRUCT
    pieces: tuple[str, ...]  # DYNAMIC: the source of every interpolated or joined expression
    argument: str  # the source of the SQL argument
    pieces_literal: bool = False  # DYNAMIC: every piece is drawn from a literal (see scan_source)


def _unparse(node: ast.AST) -> str:
    return ast.unparse(node)


def _sink_name(call: ast.Call) -> str | None:
    function = call.func
    name = (
        function.id
        if isinstance(function, ast.Name)
        else function.attr
        if isinstance(function, ast.Attribute)
        else None
    )
    return name if name in SINKS else None


def _is_str(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _builds_text(node: ast.AST) -> bool:
    """True for an f-string, a ``+`` or ``%`` with a text operand, and ``.format`` on a text."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Mod):
        return (
            _is_str(node.left)
            or _is_str(node.right)
            or _builds_text(node.left)
            or _builds_text(node.right)
        )
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"format", "format_map"}
    )


def _pieces(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.JoinedStr):
        return tuple(_unparse(p.value) for p in node.values if isinstance(p, ast.FormattedValue))
    if isinstance(node, ast.BinOp):
        found: list[str] = []
        for side in (node.left, node.right):
            if _builds_text(side):
                found.extend(_pieces(side))
            elif not _is_str(side):
                found.append(_unparse(side))
        return tuple(found)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return tuple(_unparse(a) for a in (*node.args, *(k.value for k in node.keywords)))
    return ()


class _Scan(ast.NodeVisitor):
    def __init__(self, tree: ast.Module, external: frozenset[str]) -> None:
        self.tree = tree
        self.external = external
        self.stack: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        self.sites: list[Site] = []
        self.seen: dict[tuple[str, str], int] = {}

    # ----------------------------------------------------------------- scopes and assignments
    def _assignments(self, name: str) -> list[tuple[str, ast.AST | None]]:
        """The bindings of ``name`` in the innermost scope that has any: (how, value)."""
        scopes: list[ast.AST] = [*reversed(self.stack), self.tree]
        for scope in scopes:
            found: list[tuple[str, ast.AST | None]] = []
            for node in self._own_nodes(scope):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        found.extend(self._bind(target, name, "assign", node.value))
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    found.extend(self._bind(node.target, name, "assign", node.value))
                elif isinstance(node, ast.AugAssign):
                    if isinstance(node.target, ast.Name) and node.target.id == name:
                        found.append(("aug", node.value))
                elif isinstance(node, ast.For | ast.comprehension):
                    found.extend(self._bind(node.target, name, "loop", node.iter))
                elif isinstance(node, ast.With | ast.AsyncWith):
                    for item in node.items:
                        if item.optional_vars is not None:
                            found.extend(self._bind(item.optional_vars, name, "other", None))
            if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef):
                args = scope.args
                for arg in [
                    *args.posonlyargs,
                    *args.args,
                    *args.kwonlyargs,
                    args.vararg,
                    args.kwarg,
                ]:
                    if arg is not None and arg.arg == name:
                        found.append(("param", None))
            if found:
                return found
        return []

    @staticmethod
    def _own_nodes(scope: ast.AST) -> list[ast.AST]:
        """Nodes of a scope without the bodies of the functions nested in it."""
        out: list[ast.AST] = []
        todo = list(ast.iter_child_nodes(scope))
        while todo:
            node = todo.pop()
            out.append(node)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
                continue
            todo.extend(ast.iter_child_nodes(node))
        return out

    @staticmethod
    def _bind(
        target: ast.AST, name: str, how: str, value: ast.AST | None
    ) -> list[tuple[str, ast.AST | None]]:
        if isinstance(target, ast.Name):
            return [(how, value)] if target.id == name else []
        if isinstance(target, ast.Tuple | ast.List) and any(
            isinstance(sub, ast.Name) and sub.id == name for sub in ast.walk(target)
        ):
            # unpacked from a structure: a loop keeps its iterable, an assignment its value
            return [(how if how == "loop" else "unpack", value)]
        return []

    # ------------------------------------------------------------------ literal structures
    def _literal_structure(self, node: ast.AST | None, depth: int = 0) -> bool:
        """Text and numbers, and tuples, lists, sets and dicts of them, read from the module."""
        if node is None or depth > 6:
            return False
        if isinstance(node, ast.Attribute) and _unparse(node) in self.external:
            return True
        if isinstance(node, ast.Constant):
            return isinstance(node.value, str | int | float | bool | type(None))
        if isinstance(node, ast.Tuple | ast.List | ast.Set):
            return all(self._literal_structure(e, depth + 1) for e in node.elts)
        if isinstance(node, ast.Dict):
            return all(
                k is not None and self._literal_structure(k, depth + 1) for k in node.keys
            ) and all(self._literal_structure(v, depth + 1) for v in node.values)
        if isinstance(node, ast.Name):
            found = self._assignments(node.id)
            return bool(found) and all(
                how in {"assign", "unpack", "loop"} and self._literal_structure(value, depth + 1)
                for how, value in found
            )
        if isinstance(node, ast.Subscript):
            return self._literal_structure(node.value, depth + 1)
        if isinstance(node, ast.Call):
            function = node.func
            if (
                isinstance(function, ast.Name)
                and function.id in {"reversed", "sorted", "tuple", "list", "set", "enumerate"}
                and len(node.args) >= 1
            ):
                return self._literal_structure(node.args[0], depth + 1)
            if (
                isinstance(function, ast.Attribute)
                and function.attr in {"items", "values", "keys"}
                and not node.args
            ):
                return self._literal_structure(function.value, depth + 1)
        return False

    # ------------------------------------------------------------------------ classification
    def classify(
        self, node: ast.AST, core_ok: bool = False, depth: int = 0
    ) -> tuple[str, tuple[str, ...]]:
        if _is_str(node):
            return LITERAL, ()
        if _builds_text(node):
            return DYNAMIC, _pieces(node)
        if isinstance(node, ast.Call):
            return CONSTRUCT, ()
        if isinstance(node, ast.Name) and depth < 5:
            found = self._assignments(node.id)
            if not found:
                return OPAQUE, ()
            kinds: list[tuple[str, tuple[str, ...]]] = []
            for how, value in found:
                if how == "assign" and value is not None:
                    kind, pieces = self.classify(value, core_ok, depth + 1)
                    # a call result bound to a name is text we cannot read, except where the
                    # sink also takes Core constructs (``conn.execute(select)``)
                    kinds.append((OPAQUE if kind == CONSTRUCT and not core_ok else kind, pieces))
                elif how == "aug":
                    kinds.append((DYNAMIC, ()))
                elif how in {"loop", "unpack"} and self._literal_structure(value):
                    kinds.append((LITERAL, ()))
                else:
                    kinds.append((OPAQUE, ()))
            for kind in (DYNAMIC, OPAQUE):
                for found_kind, pieces in kinds:
                    if found_kind == kind:
                        return kind, pieces
            return LITERAL, ()
        if self._literal_structure(node):
            return LITERAL, ()
        return OPAQUE, ()

    def placeholder_is_literal(self, expression: str) -> bool:
        """A piece of a dynamic site is drawn from a literal: a name bound only to literal
        structures (or looped over one) in the module or the function, or an attribute the caller
        names in ``external`` (a module constant it has checked itself)."""
        node = ast.parse(expression, mode="eval").body
        return self._literal_structure(node)

    # ----------------------------------------------------------------------------- visiting
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stack.append(node)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        sink = _sink_name(node)
        if sink is not None:
            skip = SKIP_LEADING.get(sink, 0)
            arguments = node.args[skip:] if sink in SKIP_LEADING else node.args[:1]
            for argument in arguments:
                qualname = ".".join(f.name for f in self.stack) or "<module>"
                index = self.seen.get((qualname, sink), 0)
                self.seen[(qualname, sink)] = index + 1
                kind, pieces = self.classify(argument, core_ok=sink in CORE_SINKS)
                literal = kind == DYNAMIC and all(self.placeholder_is_literal(p) for p in pieces)
                self.sites.append(
                    Site(qualname, sink, index, kind, pieces, _unparse(argument), literal)
                )
        self.generic_visit(node)


def scan_source(source: str, external: frozenset[str] = frozenset()) -> list[Site]:
    """Every sink call of a module, in source order. ``external`` names attribute expressions
    (``triggers.UPDATE_TRIGGER_FUNCTION``) that the caller has checked to be module constants of
    literal text; a DYNAMIC site whose pieces are all such names or are drawn from literal
    structures of the module has ``pieces_literal`` true."""
    tree = ast.parse(source)
    scan = _Scan(tree, external)
    scan.visit(tree)
    return scan.sites
