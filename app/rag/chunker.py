'''
chunker.py splits a repo's Python code into small searchable pieces called chunks. A chunk is one function, one method, or a file's remaining top-level code.
Searching whole functions works better than searching whole files, and it means the reviewer can later be given just the relevant code.

'''

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Folders that never contain the project's own code.
SKIP_DIRS = {".git", ".venv", "venv", "env", "__pycache__", "node_modules", "build", "dist", ".pytest_cache"}


# One searchable piece of the repo: a function, a method, or a file's top-level code.
@dataclass
class Chunk:
    id: str                 # unique, e.g. "app/services/billing.py::total"
    path: str               # file path relative to the repo root, with forward slashes
    name: str               # "total", "UserRepository.search", or "<module>"
    kind: str               # "function", "method" or "module"
    start_line: int
    end_line: int
    code: str
    calls: list[str] = field(default_factory=list)   # names this chunk calls, used to find callers later


# Collects the names a piece of code calls: foo(), self.repo.search() -> "search", etc.
def _called_names(node: ast.AST) -> list[str]:
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            if isinstance(sub.func, ast.Name):
                names.add(sub.func.id)            # plain call: total(invoice)
            elif isinstance(sub.func, ast.Attribute):
                names.add(sub.func.attr)          # method call: self.repo.search(term)
    return sorted(names)


def _source(lines: list[str], node: ast.AST) -> tuple[int, int, str]:
    # Include decorators (@router.post(...)) so the chunk shows how the function is used.
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    # Include the comment block written directly above the function: it usually explains
    # what the function does, which is exactly what search needs to match on.
    # Without this, those comments land in the file's <module> chunk instead.
    while start > 1 and lines[start - 2].strip().startswith("#"):
        start -= 1
    end = node.end_lineno
    return start, end, "\n".join(lines[start - 1:end])


# Splits one Python file into chunks: one per function and method, plus one for top-level code.
def chunk_file(path: str, text: str) -> list[Chunk]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []   # not valid Python (or a different Python version): skip rather than crash
    lines = text.splitlines()
    chunks, covered = [], set()

    def add(node, name, kind):
        start, end, code = _source(lines, node)
        chunks.append(Chunk(f"{path}::{name}", path, name, kind, start, end, code, _called_names(node)))
        covered.update(range(start, end + 1))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node, node.name, "function")
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(item, f"{node.name}.{item.name}", "method")

    # Everything not inside a function: imports, constants, class headers and fields.
    # Useful context too (e.g. TAX_RATE = 0.14), so it becomes one "<module>" chunk.
    rest = [(i, line) for i, line in enumerate(lines, 1) if i not in covered and line.strip()]
    if rest:
        chunks.append(Chunk(f"{path}::<module>", path, "<module>", "module",
                            rest[0][0], rest[-1][0], "\n".join(line for _, line in rest)))
    return chunks


# Walks a repo folder and chunks every Python file in it.
def chunk_repo(root: str | Path) -> list[Chunk]:
    root = Path(root)
    chunks = []
    for file in sorted(root.rglob("*.py")):
        rel = file.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        chunks.extend(chunk_file(rel.as_posix(), text))
    return chunks


# Try it:  python -m app.rag.chunker ..\pr-sandbox
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    chunks = chunk_repo(repo)
    for c in chunks:
        print(f"{c.id:55} lines {c.start_line:>3}-{c.end_line:<3} calls: {', '.join(c.calls[:6])}")
    print(f"\n{len(chunks)} chunks from {len({c.path for c in chunks})} files")
