"""Opt-in Supabase persistence verification for the image parser."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.database.supabase_repository import get_or_create_default_business, get_supabase_client
from backend.parsers.image_parser import parse_image

IMAGE_PATH = PROJECT_ROOT / "data" / "input" / "images" / "test.jpg"


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    print("DATABASE PERSISTENCE TEST")
    print("-------------------------")
    print(f"File: {IMAGE_PATH}")
    print("Business ID: automatically resolved")

    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        print("Status: NOT RUN")
        print("DATABASE INSERT: FAILED")
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")
        return 1
    if not IMAGE_PATH.is_file():
        print("Status: NOT RUN")
        print("DATABASE INSERT: FAILED")
        print(f"Error: image not found: {IMAGE_PATH}")
        return 1

    try:
        client = get_supabase_client()
        business_id = get_or_create_default_business(client)
        print(f"Business ID: {business_id}")
        result = parse_image(
            IMAGE_PATH,
            business_id=business_id,
            persist_to_database=True,
            file_type="receipt",
        )
        print(f"Status: {result.processing_status}")
        print(f"Database File ID: {result.database_file_id}")
        print(f"Database Evidence ID: {result.database_evidence_id}")
        if result.processing_status != "success" or not result.database_file_id or not result.database_evidence_id:
            print("DATABASE INSERT: FAILED")
            print(f"Error: {result.error or 'parser did not return database IDs'}")
            return 1

        source_rows = (
            client.table("source_file")
            .select("file_id,business_id")
            .eq("file_id", result.database_file_id)
            .execute()
            .data
        )
        evidence_rows = (
            client.table("financial_evidence")
            .select("evidence_id,file_id")
            .eq("evidence_id", result.database_evidence_id)
            .execute()
            .data
        )
        provenance_rows = (
            client.table("provenance")
            .select("provenance_id,evidence_id,file_id,field_name,page_number,source_text,extraction_method,confidence_score,location")
            .eq("evidence_id", result.database_evidence_id)
            .execute()
            .data
        )
        if not source_rows or not evidence_rows or not provenance_rows:
            raise RuntimeError("created source_file, financial_evidence, or provenance rows were not found")
        if source_rows[0]["business_id"] != business_id:
            raise RuntimeError("created source file does not belong to the resolved business")
        if evidence_rows[0]["file_id"] != source_rows[0]["file_id"]:
            raise RuntimeError("financial evidence is not linked to the created source file")
        if any(row.get("field_name") is None for row in provenance_rows):
            raise RuntimeError("one or more newly inserted provenance rows has NULL field_name")
        expected_fields = {"date", "amount", "currency", "party_name"}
        stored_fields = {row["field_name"] for row in provenance_rows}
        if not expected_fields.issubset(stored_fields):
            raise RuntimeError(f"missing expected provenance fields: {expected_fields - stored_fields}")
        if any(
            row["evidence_id"] != result.database_evidence_id
            or row["file_id"] != result.database_file_id
            for row in provenance_rows
        ):
            raise RuntimeError("provenance rows are linked to the wrong evidence or source file")

        print("Retrieved provenance rows:")
        for row in provenance_rows:
            print(
                {
                    "provenance_id": row["provenance_id"],
                    "field_name": row["field_name"],
                    "source_text": row["source_text"],
                    "page_number": row["page_number"],
                    "extraction_method": row["extraction_method"],
                    "confidence_score": row["confidence_score"],
                    "location": row["location"],
                }
            )

        print("DATABASE INSERT: SUCCESS")
        return 0
    except Exception as exc:
        print("Status: failed")
        print("DATABASE INSERT: FAILED")
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
