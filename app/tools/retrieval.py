import logging
import os
import re
from pathlib import Path

from app.rag.chunker import Chunk, chunk_repo
from app.rag.keyword import BM25Index, diff_to_query

logger = logging.getLogger(__name__)

# Which search the reviewers get context from. Set in .env:
#   off     - no context, diff only (the behaviour before week 3)
#   keyword - BM25 only (instant, no models)
#   hybrid  - BM25 + embeddings, fused with RRF
#   rerank  - hybrid, then the cross-encoder picks the best
MODE = os.getenv("RETRIEVAL_MODE", "off").lower()

# How many related chunks to add, and a hard cap on context size.
# Every reviewer gets the same context, so 4,000 chars (~1k tokens) costs ~3k tokens per review.
TOP_K = int(os.getenv("RETRIEVAL_K", "4"))
MAX_CONTEXT_CHARS = int(os.getenv("RETRIEVAL_MAX_CHARS", "4000"))

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Models are slow to load, so each is loaded once and reused for every review.
_embedder = None
_reranker = None


# The new-file line numbers each diff touches. Removed lines count at the spot they were removed from.
def changed_lines(diff: str) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    path, new_line = None, None
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            path, new_line = None, None
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif m := HUNK.match(line):
            new_line = int(m.group(1))
        elif path is None or new_line is None or line.startswith(("--- ", "\\")):
            continue
        elif line.startswith("+"):
            out.setdefault(path, set()).add(new_line)
            new_line += 1
        elif line.startswith("-"):
            out.setdefault(path, set()).add(new_line)     # deleted here; the next surviving line is new_line
        else:
            new_line += 1                                 # unchanged context line
    return out


# Functions and methods the diff changes, found by line numbers. No search needed.
# <module> chunks are skipped: they are scattered across the file, so line overlap means little.
def changed_chunks(chunks: list[Chunk], diff: str) -> list[Chunk]:
    touched = changed_lines(diff)
    return [c for c in chunks
            if c.kind != "module" and any(c.start_line <= n <= c.end_line for n in touched.get(c.path, ()))]


# Related code from the rest of the repo, using the search MODE chosen in .env.
def related_chunks(chunks: list[Chunk], diff: str, exclude: set[str]) -> list[Chunk]:
    global _embedder, _reranker
    query = diff_to_query(diff)
    if MODE == "keyword":
        return [c for c, _ in BM25Index(chunks).search(query, TOP_K, exclude)]

    from app.rag.embeddings import Embedder
    from app.rag.hybrid import HybridIndex
    _embedder = _embedder or Embedder()
    index = HybridIndex(chunks, _embedder)
    if MODE == "hybrid":
        return [c for c, _ in index.search(query, TOP_K, exclude_ids=exclude)]
    if MODE == "rerank":
        from app.rag.rerank import Reranker, retrieve
        _reranker = _reranker or Reranker()
        return [c for c, _ in retrieve(index, _reranker, query, TOP_K, exclude_ids=exclude)]
    raise ValueError(f"Unknown RETRIEVAL_MODE: {MODE}")


# A chunk as the reviewer sees it: a header saying where it lives, then numbered lines.
def _format(c: Chunk, label: str) -> str:
    numbered = "\n".join(f"{c.start_line + i:>5}  {line}" for i, line in enumerate(c.code.splitlines()))
    return f"### {c.path} lines {c.start_line}-{c.end_line} ({c.name}) [{label}]\n{numbered}"


# The whole retrieval step: changed functions in full, then related code, within the size cap.
# Returns (context text for the prompt, ids of the chunks used). Empty when MODE is off.
def build_context(diff: str, repo_root: str | Path | None) -> tuple[str, list[str]]:
    if MODE == "off" or not repo_root:
        return "", []

    chunks = chunk_repo(repo_root)
    changed = changed_chunks(chunks, diff)
    related = related_chunks(chunks, diff, exclude={c.id for c in changed})

    parts, used, size = [], [], 0
    for c, label in [(c, "changed function, full") for c in changed] + [(c, "related") for c in related]:
        block = _format(c, label)
        if size + len(block) > MAX_CONTEXT_CHARS:
            continue                      # skip what doesn't fit; a smaller chunk later might
        parts.append(block)
        used.append(c.id)
        size += len(block)

    logger.info("retrieval (%s): %d changed + %d related chunk(s), %d chars",
                MODE, len(changed), len(related), size)
    return "\n\n".join(parts), used
