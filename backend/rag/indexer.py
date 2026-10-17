"""Create embeddings for document text and save chunks to Supabase."""

from typing import Any

from backend.rag.document_builder import split_text
from backend.rag.embeddings import embed_texts

TABLE_NAME = "document_chunks"
INSERT_BATCH_SIZE = 100


def index_document(
    *,
    text: str,
    business_id: str,
    source_file_id: str,
    file_name: str,
    client: Any | None = None,
) -> int:
    """Upsert every text chunk for one persisted source file; return chunk count."""
    from backend.database.supabase_repository import get_supabase_client

    supabase = client or get_supabase_client()
    chunks = split_text(text)
    if not chunks:
        supabase.table(TABLE_NAME).delete().eq("source_file_id", source_file_id).execute()
        return 0

    vectors = embed_texts([chunk.content for chunk in chunks])
    rows = [
        {
            "business_id": business_id,
            "source_file_id": source_file_id,
            "file_name": file_name,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "embedding": vector,
        }
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]

    for offset in range(0, len(rows), INSERT_BATCH_SIZE):
        supabase.table(TABLE_NAME).upsert(
            rows[offset : offset + INSERT_BATCH_SIZE],
            on_conflict="source_file_id,chunk_index",
        ).execute()

    # Remove trailing chunks if a document was reprocessed with less text.
    supabase.table(TABLE_NAME).delete().eq("source_file_id", source_file_id).gt(
        "chunk_index", len(chunks) - 1
    ).execute()
    return len(chunks)


def index_existing_documents(client: Any | None = None) -> int:
    """Index the latest saved extraction for each invoice/source file."""
    from backend.database.supabase_repository import get_supabase_client

    supabase = client or get_supabase_client()
    page_size = 500
    start = 0
    indexed_file_ids: set[str] = set()
    indexed_count = 0

    while True:
        response = (
            supabase.table("invoice_extraction")
            .select("invoice_id,raw_text,processed_at")
            .order("processed_at", desc=True)
            .range(start, start + page_size - 1)
            .execute()
        )
        extractions = response.data or []
        if not extractions:
            break

        for extraction in extractions:
            invoice_id = extraction.get("invoice_id")
            text = extraction.get("raw_text") or ""
            if not invoice_id or not text.strip():
                continue

            invoice_response = (
                supabase.table("invoice")
                .select("file_id,business_id")
                .eq("invoice_id", invoice_id)
                .limit(1)
                .execute()
            )
            invoices = invoice_response.data or []
            if not invoices:
                continue
            invoice = invoices[0]
            file_id = invoice.get("file_id")
            business_id = invoice.get("business_id")
            if not file_id or not business_id or file_id in indexed_file_ids:
                continue

            source_response = (
                supabase.table("source_file")
                .select("file_name")
                .eq("file_id", file_id)
                .limit(1)
                .execute()
            )
            sources = source_response.data or []
            if not sources:
                continue

            indexed_count += index_document(
                text=text,
                business_id=business_id,
                source_file_id=file_id,
                file_name=sources[0].get("file_name") or "unknown",
                client=supabase,
            )
            indexed_file_ids.add(file_id)

        if len(extractions) < page_size:
            break
        start += page_size

    return indexed_count
