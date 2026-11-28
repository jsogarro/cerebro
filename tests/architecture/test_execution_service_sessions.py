"""The execution service opens no session without a tenant.

Under forced row-level security a session with no tenant reads nothing and
cannot insert, and nothing about the call looks wrong in review. Every use of
``self.session_factory`` therefore has to be one of:

* the first argument of ``tenant_session`` inside ``_tenant_session``, with
  every ``self._tenant_session(...)`` call passing an organization that is not
  missing or a literal ``None``;
* a presence check (``not x``, ``x is None``, ``x or y``, an ``if`` test);
* one of the named hand-offs below, whose receiver scopes its own sessions.

That rules out calling the factory, aliasing it, and passing it to a helper.
The two cross-tenant scans may call ``maintenance_session_factory``, and
nothing else may use it.

This is a static check on one file and can be defeated by indirection such as
``getattr``; ``tests/integration/test_des_tenant_rls.py``, which runs the
service as ``cerebro_app``, is the control that actually proves the behaviour.
"""

import ast
from pathlib import Path

SERVICE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "api"
    / "services"
    / "direct_execution_service.py"
)
MAINTENANCE_SCANS = frozenset({"reconcile_expired_runs", "restore_active_executions"})
# (function, receiver): each receiver opens its sessions through tenant_session.
HAND_OFFS = frozenset(
    {
        ("__init__", "MASRSupervisorBridge"),  # tool audit store, tenant-scoped
        ("_model_call_journal", "ModelCallReservationRepository"),
    }
)


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }


def _is_self_attribute(node: ast.AST, attribute: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attribute
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node.name
    return "<module>"


def _callee(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


def _is_presence_check(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents[node]
    if isinstance(parent, ast.UnaryOp):
        return isinstance(parent.op, ast.Not)
    if isinstance(parent, ast.BoolOp):
        return True
    if isinstance(parent, ast.Compare):
        return all(isinstance(op, ast.Is | ast.IsNot) for op in parent.ops) and all(
            isinstance(c, ast.Constant) and c.value is None for c in parent.comparators
        )
    return isinstance(parent, ast.If | ast.IfExp) and parent.test is node


def _is_assignment_target(node: ast.AST, parent: ast.AST) -> bool:
    if isinstance(parent, ast.Assign):
        return node in parent.targets
    return isinstance(parent, ast.AnnAssign) and parent.target is node


def _organization_arg(call: ast.Call, positional_index: int) -> ast.AST | None:
    if len(call.args) > positional_index:
        return call.args[positional_index]
    for kw in call.keywords:
        if kw.arg == "organization_id":
            return kw.value
    return None


def _has_non_none_organization(call: ast.Call, positional_index: int) -> bool:
    org = _organization_arg(call, positional_index)
    return org is not None and not (isinstance(org, ast.Constant) and org.value is None)


def _allowed_session_factory_use(
    node: ast.Attribute, parents: dict[ast.AST, ast.AST]
) -> bool:
    parent = parents[node]
    function = _enclosing_function(node, parents)
    if _is_assignment_target(node, parent):
        return True
    if isinstance(parent, ast.Call):
        if _callee(parent) == "tenant_session" and function == "_tenant_session":
            return (
                bool(parent.args)
                and parent.args[0] is node
                and _has_non_none_organization(parent, 1)
            )
        return (_callee(parent), function) in {(r, f) for f, r in HAND_OFFS}
    if isinstance(parent, ast.keyword):
        call = parents[parent]
        return (
            isinstance(call, ast.Call)
            and (
                function,
                _callee(call),
            )
            in HAND_OFFS
        )
    return _is_presence_check(node, parents)


def _uses_in(
    tree: ast.AST, attribute: str
) -> list[tuple[ast.Attribute, dict[ast.AST, ast.AST]]]:
    parents = _parents(tree)
    return [
        (node, parents)
        for node in ast.walk(tree)
        if _is_self_attribute(node, attribute)
    ]


def _uses(attribute: str) -> list[tuple[ast.Attribute, dict[ast.AST, ast.AST]]]:
    return _uses_in(ast.parse(SERVICE.read_text()), attribute)


def _offending_session_factory_uses(tree: ast.AST) -> list[str]:
    return [
        f"{_enclosing_function(node, parents)}:{node.lineno}"
        for node, parents in _uses_in(tree, "session_factory")
        if not _allowed_session_factory_use(node, parents)
    ]


def _offending_tenant_session_calls(tree: ast.AST) -> tuple[int, list[str]]:
    uses = _uses_in(tree, "_tenant_session")
    offending = [
        f"{_enclosing_function(node, parents)}:{node.lineno}"
        for node, parents in uses
        if not (
            isinstance(parents[node], ast.Call)
            and parents[node].func is node
            and _has_non_none_organization(parents[node], 0)
        )
    ]
    return len(uses), offending


def test_every_use_of_the_application_factory_is_tenant_scoped() -> None:
    assert _offending_session_factory_uses(ast.parse(SERVICE.read_text())) == []


def test_every_tenant_session_call_passes_a_non_none_organization() -> None:
    checked, offending = _offending_tenant_session_calls(ast.parse(SERVICE.read_text()))
    assert checked > 0
    assert offending == []


def test_guard_catches_tenant_session_bypasses() -> None:
    sample = """
class Service:
    def _tenant_session(self, organization_id):
        return tenant_session(self.session_factory, organization_id)

    async def ok(self, org):
        async with self._tenant_session(org):
            pass

    async def ok_kw(self, org):
        async with self._tenant_session(organization_id=org):
            pass

    async def bad_none(self):
        async with self._tenant_session(None):
            pass

    async def bad_kw_none(self):
        async with self._tenant_session(organization_id=None):
            pass

    async def bad_missing(self):
        async with self._tenant_session():
            pass

    async def bad_alias(self):
        fn = self._tenant_session
        return fn

    async def bad_bypass(self, org):
        async with tenant_session(self.session_factory, org):
            pass
"""
    tree = ast.parse(sample)
    checked, offending = _offending_tenant_session_calls(tree)
    assert checked == 6
    assert {item.split(":")[0] for item in offending} == {
        "bad_none",
        "bad_kw_none",
        "bad_missing",
        "bad_alias",
    }
    factory_offending = _offending_session_factory_uses(tree)
    assert [item.split(":")[0] for item in factory_offending] == ["bad_bypass"]


def test_only_the_cross_tenant_scans_use_the_maintenance_factory() -> None:
    callers = {
        _enclosing_function(node, parents)
        for node, parents in _uses("maintenance_session_factory")
        if not (isinstance(parents[node], ast.Assign) and node in parents[node].targets)
        and _enclosing_function(node, parents) != "__init__"
    }

    assert callers == MAINTENANCE_SCANS
