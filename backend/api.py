"""FastAPI endpoints used by the document retrieval and LLM cascade decision maker frontend."""

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.cascade.service import CascadeDecisionMaker, evidence_to_cascade_dict
from backend.parsers.bank_csv_parser import SUPPORTED_EXTENSIONS as BANK_CSV_EXTENSIONS
from backend.rag.retriever import retrieve
from backend.schemas.evidence import Evidence
from backend.services.ingestion_service import ALL_SUPPORTED_EXTENSIONS, ingest_file

LOGGER = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
PERSISTABLE_EXTENSIONS = ALL_SUPPORTED_EXTENSIONS - BANK_CSV_EXTENSIONS

app = FastAPI(title="Financial Document Retrieval & Cascade Decision API", version="0.2.0")
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

# Global Cascade Decision Maker instance
DECISION_MAKER = CascadeDecisionMaker()


def _event(event_type: str, **payload: Any) -> str:
    data = json.dumps({"type": event_type, **payload}, ensure_ascii=False)
    return f"data: {data}\n\n"


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "cascade_router_thresholds": DECISION_MAKER.router.thresholds,
        "ollama_connected": DECISION_MAKER.client is not None,
    }


@app.post("/api/query")
async def query_documents(
    question: str = Form(...),
    files: list[UploadFile] | None = File(default=None),
    business_id: str | None = Form(default=None),
) -> StreamingResponse:
    """Persist uploaded files, retrieve relevant chunks, execute the 4-tier LLM cascade decision maker, and stream progress/results."""
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
        parsed_evidence_list: list[Evidence] = []
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
                        # If database persistence failed (e.g. Supabase unconfigured), fallback to in-memory extraction
                        if evidence.processing_status != "success" and "supabase" in (evidence.error or "").lower():
                            LOGGER.info("Retrying file %s with persist_to_database=False", name)
                            evidence = ingest_file(
                                temporary_path,
                                persist_to_database=False,
                                business_id=requested_business_id,
                            )

                    if evidence.processing_status != "success":
                        file_warnings.append({
                            "file_name": name,
                            "message": evidence.error or "The file could not be parsed and saved.",
                        })
                        continue

                    parsed_evidence_list.append(evidence)
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
        retrieved_chunks = []
        try:
            from backend.database.supabase_repository import get_or_create_default_business, get_supabase_client

            client = get_supabase_client()
            active_business_id = requested_business_id or get_or_create_default_business(client)
            retrieved_chunks = retrieve(
                clean_question,
                active_business_id,
                match_count=5,
                min_similarity=0.35,
                client=client,
            )
        except Exception as exc:
            LOGGER.warning("Document retrieval bypassed or failed: %s", exc)

        yield _event("status", message="Evaluating evidence through the 4-tier LLM cascade decision maker…")
        try:
            # Transform extracted multimodal evidence and retrieved RAG context into cascade input
            cascade_evidence = evidence_to_cascade_dict(
                evidence_list=parsed_evidence_list,
                retrieved_chunks=retrieved_chunks,
            )

            # Execute the cascade with learned stop routing
            decision = DECISION_MAKER.execute_decision(
                query=clean_question,
                evidence=cascade_evidence,
            )

            ai_answer = decision.get("answer", "")
            stopped_tier = decision.get("stopped_tier", "layer_4")
            stopped_model = decision.get("stopped_model", "")
            stop_prob = decision.get("stop_probability")
            tier_history = decision.get("tier_history", [])

            yield _event(
                "results",
                chunks=retrieved_chunks,
                files=saved_files,
                indexed_files=indexed_files,
                warnings=file_warnings,
                llm_called=True,
                answer=ai_answer,
                stopped_tier=stopped_tier,
                stopped_model=stopped_model,
                stop_probability=stop_prob,
                tier_history=tier_history,
                is_mock=decision.get("is_mock", False),
            )
            yield _event(
                "status",
                message=f"Cascade decision reached at {stopped_tier} ({stopped_model}). Audit answer generated.",
                done=True,
            )
        except Exception as exc:
            LOGGER.exception("LLM Cascade decision maker failed: %s", exc)
            yield _event(
                "results",
                chunks=retrieved_chunks,
                files=saved_files,
                indexed_files=indexed_files,
                warnings=file_warnings,
                llm_called=False,
            )
            yield _event(
                "status",
                message=f"Retrieved {len(retrieved_chunks)} chunk(s). Cascade evaluation encountered an issue: {exc}",
                done=True,
            )

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
