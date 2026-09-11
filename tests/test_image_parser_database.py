import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from backend.database.supabase_repository import get_supabase_client
from backend.parsers.image_parser import parse_image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_PATH = PROJECT_ROOT / "data" / "input" / "images" / "test.jpg"


def _integration_configuration():
    load_dotenv(PROJECT_ROOT / ".env")
    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        pytest.skip("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for Supabase integration tests")
    if not IMAGE_PATH.is_file():
        pytest.skip(f"Integration image is missing: {IMAGE_PATH}")
    return True


@pytest.mark.integration
def test_image_parser_persists_and_verifies_supabase_records():
    _integration_configuration()
    client = get_supabase_client()
    result = parse_image(
        IMAGE_PATH,
        persist_to_database=True,
        file_type="receipt",
    )

    assert result.processing_status == "success"
    assert result.database_file_id
    assert result.database_evidence_id

    source_rows = (
        client.table("source_file")
        .select("file_id,business_id,file_name,file_type")
        .eq("file_id", result.database_file_id)
        .execute()
        .data
    )
    evidence_rows = (
        client.table("financial_evidence")
        .select("evidence_id,file_id,amount,currency,party_name")
        .eq("evidence_id", result.database_evidence_id)
        .execute()
        .data
    )
    provenance_rows = (
        client.table("provenance")
        .select("evidence_id,file_id,field_name,source_text,page_number,extraction_method,location")
        .eq("evidence_id", result.database_evidence_id)
        .execute()
        .data
    )

    assert source_rows
    assert source_rows[0]["file_name"] == IMAGE_PATH.name
    assert source_rows[0]["file_type"] == "receipt"
    assert evidence_rows and evidence_rows[0]["file_id"] == result.database_file_id
    assert evidence_rows[0]["file_id"] == source_rows[0]["file_id"]
    assert provenance_rows
    assert all(row["field_name"] is not None for row in provenance_rows)
    assert any(row["field_name"] == "amount" for row in provenance_rows)
