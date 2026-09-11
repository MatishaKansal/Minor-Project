"""Opt-in Supabase integration test for one-business image batching."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.supabase_repository import get_or_create_default_business, get_supabase_client
from backend.parsers.image_parser import SUPPORTED_EXTENSIONS, parse_images_for_business

IMAGE_DIRECTORY = PROJECT_ROOT / "data" / "input" / "images"


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    image_paths = sorted(
        (
            path
            for path in IMAGE_DIRECTORY.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ),
        key=lambda path: path.name.lower(),
    ) if IMAGE_DIRECTORY.is_dir() else []

    print("BUSINESS IMAGE BATCH TEST")
    print("-------------------------")
    print("Business ID: automatically resolved")
    print(f"Total images: {len(image_paths)}")

    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        print("Successful: 0")
        print(f"Failed: {len(image_paths)}")
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")
        return 1

    try:
        client = get_supabase_client()
        business_id = get_or_create_default_business(client)
        print(f"Business ID: {business_id}")
        results = parse_images_for_business(business_id, IMAGE_DIRECTORY, file_type="receipt")
        successful = [result for result in results if result.processing_status == "success"]
        failed = [result for result in results if result.processing_status != "success"]
        print(f"Successful: {len(successful)}")
        print(f"Failed: {len(failed)}")
        for result in successful:
            print(f"{result.file_name}")
            print(f"  database_file_id: {result.database_file_id}")
            print(f"  database_evidence_id: {result.database_evidence_id}")

        source_rows = (
            client.table("source_file")
            .select("file_id,file_name,business_id")
            .eq("business_id", business_id)
            .in_("file_name", [path.name for path in image_paths])
            .execute()
            .data
        )
        evidence_rows = (
            client.table("financial_evidence")
            .select("evidence_id,file_id")
            .in_("file_id", [result.database_file_id for result in successful])
            .execute()
            .data
        )
        source_ids = {result.database_file_id for result in successful}
        resolved_business_ids = {row["business_id"] for row in source_rows}
        source_row_ids = {row["file_id"] for row in source_rows}
        evidence_file_ids = {row["file_id"] for row in evidence_rows}
        if len(successful) != len(image_paths):
            raise RuntimeError("one or more images failed to persist")
        if len(source_rows) < len(image_paths) or not source_ids.issubset(source_row_ids):
            raise RuntimeError("database does not contain the returned source_file row for every processed image")
        if len(evidence_rows) < len(successful) or not source_ids.issubset(evidence_file_ids):
            raise RuntimeError("database does not contain one financial_evidence row per processed image")
        if len(source_ids) != len(successful) or len({result.database_evidence_id for result in successful}) != len(successful):
            raise RuntimeError("database IDs were reused within the batch")
        if resolved_business_ids != {business_id} or any(row["file_id"] not in source_ids for row in evidence_rows):
            raise RuntimeError("batch records do not belong to one resolved business")

        print("Database verification: one source_file and financial_evidence record per image")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
