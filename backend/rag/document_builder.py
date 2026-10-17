"""Split extracted document text into overlapping chunks for semantic search."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentChunk:
    chunk_index: int
    content: str


def split_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> list[DocumentChunk]:
    """Split text near word boundaries, keeping a small overlap between chunks."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size")

    clean_text = " ".join((text or "").split())
    if not clean_text:
        return []

    chunks: list[DocumentChunk] = []
    start = 0
    while start < len(clean_text):
        end = min(start + chunk_size, len(clean_text))
        if end < len(clean_text):
            boundary = clean_text.rfind(" ", start, end)
            if boundary > start:
                end = boundary

        content = clean_text[start:end].strip()
        if content:
            chunks.append(DocumentChunk(chunk_index=len(chunks), content=content))
        if end >= len(clean_text):
            break
        start = max(end - overlap, start + 1)

    return chunks
