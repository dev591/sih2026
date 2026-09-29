"""
Code map for the assistant: reads every source file once, so the assistant "knows the whole codebase"
without pasting 500 KB of code into a model with an 8K-token window.

  python sih-project/tools/codemap.py            # (re)build tools/codemap.json
  python sih-project/tools/codemap.py "how are false alarms controlled"     # search

Two things come out of it:
  * overview()  a compact one-line-per-module map (what each file is for, how to run it)
  * search(q)   the actual code/docstring chunks most relevant to a question, with file:line

Read-only. Nothing here imports or runs the project's code — it only parses text (ast for Python).
"""
from __future__ import annotations

import ast
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]            # sih-project/
OUT = Path(__file__).with_name("codemap.json")
SKIP = {"__pycache__", "node_modules", "weights", "data", "dist", ".git", "docs"}
EXTS = {".py", ".yaml", ".yml", ".ts", ".tsx"}
MAX_CHUNK = 1800


def _skip(p: Path) -> bool:
    return any(part in SKIP for part in p.relative_to(ROOT).parts)


def _first(s: str | None, n: int = 240) -> str:
    return " ".join((s or "").split())[:n]


def _py(path: Path, text: str) -> tuple[dict, list[dict]]:
    rel = path.relative_to(ROOT).as_posix()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {"path": rel, "purpose": "(could not parse)", "defs": [], "cli": []}, []
    lines = text.splitlines()
    doc = ast.get_docstring(tree) or ""
    defs, chunks = [], []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "def"
            d = _first(ast.get_docstring(node), 140)
            defs.append(f"{kind} {node.name}: {d}" if d else f"{kind} {node.name}")
            seg = "\n".join(lines[node.lineno - 1: min(node.end_lineno or node.lineno, node.lineno + 45)])
            chunks.append({"path": rel, "line": node.lineno, "title": f"{kind} {node.name}", "text": seg[:MAX_CHUNK]})
    cli = re.findall(r'add_argument\(\s*"(--[\w-]+)"[^)]*?(?:default=([^,)]+))?', text)
    usage = re.findall(r"python(?:3)? (?:-u )?(?:-m )?[\w./-]+[^\n\"']*", doc)
    if doc:
        chunks.insert(0, {"path": rel, "line": 1, "title": "module docstring", "text": doc[:MAX_CHUNK]})
    return {"path": rel, "purpose": _first(doc, 420), "defs": defs[:40],
            "cli": [f"{a} (default {d.strip()})" if d else a for a, d in cli],
            "run": [u.strip() for u in usage[:4]], "lines": len(lines)}, chunks


def _text(path: Path, text: str) -> tuple[dict, list[dict]]:
    rel = path.relative_to(ROOT).as_posix()
    head = "\n".join(text.splitlines()[:12])
    chunks, buf, start = [], [], 1
    for i, ln in enumerate(text.splitlines(), 1):
        buf.append(ln)
        if sum(len(x) for x in buf) > MAX_CHUNK:
            chunks.append({"path": rel, "line": start, "title": "block", "text": "\n".join(buf)})
            buf, start = [], i + 1
    if buf:
        chunks.append({"path": rel, "line": start, "title": "block", "text": "\n".join(buf)})
    return {"path": rel, "purpose": _first(head, 300), "defs": [], "cli": [], "lines": len(text.splitlines())}, chunks


def build() -> dict:
    mods, chunks = [], []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file() or p.suffix not in EXTS or _skip(p):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        m, c = (_py if p.suffix == ".py" else _text)(p, text)
        mods.append(m)
        chunks += c
    data = {"modules": mods, "chunks": chunks}
    OUT.write_text(json.dumps(data), encoding="utf-8")
    return data


def load() -> dict:
    return json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else build()


_STOP = {"the", "and", "how", "are", "is", "does", "do", "what", "where", "which", "code", "file", "files", "module",
         "function", "script", "source", "this", "that", "for", "with", "our", "your", "you", "can", "use", "used",
         "work", "works", "explain", "tell", "about", "controlled", "implemented", "into", "from", "when"}


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def _tok(s: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9_]{2,}", s.lower()) if w not in _STOP]


def search(q: str, k: int = 3, data: dict | None = None) -> list[dict]:
    """BM25-ish keyword ranking over code chunks. Returns the top k chunks."""
    data = data or load()
    chunks = data["chunks"]
    df = Counter()
    toks = []
    for c in chunks:
        t = _tok((c["path"].replace("/", " ").replace("_", " ") + " ") * 3 + (c["title"] + " ") * 2 + c["text"])
        toks.append(Counter(t))
        df.update(set(t))
    n, qt = len(chunks), _tok(q)
    scored = []
    for c, tf in zip(chunks, toks):
        s = sum((tf[w] / (tf[w] + 1.5)) * math.log(1 + n / (1 + df[w])) for w in qt if w in tf)
        if c["path"].endswith(".py") and not c["path"].startswith("tools/"):
            s *= 1.4                      # the project's own logic beats the assistant tooling and the UI
        if s > 0:
            scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:k]]


def overview(max_chars: int = 5200) -> str:
    """One line per module — enough for the model to know where everything is."""
    out = []
    for m in load()["modules"]:
        if m["path"].endswith("__init__.py"):
            continue
        purpose = m["purpose"].split(". ")[0][:130]
        out.append(f"- {m['path']}: {purpose}")
    s = "\n".join(out)
    return s if len(s) <= max_chars else s[:max_chars].rsplit("\n", 1)[0] + "\n- ..."


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for c in search(" ".join(sys.argv[1:]), 4):
            print(f"\n## {c['path']}:{c['line']}  {c['title']}\n{c['text'][:500]}")
    else:
        d = build()
        print(f"indexed {len(d['modules'])} files, {len(d['chunks'])} chunks -> {OUT}")
