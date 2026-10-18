"""Codemod: `.dict(...)` -> `.model_dump(...)` on Pydantic models (Pydantic v2).

Usage (from backend/):
    .venv/Scripts/python.exe scripts/codemod_model_dump.py --report
    .venv/Scripts/python.exe scripts/codemod_model_dump.py --apply

Tokenize-based (not regex), so `.dict()` inside comments, docstrings and
string literals is left alone — only real attribute calls are rewritten.
`.dict()` is a deprecated Pydantic-v2 alias with identical behaviour, so
this is behaviour-preserving.
"""
import io
import pathlib
import sys
import tokenize

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app"


def find_sites(path: pathlib.Path):
    src = path.read_bytes().decode("utf-8")
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    sites = []
    for i, tok in enumerate(toks):
        if tok.type == tokenize.NAME and tok.string == "dict":
            prev = next((t for t in reversed(toks[:i]) if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)), None)
            nxt = next((t for t in toks[i + 1:] if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)), None)
            if (prev is not None and prev.type == tokenize.OP and prev.string == "."
                    and nxt is not None and nxt.type == tokenize.OP and nxt.string == "("):
                line = src.splitlines()[tok.start[0] - 1].strip()
                sites.append((tok.start[0], tok.start[1], line[:100]))
    return sites


def apply_to_file(path: pathlib.Path) -> int:
    src = path.read_bytes().decode("utf-8")
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    out = []
    n = 0
    for i, tok in enumerate(toks):
        if tok.type == tokenize.NAME and tok.string == "dict":
            prev = next((t for t in reversed(toks[:i]) if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)), None)
            nxt = next((t for t in toks[i + 1:] if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)), None)
            if (prev is not None and prev.type == tokenize.OP and prev.string == "."
                    and nxt is not None and nxt.type == tokenize.OP and nxt.string == "("):
                tok = tokenize.TokenInfo(tok.type, "model_dump", tok.start, tok.end, tok.line)
                n += 1
        out.append(tok)
    if n:
        path.write_bytes(tokenize.untokenize(out).encode("utf-8"))
    return n


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--report"
    assert mode in ("--report", "--apply"), "usage: codemod_model_dump.py [--report|--apply]"
    total = 0
    for path in sorted(APP.rglob("*.py")):
        if mode == "--report":
            sites = find_sites(path)
            if sites:
                print(f"{path.relative_to(ROOT)}: {len(sites)}")
                for lineno, _col, line in sites:
                    print(f"  L{lineno}: {line}")
                total += len(sites)
        else:
            n = apply_to_file(path)
            if n:
                print(f"{path.relative_to(ROOT)}: rewrote {n}")
            total += n
    print(f"TOTAL: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
