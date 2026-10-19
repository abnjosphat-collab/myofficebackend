# tests/test_no_internal_error_leak.py — guardrail: no 500 response may carry
# internal error text to the client.
#
# Every `raise HTTPException(500, detail=...)` in app/**/*.py must have a
# static detail (no reference to the caught exception). Anything that needs
# the exception goes through app.errors.server_error(), which logs the
# traceback server-side with a ref id and returns a generic message.
#
# Parses every router (AST), so a regression fails here even when no
# behavioural test happens to trigger that handler.
import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def _detail_arg(node: ast.Call):
    if len(node.args) >= 2:
        return node.args[1]
    for k in node.keywords:
        if k.arg == "detail":
            return k.value
    return None


def _is_500(node: ast.Call) -> bool:
    """Any server-side status (500, 502, 503, 207 partial failures): all of them reach the person, so none may carry the exception."""
    def server_side(value) -> bool:
        return isinstance(value, ast.Constant) and isinstance(value.value, int) and (value.value >= 500 or value.value == 207)

    if node.args and server_side(node.args[0]):
        return True
    return any(k.arg == "status_code" and server_side(k.value) for k in node.keywords)


def test_no_5xx_detail_includes_the_exception():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "HTTPException"):
                continue
            if not _is_500(node):
                continue
            detail = _detail_arg(node)
            if detail is None:
                continue
            names = {n.id for n in ast.walk(detail) if isinstance(n, ast.Name)} - {"str"}
            leaked = names & {"e", "exc", "err", "error", "error_detail"}
            if leaked:
                offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno} leaks {sorted(leaked)}")
    assert not offenders, (
        "500 details must not include the caught exception (use app.errors.server_error):\n"
        + "\n".join(offenders)
    )


def test_server_error_shape_is_generic_with_ref():
    from fastapi import HTTPException
    from app.errors import server_error

    try:
        raise ValueError("table spares does not exist")
    except ValueError as e:
        exc = server_error(e, "Loading spares")
    assert isinstance(exc, HTTPException)
    assert exc.status_code == 500
    assert "table spares does not exist" not in str(exc.detail)
    assert str(exc.detail).startswith("Loading spares failed. Please try again. (ref ")
    assert str(exc.detail).endswith(")")
