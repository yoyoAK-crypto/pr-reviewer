import math
import re
import sys
from collections import Counter

from app.rag.chunker import Chunk, chunk_repo

# Words that appear in almost all Python code, so they say nothing about what a chunk is about.
STOPWORDS = {
    "def", "class", "return", "self", "import", "from", "as", "if", "else", "elif", "for", "while", "in",
    "is", "not", "and", "or", "none", "true", "false", "with", "try", "except", "raise", "pass", "lambda",
    "async", "await", "str", "int", "float", "bool", "list", "dict", "the", "a", "an", "to", "of", "it",
}

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


# Splits code into searchable words. Identifiers are kept whole AND split into parts,
# so "get_user_by_id" matches a search for "get_user_by_id", "user" or "id",
# and "UserRepository" matches "user" and "repository".
def tokenize(text: str) -> list[str]:
    tokens = []
    for ident in IDENT.findall(text):
        parts = [p.lower() for piece in ident.split("_") for p in CAMEL.findall(piece)]
        whole = ident.lower()
        tokens.append(whole)
        if len(parts) > 1:
            tokens.extend(parts)
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


# BM25: the classic keyword-ranking formula used by search engines like Elasticsearch.
# A chunk scores higher when it contains the query's words often (tf), when those words are
# rare across the repo (idf), and it is not rewarded just for being long (length normalisation).
class BM25Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        # The chunk's name is counted 3 extra times: a match on the function name means more
        # than a match somewhere in its body.
        self.docs = [Counter(tokenize(c.code) + tokenize(c.path) + tokenize(c.name) * 3) for c in chunks]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_len = sum(self.lengths) / len(self.lengths) if chunks else 0
        df = Counter(term for d in self.docs for term in d)          # how many chunks contain each term
        n = len(chunks)
        self.idf = {t: math.log((n - f + 0.5) / (f + 0.5) + 1) for t, f in df.items()}

    def score(self, query_terms: list[str], i: int) -> float:
        doc, length, s = self.docs[i], self.lengths[i], 0.0
        for t in set(query_terms):
            tf = doc.get(t, 0)
            if tf:
                norm = tf + self.k1 * (1 - self.b + self.b * length / self.avg_len)
                s += self.idf[t] * tf * (self.k1 + 1) / norm
        return s

    # Returns the top-k (chunk, score) pairs for a query, best first.
    def search(self, query: str, k: int = 5, exclude_ids: set[str] = frozenset()) -> list[tuple[Chunk, float]]:
        terms = tokenize(query)
        scored = [(c, self.score(terms, i)) for i, c in enumerate(self.chunks) if c.id not in exclude_ids]
        scored = [(c, s) for c, s in scored if s > 0]
        return sorted(scored, key=lambda x: x[1], reverse=True)[:k]


# Builds a search query from a diff: the identifiers on added and removed lines.
# Removed lines matter too: if a diff deletes or renames `discount`, we still want its callers.
def diff_to_query(diff: str) -> str:
    changed = [line[1:] for line in diff.splitlines()
               if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    return " ".join(changed)


# Try it:
#   python -m app.rag.keyword ..\pr-sandbox "discount bulk"
#   python -m app.rag.keyword ..\pr-sandbox --diff evals\cases_hard\bug_discount_boundary.diff
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    repo, arg = sys.argv[1], sys.argv[2]
    query = diff_to_query(open(sys.argv[3], encoding="utf-8").read()) if arg == "--diff" else arg

    index = BM25Index(chunk_repo(repo))
    print(f"Query terms: {sorted(set(tokenize(query)))[:20]}\n")
    for chunk, score in index.search(query, k=5):
        print(f"{score:6.2f}  {chunk.id}  (lines {chunk.start_line}-{chunk.end_line})")
