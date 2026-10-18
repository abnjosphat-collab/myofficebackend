"""Codemod: route every 500 that leaks internal error text through app.errors.server_error.

Usage (from backend/):
    .venv/Scripts/python.exe scripts/codemod_server_error.py --report   # list only
    .venv/Scripts/python.exe scripts/codemod_server_error.py --apply    # rewrite files

Scope: `raise HTTPException(500-ish, detail=<references the caught exception>)`
in app/**/*.py (except app/errors.py itself). Deliberate 4xx messages, 500s
with static text, and 503s are left alone.

Rewrite:
    raise HTTPException(status_code=500, detail=f"Error creating overtime: {e}")
becomes:
    raise server_error(e, "Creating overtime")

The 'what' phrase is derived from the static message prefix ("Error X" -> "X",
first letter capitalised); a bare `str(e)`/`{e}` detail with no prefix falls
back to a humanised enclosing-function name ("Loading ..."). Any OTHER
interpolated names (ids, counts) are kept in the 'what' as an f-string, since
they are caller data, not internal error text.
"""
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app"

GERUND = [
    ("fetching", "Loading"), ("getting", "Loading"), ("loading", "Loading"),
    ("listing", "Loading"), ("retrieving", "Loading"),
]


def _is_500(node: ast.Call) -> bool:
    if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == 500:
        return True
    return any(
        k.arg == "status_code" and isinstance(k.value, ast.Constant) and k.value.value == 500
        for k in node.keywords
    )


def _detail_arg(node: ast.Call):
    if len(node.args) >= 2:
        return node.args[1]
    for k in node.keywords:
        if k.arg == "detail":
            return k.value
    return None


def _except_name(tree: ast.Module, target: ast.AST) -> str | None:
    """Name bound by the innermost `except ... as NAME` enclosing target."""
    best = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.name is None:
            continue
        if node.lineno <= target.lineno and node.end_lineno >= target.end_lineno:
            if best is None or node.lineno >= best[0]:
                best = (node.lineno, node.name)
    return best[1] if best else None


def _enclosing_func(tree: ast.Module, target: ast.AST) -> str | None:
    best = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.lineno <= target.lineno and node.end_lineno >= target.end_lineno:
            if best is None or node.lineno >= best[0]:
                best = (node.lineno, node.name)
    return best[1] if best else None


def _humanise_func(name: str) -> str:
    words = name.lstrip("_").split("_")
    if words and words[0] in {"get", "fetch", "list", "load", "retrieve"}:
        words = ["Loading"] + [w for w in words[1:] if w]
    elif words and words[0] in {"create", "add"}:
        words = ["Creating"] + [w for w in words[1:] if w]
    elif words and words[0] in {"update", "edit"}:
        words = ["Updating"] + [w for w in words[1:] if w]
    elif words and words[0] in {"delete", "remove"}:
        words = ["Deleting"] + [w for w in words[1:] if w]
    else:
        words = [words[0].capitalize()] + words[1:] if words else ["Processing"]
    text = " ".join(words)
    return text[0].upper() + text[1:] if text else "Processing"


def _names_in(detail: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(detail) if isinstance(n, ast.Name)} - {"str"}


def derive_what(detail_src: str, func_name: str | None) -> str:
    """Static 'what' phrase from the message prefix, e.g. 'Creating overtime'.

    Returns the replacement expression source for the `what` argument: either a
    plain quoted string or an f-string when caller-data interpolations survive.
    """
    s = detail_src.strip()
    prefix = "f" if s.startswith(("f'", 'f"')) else ""
    # Strip the string quotes to inspect the template.
    m = re.match(r"""^(f?)(['\"])((?:.|[\r\n])*)\2$""", s, re.S)
    if not m:
        return '"%s"' % _humanise_func(func_name or "process")
    _pfx, quote, template = m.group(1), m.group(2), m.group(3)
    # Drop the exception interpolation ({e} / {str(e)} / {error_detail}), plus a
    # dangling ": " or " - " separator left in front of it.
    template = re.sub(r"\s*[:\-–—]\s*\{(?:str\(e\)|e|error_detail)\}\s*$", "", template)
    template = re.sub(r"\{(?:str\(e\)|e|error_detail)\}", "", template).strip()
    template = re.sub(r"^Error\s+", "", template).strip()
    template = re.sub(r"[\s:,\-]+$", "", template).strip()
    if not template or template.lower() in {"error", "failed"}:
        template = _humanise_func(func_name or "process")
        return '"%s"' % template.replace('"', '\\"')
    # server_error() appends "failed" itself — a template already ending in it
    # ("Analysis failed: {e}") would read "... failed failed", so strip it here.
    template = re.sub(r"\s+failed$", "", template).strip() or template
    template = template[0].upper() + template[1:]
    for verb, replacement in GERUND:
        if template.lower().startswith(verb + " "):
            template = replacement + template[len(verb):]
            break
    if "{" in template:
        return 'f"%s"' % template.replace('"', '\\"')
    return '"%s"' % template.replace('"', '\\"')


def find_leaks(path: pathlib.Path):
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    leaks = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "HTTPException"):
            continue
        if not _is_500(node):
            continue
        detail = _detail_arg(node)
        if detail is None:
            continue
        exc_name = _except_name(tree, node)
        names = _names_in(detail)
        leak_names = names & ({exc_name, "error_detail"} if exc_name else {"error_detail"})
        if not leak_names:
            continue
        # The raise statement spans the call; find it for clean replacement.
        stmt = node
        for cand in ast.walk(tree):
            if isinstance(cand, ast.Raise) and cand.lineno <= node.lineno and cand.end_lineno >= node.end_lineno:
                if cand.lineno >= stmt.lineno if isinstance(stmt, ast.AST) and hasattr(stmt, "lineno") else True:
                    stmt = cand
        # Reconstruct detail source from the file for template surgery.
        detail_src = ast.get_source_segment(src, detail) or ""
        what = derive_what(detail_src.strip(), _enclosing_func(tree, node))
        leaks.append({
            "lineno": stmt.lineno, "end_lineno": stmt.end_lineno,
            "col": stmt.col_offset, "exc": exc_name or "e", "what": what,
            "old": "".join(lines[stmt.lineno - 1: stmt.end_lineno]),
        })
    return leaks, src


def apply_to_file(path: pathlib.Path, leaks) -> int:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    for leak in sorted(leaks, key=lambda l: l["lineno"], reverse=True):
        indent = " " * leak["col"]
        newline = "\r\n" if leak["old"].endswith("\r\n") else "\n"
        replacement = f'{indent}raise server_error({leak["exc"]}, {leak["what"]}){newline}'
        lines[leak["lineno"] - 1: leak["end_lineno"]] = [replacement]
    text = "".join(lines)
    if "from app.errors import server_error" not in text:
        # Insert after the last top-level `from app.... import ...` line.
        insert_at = None
        for i, line in enumerate(text.splitlines(keepends=True)):
            if re.match(r"from app\.[a-z_]+ import ", line):
                insert_at = i
        new_lines = text.splitlines(keepends=True)
        eol = "\r\n" if new_lines and new_lines[0].endswith("\r\n") else "\n"
        imp = f"from app.errors import server_error{eol}"
        if insert_at is not None:
            new_lines.insert(insert_at + 1, imp)
        else:
            # Fall back: after the last import line.
            last_imp = max(i for i, line in enumerate(new_lines)
                           if line.startswith(("import ", "from ")))
            new_lines.insert(last_imp + 1, imp)
        text = "".join(new_lines)
    path.write_text(text, encoding="utf-8", newline="")
    return len(leaks)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--report"
    assert mode in ("--report", "--apply"), "usage: codemod_server_error.py [--report|--apply]"
    total = 0
    files = sorted(p for p in APP.rglob("*.py") if p.name != "errors.py")
    for path in files:
        leaks, _src = find_leaks(path)
        if not leaks:
            continue
        rel = path.relative_to(ROOT)
        if mode == "--report":
            print(f"{rel}: {len(leaks)}")
            for leak in leaks:
                first = leak["old"].strip().splitlines()[0][:100]
                print(f"  L{leak['lineno']}: {first}  ->  server_error({leak['exc']}, {leak['what']})")
        else:
            n = apply_to_file(path, leaks)
            print(f"{rel}: rewrote {n}")
        total += len(leaks)
    print(f"TOTAL: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
