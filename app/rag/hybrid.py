import sys

from app.rag.chunker import Chunk, chunk_repo
from app.rag.embeddings import Embedder, VectorIndex
from app.rag.keyword import BM25Index, diff_to_query

# RRF constant. 60 is the value from the original paper and the common default:
# it stops the #1 result of one list from completely dominating the fused ranking.
RRF_K = 60


# Reciprocal Rank Fusion: merges ranked lists using positions only, not raw scores.
# BM25 scores (like 14.5) and cosine similarities (like 0.82) are on different scales and
# can't be added directly, but "3rd place" means the same thing in both lists.
# Each list gives a chunk 1 / (RRF_K + rank); a chunk ranked well in BOTH lists wins.
def rrf(ranked_lists: list[list[Chunk]], k: int = RRF_K) -> list[tuple[Chunk, float]]:
    scores: dict[str, float] = {}
    by_id: dict[str, Chunk] = {}
    for ranked in ranked_lists:
        for rank, chunk in enumerate(ranked, start=1):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1 / (k + rank)
            by_id[chunk.id] = chunk
    return sorted(((by_id[i], s) for i, s in scores.items()), key=lambda x: x[1], reverse=True)


# Keyword search + semantic search over the same chunks, fused with RRF.
class HybridIndex:
    def __init__(self, chunks: list[Chunk], embedder: Embedder | None = None):
        self.chunks = chunks
        self.keyword = BM25Index(chunks)
        self.vector = VectorIndex(chunks, embedder or Embedder())

    # Every chunk that made either method's top-n, fused with RRF (so up to 2n chunks).
    # This is the "shortlist" a reranker re-sorts: a chunk that is 1st in one list and absent
    # from the other is still on it, even if RRF alone would rank it low.
    def candidates(self, query: str, n: int = 20,
                   exclude_ids: set[str] = frozenset()) -> list[tuple[Chunk, float]]:
        kw = [c for c, _ in self.keyword.search(query, n, exclude_ids)]
        vec = [c for c, _ in self.vector.search(query, n, exclude_ids)]
        return rrf([kw, vec])

    # Each search method fetches a deeper list (candidates) than we finally return (k),
    # so a chunk that is only 8th in one list can still rise if it is strong in the other.
    def search(self, query: str, k: int = 5, candidates: int = 20,
               exclude_ids: set[str] = frozenset()) -> list[tuple[Chunk, float]]:
        return self.candidates(query, candidates, exclude_ids)[:k]


# Try it, and compare the three rankings side by side:
#   python -m app.rag.hybrid app "find the duplicate review check"
#   python -m app.rag.hybrid ..\pr-sandbox --diff evals\cases_hard\bug_discount_boundary.diff
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    repo, arg = sys.argv[1], sys.argv[2]
    query = diff_to_query(open(sys.argv[3], encoding="utf-8").read()) if arg == "--diff" else arg

    index = HybridIndex(chunk_repo(repo))
    kw = [c.id for c, _ in index.keyword.search(query, 5)]
    vec = [c.id for c, _ in index.vector.search(query, 5)]
    fused = index.search(query, 5)

    for title, ids in (("KEYWORD (BM25)", kw), ("SEMANTIC (embeddings)", vec)):
        print(f"\n{title}")
        for i, cid in enumerate(ids, 1):
            print(f"  {i}. {cid}")
    print("\nHYBRID (RRF)")
    for i, (c, s) in enumerate(fused, 1):
        tags = ("K" if c.id in kw else "-") + ("S" if c.id in vec else "-")
        print(f"  {i}. {c.id}  [{tags}]  {s:.4f}")
