"""Run the appropriate parser on every supported file in data/input."""

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.ingestion_service import ingest_directory


INPUT_DIRECTORY = PROJECT_ROOT / "data" / "input"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--persist",
        action="store_true",
        help="upload successful parsed files and insert their records into Supabase",
    )
    args = parser.parse_args()
    results = ingest_directory(INPUT_DIRECTORY, persist_to_database=args.persist)
    successful = 0
    failed = 0

    for result in results:
        print("=" * 72)
        print(f"FILE: {result.file_name}")
        print(f"STATUS: {result.processing_status}")
        if result.error:
            print(f"ERROR: {result.error}")
        print("PARSED DATA:")
        print(result.model_dump_json(indent=2, exclude={"extracted_text", "provenance"}))
        if result.processing_status == "success":
            successful += 1
        else:
            failed += 1

    print("=" * 72)
    print(f"Total files parsed: {len(results)}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    return 1 if failed or not results else 0


if __name__ == "__main__":
    raise SystemExit(main())
