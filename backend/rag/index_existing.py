"""Console command to index documents already saved in Supabase."""

from backend.rag.indexer import index_existing_documents


def main() -> None:
    count = index_existing_documents()
    print(f"Indexed {count} document chunk(s) from existing Supabase records.")


if __name__ == "__main__":
    main()
