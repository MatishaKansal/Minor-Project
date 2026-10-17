"""FastAPI endpoints used by the document retrieval frontend."""

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.parsers.bank_csv_parser import SUPPORTED_EXTENSIONS as BANK_CSV_EXTENSIONS
from backend.rag.retriever import retrieve
from backend.services.ingestion_service import ALL_SUPPORTED_EXTENSIONS, ingest_file

LOGGER = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
PERSISTABLE_EXTENSIONS = ALL_SUPPORTED_EXTENSIONS - BANK_CSV_EXTENSIONS

app = FastAPI(title="Financial Document Retrieval API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def _event(event_type: str, **payload: Any) -> str:
    data = json.dumps({"type": event_type, **payload}, ensure_ascii=False)
    return f"data: {data}\n\n"


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/query")
async def query_documents(
    question: str = Form(...),
    files: list[UploadFile] | None = File(default=None),
    business_id: str | None = Form(default=None),
) -> StreamingResponse:
    """Persist uploaded files, retrieve relevant chunks, and stream progress/results."""
    clean_question = question.strip()
    if not clean_question:
        return StreamingResponse(
            iter([_event("error", message="Please enter a question.")]),
            media_type="text/event-stream",
        )

    upload_payloads: list[tuple[str, bytes]] = []
    unsupported_uploads: list[str] = []
    for upload in files or []:
        name = Path(upload.filename or "upload").name
        suffix = Path(name).suffix.lower()
        if suffix not in PERSISTABLE_EXTENSIONS:
            unsupported_uploads.append(name)
            continue
        content = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            upload_payloads.append((name, b""))
            continue
        upload_payloads.append((name, content))

    requested_business_id = business_id.strip() if business_id else None

    def stream_events():
        yield _event("status", message="Your question is received.")
        saved_files: list[dict[str, str]] = []
        indexed_files: list[dict[str, str]] = []
        file_warnings: list[dict[str, str]] = [
            {"file_name": name, "message": "This file type is not supported."}
            for name in unsupported_uploads
        ]

        if upload_payloads:
            yield _event("status", message="Parsing and saving your files. This can take a little while.")
            for name, content in upload_payloads:
                yield _event("status", message=f"Parsing, saving and indexing {name}…")
                if not content:
                    file_warnings.append({"file_name": name, "message": "File is empty or exceeds the 20 MB limit."})
                    continue

                try:
                    with tempfile.TemporaryDirectory(prefix="rag-upload-") as temporary_directory:
                        temporary_path = Path(temporary_directory) / name
                        temporary_path.write_bytes(content)
                        evidence = ingest_file(
                            temporary_path,
                            persist_to_database=True,
                            business_id=requested_business_id,
                        )
                    if evidence.processing_status != "success":
                        file_warnings.append({
                            "file_name": name,
                            "message": evidence.error or "The file could not be parsed and saved.",
                        })
                        continue

                    saved_file = {
                        "file_name": name,
                        "file_id": evidence.database_file_id or "",
                    }
                    saved_files.append(saved_file)
                    if evidence.database_file_id:
                        from backend.database.supabase_repository import get_supabase_client

                        check_client = get_supabase_client()
                        chunk_rows = (
                            check_client.table("document_chunks")
                            .select("chunk_id")
                            .eq("source_file_id", evidence.database_file_id)
                            .limit(1)
                            .execute()
                            .data
                            or []
                        )
                        if chunk_rows:
                            indexed_files.append(saved_file)
                            yield _event("file_indexed", file_name=name)
                        else:
                            file_warnings.append({
                                "file_name": name,
                                "message": "Saved, but no searchable chunks were found. Check RAG indexing configuration.",
                            })
                    else:
                        file_warnings.append({
                            "file_name": name,
                            "message": "Parsed, but the database did not return a saved file ID.",
                        })
                except Exception as exc:
                    LOGGER.exception("Uploaded file processing failed: %s", name)
                    file_warnings.append({"file_name": name, "message": str(exc)})

        yield _event("status", message="Finding document chunks related to your question…")
        try:
            from backend.database.supabase_repository import get_or_create_default_business, get_supabase_client

            client = get_supabase_client()
            active_business_id = requested_business_id or get_or_create_default_business(client)
            results = retrieve(
                clean_question,
                active_business_id,
                match_count=5,
                min_similarity=0.50,
                client=client,
            )
            yield _event(
                "results",
                chunks=results,
                files=saved_files,
                indexed_files=indexed_files,
                warnings=file_warnings,
                llm_called=False,
            )
            yield _event(
                "status",
                message=(
                    f"Found {len(results)} matching chunk(s). Showing the retrieved text; no AI answer was generated."
                    if results
                    else "No matching chunks found. Showing any files that were saved. No AI answer was generated."
                ),
                done=True,
            )
        except Exception as exc:
            LOGGER.exception("Document retrieval failed")
            yield _event("error", message=str(exc), files=indexed_files, warnings=file_warnings)

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
