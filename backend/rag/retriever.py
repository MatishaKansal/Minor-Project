"""Retrieve semantically similar document chunks from Supabase."""

from typing import Any

from backend.rag.embeddings import embed_texts


def retrieve(
    query: str,
    business_id: str,
    *,
    match_count: int = 5,
    min_similarity: float = 0.35,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """Return matching chunks for one business, without generating an LLM answer."""
    if not query.strip():
        raise ValueError("Query cannot be empty")
    if not business_id.strip():
        raise ValueError("business_id cannot be empty")

    from backend.database.supabase_repository import get_supabase_client

    supabase = client or get_supabase_client()
    query_embedding = embed_texts([query])[0]
    response = supabase.rpc(
        "match_document_chunks",
        {
            "query_embedding": query_embedding,
            "match_business_id": business_id,
            "match_count": match_count,
            "min_similarity": min_similarity,
        },
    ).execute()
    return response.data or []
