"""Local sentence embeddings used by both indexing and retrieval."""

from functools import lru_cache
from typing import Sequence

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384


@lru_cache(maxsize=1)
def _load_model():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "RAG embeddings need sentence-transformers. Install project requirements first."
        ) from exc
    return SentenceTransformer(MODEL_NAME)


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    """Create 384-dimensional embeddings locally; the first call downloads the model."""
    if not texts:
        return []
    vectors = _load_model().encode(
        list(texts),
        batch_size=32,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vectors.tolist()
