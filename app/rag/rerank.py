import os
import sys

from app.rag.chunker import Chunk, chunk_repo
from app.rag.embeddings import chunk_text
from app.rag.hybrid import HybridIndex
from app.rag.keyword import diff_to_query

# A small cross-encoder (~150 MB, CPU). It reads up to 8k tokens, so whole functions fit.
# Swap via .env, e.g. RERANK_MODEL=Xenova/ms-marco-MiniLM-L-6-v2 (80 MB, faster, shorter input).
MODEL_NAME = os.getenv("RERANK_MODEL", "jinaai/jina-reranker-v1-turbo-en")

# Diff queries can be long; the reranker reads query + chunk together, so keep the query bounded.
MAX_QUERY_CHARS = 1500


# A cross-encoder: reads the query and ONE chunk together and outputs a relevance score.
# Unlike embeddings (query and chunk turned into vectors separately, then compared),
# it sees both texts at once, so it can judge how they relate, word by word.
# Much more accurate, but it needs one model run per chunk, so it only re-sorts a shortlist.
class Reranker:
    def __init__(self, model_name: str = MODEL_NAME):
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        self.model_name = model_name
        self.model = TextCrossEncoder(model_name)

    # Returns the top-k (chunk, score) pairs, best first. Scores are only comparable
    # within one call (higher = more relevant), not across queries or models.
    def rerank(self, query: str, chunks: list[Chunk], k: int = 5) -> list[tuple[Chunk, float]]:
        if not chunks:
            return []
        scores = list(self.model.rerank(query[:MAX_QUERY_CHARS], [chunk_text(c) for c in chunks]))
        ranked = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)
        return [(c, float(s)) for c, s in ranked[:k]]


# The full retrieval pipeline: hybrid search builds the shortlist, the reranker picks the best.
def retrieve(index: HybridIndex, reranker: Reranker, query: str, k: int = 5, shortlist: int = 20,
             exclude_ids: set[str] = frozenset()) -> list[tuple[Chunk, float]]:
    candidates = [c for c, _ in index.candidates(query, shortlist, exclude_ids)]
    return reranker.rerank(query, candidates, k)


# Try it, and compare hybrid alone with hybrid + reranking:
#   python -m app.rag.rerank app "find the duplicate review check"
#   python -m app.rag.rerank ..\pr-sandbox --diff evals\cases_hard\bug_discount_boundary.diff
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    repo, arg = sys.argv[1], sys.argv[2]
    query = diff_to_query(open(sys.argv[3], encoding="utf-8").read()) if arg == "--diff" else arg

    index = HybridIndex(chunk_repo(repo))
    shortlist = [c for c, _ in index.candidates(query, 20)]
    print(f"Shortlist: {len(shortlist)} chunks from hybrid search\n")

    print("HYBRID (RRF) top 5")
    for i, c in enumerate(shortlist[:5], 1):
        print(f"  {i}. {c.id}")

    reranked = Reranker().rerank(query, shortlist, 5)
    position = {c.id: i for i, c in enumerate(shortlist, 1)}
    print("\nRERANKED top 5   (was #N in hybrid)")
    for i, (c, s) in enumerate(reranked, 1):
        print(f"  {i}. {c.id}  score {s:.3f}  (was #{position[c.id]})")
