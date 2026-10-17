"""Console entry point for retrieving and printing relevant document chunks."""

import argparse

from backend.database.supabase_repository import get_or_create_default_business, get_supabase_client
from backend.rag.retriever import retrieve


def main() -> None:
    parser = argparse.ArgumentParser(description="Search saved financial documents with RAG retrieval.")
    parser.add_argument("query", help="Question or phrase to search for")
    parser.add_argument("--business-id", help="Business UUID; defaults to the only configured business")
    parser.add_argument("--count", type=int, default=5, help="Maximum matching chunks to print (default: 5)")
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=0.35,
        help="Minimum cosine similarity from 0 to 1 (default: 0.35)",
    )
    args = parser.parse_args()

    client = get_supabase_client()
    business_id = args.business_id or get_or_create_default_business(client)
    results = retrieve(
        args.query,
        business_id,
        match_count=args.count,
        min_similarity=args.min_similarity,
        client=client,
    )

    if not results:
        print("No matching document chunks found.")
        return

    for index, result in enumerate(results, start=1):
        print(f"\n[{index}] {result.get('file_name', 'Unknown file')}")
        print(f"Similarity: {result.get('similarity', 0):.3f}")
        print(f"Source file ID: {result.get('source_file_id', 'unknown')}")
        print(f"Chunk {result.get('chunk_index', '?')}: {result.get('content', '')}")


if __name__ == "__main__":
    main()
