import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np

from app.rag.chunker import Chunk, chunk_repo

# A small, general-purpose embedding model (~67 MB, runs on CPU, no GPU needed).
# Downloaded once on first use. Swap it via .env to compare models in your evals,
# e.g. EMBED_MODEL=jinaai/jina-embeddings-v2-base-code (trained on code, ~640 MB).
MODEL_NAME = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")

# Embeddings are saved here so unchanged chunks are never embedded twice.
CACHE_DIR = Path(".rag_cache")

# Embedding models read a limited amount of text; very long chunks are cut to this many characters.
MAX_CHARS = 2000


# Turns text into vectors (lists of numbers) that capture meaning.
# Texts that mean similar things get vectors pointing in similar directions.
class Embedder:
    def __init__(self, model_name: str = MODEL_NAME):
        from fastembed import TextEmbedding   # imported here so the rest of the file loads fast
        self.model_name = model_name
        self.model = TextEmbedding(model_name)

    # Chunks of code are "passages"; search text is a "query". Some models embed the two
    # slightly differently, which improves matching between a short query and longer code.
    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return np.array(list(self.model.passage_embed(texts)), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return np.array(next(iter(self.model.query_embed(text))), dtype=np.float32)


# What actually gets embedded for a chunk: where it lives, its name, and its code.
def chunk_text(c: Chunk) -> str:
    return f"{c.path} {c.name}\n{c.code}"[:MAX_CHARS]


# Unit length, so a simple dot product between two vectors equals their cosine similarity.
def _normalize(m: np.ndarray) -> np.ndarray:
    return m / np.clip(np.linalg.norm(m, axis=-1, keepdims=True), 1e-12, None)


# Semantic search over chunks: finds code by meaning, not exact words.
class VectorIndex:
    def __init__(self, chunks: list[Chunk], embedder: Embedder, cache_dir: Path = CACHE_DIR):
        self.chunks, self.embedder = chunks, embedder
        self.matrix = _normalize(self._embed_with_cache(chunks, cache_dir))

    # Looks up each chunk's embedding in the cache by a hash of its text; only new or changed
    # chunks are sent to the model. Re-indexing a repo after a small change takes seconds.
    def _embed_with_cache(self, chunks: list[Chunk], cache_dir: Path) -> np.ndarray:
        safe_name = self.embedder.model_name.replace("/", "__")
        cache_file = cache_dir / f"{safe_name}.json"
        cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}

        texts = [chunk_text(c) for c in chunks]
        keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in cache]
        if missing:
            print(f"Embedding {len(missing)} new chunk(s) with {self.embedder.model_name}...", file=sys.stderr)
            vectors = self.embedder.embed_passages([texts[i] for i in missing])
            for i, v in zip(missing, vectors):
                cache[keys[i]] = v.tolist()
            cache_dir.mkdir(exist_ok=True)
            cache_file.write_text(json.dumps(cache))
        if not chunks:
            return np.zeros((0, 1), dtype=np.float32)
        return np.array([cache[k] for k in keys], dtype=np.float32)

    # Returns the top-k (chunk, similarity) pairs, best first. Similarity is between -1 and 1.
    def search(self, query: str, k: int = 5, exclude_ids: set[str] = frozenset()) -> list[tuple[Chunk, float]]:
        if not self.chunks:
            return []
        q = _normalize(self.embedder.embed_query(query[:MAX_CHARS]))
        sims = self.matrix @ q          # one dot product per chunk = cosine similarity
        order = np.argsort(-sims)
        return [(self.chunks[i], float(sims[i])) for i in order if self.chunks[i].id not in exclude_ids][:k]


# Try it:  python -m app.rag.embeddings app "find the duplicate review check"
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    index = VectorIndex(chunk_repo(sys.argv[1]), Embedder())
    for chunk, sim in index.search(sys.argv[2]):
        print(f"{sim:6.3f}  {chunk.id}  (lines {chunk.start_line}-{chunk.end_line})")
