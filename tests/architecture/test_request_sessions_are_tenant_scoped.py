"""A request handler that opens the shared session must also resolve the tenant.

Under forced row-level security a session with no tenant reads nothing and
cannot insert, and nothing in the handler looks wrong. A handler that depends
on ``get_session`` therefore also has to depend on ``get_tenant_context``
(directly, through an alias, or through a dependency that does), unless it
only touches tables that carry no row policy; those are listed below with the
tables they touch.

This is a static check on the route modules. Background jobs and the lifespan
are covered by the integration suites that run as ``cerebro_app``.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
MODULES = (
    *sorted((SRC / "api" / "routes").glob("*.py")),
    SRC / "api" / "auth" / "auth_router.py",
    SRC / "middleware" / "auth_middleware.py",
)
TENANT = "get_tenant_context"

# Handlers whose session reaches only tables without a row policy.
UNPOLICED = {
    "admin_security.py": "audit_logs, security_alerts, users",
    "auth_router.py": "users",
    "auth_middleware.py": "users",
}
# Handlers that set the tenant themselves rather than through the dependency.
SETS_TENANT_ITSELF = {("users.py", "delete_user_gdpr")}


def _depends_target(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Depends"
        and node.args
        and isinstance(node.args[0], ast.Name)
    ):
        return node.args[0].id
    return None


def _depends_in(node: ast.AST) -> set[str]:
    return {t for n in ast.walk(node) if (t := _depends_target(n))}


def _aliases(tree: ast.Module) -> dict[str, set[str]]:
    """Module-level ``Name = Annotated[T, Depends(f)]`` aliases."""
    return {
        target.id: _depends_in(stmt.value)
        for stmt in tree.body
        if isinstance(stmt, ast.Assign)
        for target in stmt.targets
        if isinstance(target, ast.Name)
    }


def _functions(tree: ast.Module) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    return {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
    }


def _direct_dependencies(
    fn: ast.AsyncFunctionDef | ast.FunctionDef, aliases: dict[str, set[str]]
) -> set[str]:
    found = _depends_in(fn.args)
    for arg in [*fn.args.args, *fn.args.kwonlyargs]:
        if arg.annotation is not None:
            found |= _depends_in(arg.annotation)
            found |= {
                dep
                for n in ast.walk(arg.annotation)
                if isinstance(n, ast.Name)
                for dep in aliases.get(n.id, ())
            }
    for decorator in fn.decorator_list:
        found |= _depends_in(decorator)
    return found


def _closure(name: str, functions: dict, aliases: dict, seen: set[str]) -> set[str]:
    if name in seen or name not in functions:
        return set()
    seen.add(name)
    direct = _direct_dependencies(functions[name], aliases)
    for dep in list(direct):
        direct |= _closure(dep, functions, aliases, seen)
    return direct


def _is_handler(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    return any(
        isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.attr in {"get", "post", "put", "patch", "delete", "websocket"}
        for d in fn.decorator_list
    )


def _violations() -> list[str]:
    found = []
    for path in MODULES:
        if path.name in UNPOLICED:
            continue
        tree = ast.parse(path.read_text())
        functions, aliases = _functions(tree), _aliases(tree)
        for name, fn in functions.items():
            if not _is_handler(fn):
                continue
            if (path.name, name) in SETS_TENANT_ITSELF:
                continue
            closure = _closure(name, functions, aliases, set())
            if "get_session" in closure and TENANT not in closure:
                found.append(f"{path.name}:{name}")
    return found


def test_every_handler_that_uses_the_session_resolves_the_tenant() -> None:
    assert _violations() == []


def test_the_exemptions_still_name_real_modules() -> None:
    names = {p.name for p in MODULES}
    assert set(UNPOLICED) | {m for m, _ in SETS_TENANT_ITSELF} <= names


def test_a_handler_that_sets_the_tenant_itself_really_does() -> None:
    for module, handler in SETS_TENANT_ITSELF:
        tree = ast.parse(next(p for p in MODULES if p.name == module).read_text())
        calls = {
            n.func.id
            for n in ast.walk(_functions(tree)[handler])
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "set_postgres_tenant_context" in calls, handler
