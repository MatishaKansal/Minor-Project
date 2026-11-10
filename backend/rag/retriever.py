"""Retrieve semantically similar document chunks from Supabase."""

import re
from datetime import datetime
from typing import Any

from backend.rag.embeddings import embed_texts

_EXACT_TOKEN_PATTERNS = (
    # GSTIN (15 characters)
    r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b",
    # PAN (10 characters)
    r"\b[A-Z]{5}\d{4}[A-Z]\b",
    # Delimited invoice/document identifiers (e.g., INV-08-0007, POS-2026-1002, TAX/2021/01)
    r"\b[A-Za-z0-9]{2,}(?:[-/_#][A-Za-z0-9]+)+\b",
    # Mixed alphanumeric codes (e.g., UP14GT2103, LKO010005025, RB236566, INV1001)
    r"\b(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{4,25}\b",
    # Standalone numeric identifiers (6 to 20 digits, e.g., invoice 4203304680, e-way bill 471216864245)
    r"\b\d{6,20}\b",
    # Formatted monetary amounts with commas (e.g., 231,400.50, 324,284.46)
    r"(?<!\w)\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?(?!\w)",
    # Decimal monetary amounts (e.g., 42.50, 100.00)
    r"(?<!\w)\d+\.\d{1,2}(?!\w)",
)


def _exact_tokens(query: str) -> list[str]:
    tokens: set[str] = set()

    for pattern in _EXACT_TOKEN_PATTERNS:
        for match in re.finditer(pattern, query):
            val = match.group(0)
            tokens.add(val)
            if "," in val:
                tokens.add(val.replace(",", ""))

    # Date pattern: DD-MM-YYYY, DD/MM/YYYY, DD.MM.YYYY
    for match in re.finditer(r"\b(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4})\b", query):
        raw = match.group(0)
        tokens.add(raw)
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        for dt_args in [(year, month, day), (year, day, month)]:
            try:
                dt = datetime(*dt_args)
                tokens.add(dt.strftime("%Y-%m-%d"))
                tokens.add(dt.strftime("%d-%m-%Y"))
                tokens.add(dt.strftime("%d/%m/%Y"))
                tokens.add(dt.strftime("%d.%m.%Y"))
                tokens.add(f"{dt.day}-{dt.month}-{dt.year}")
                tokens.add(f"{dt.day}/{dt.month}/{dt.year}")
                tokens.add(f"{dt.day}.{dt.month}.{dt.year}")
                tokens.add(dt.strftime("%d %b %Y"))
                break
            except ValueError:
                continue

    # Date pattern: YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD
    for match in re.finditer(r"\b(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})\b", query):
        raw = match.group(0)
        tokens.add(raw)
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            dt = datetime(year, month, day)
            tokens.add(dt.strftime("%Y-%m-%d"))
            tokens.add(dt.strftime("%d-%m-%Y"))
            tokens.add(dt.strftime("%d/%m/%Y"))
            tokens.add(dt.strftime("%d.%m.%Y"))
            tokens.add(f"{dt.day}-{dt.month}-{dt.year}")
            tokens.add(f"{dt.day}/{dt.month}/{dt.year}")
            tokens.add(f"{dt.day}.{dt.month}.{dt.year}")
            tokens.add(dt.strftime("%d %b %Y"))
        except ValueError:
            continue

    return sorted(tokens, key=lambda token: (-len(token), token))


def _result_key(result: dict[str, Any]) -> tuple[Any, ...]:
    if result.get("chunk_id") is not None:
        return ("chunk_id", result["chunk_id"])
    if result.get("id") is not None:
        return ("id", result["id"])
    return (
        "chunk",
        result.get("source_file_id"),
        result.get("chunk_index"),
        result.get("content"),
    )


def _exact_matches(
    supabase: Any,
    query: str,
    business_id: str,
    match_count: int,
) -> list[dict[str, Any]]:
    """Find identifier/value matches that embeddings can rank poorly."""
    matches: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for token in _exact_tokens(query):
        response = (
            supabase.table("document_chunks")
            .select("*")
            .eq("business_id", business_id)
            .ilike("content", f"%{token}%")
            .limit(match_count)
            .execute()
        )
        for result in response.data or []:
            key = _result_key(result)
            if key in seen:
                continue
            seen.add(key)
            result = dict(result)
            result.setdefault("similarity", 1.0)
            result["match_type"] = "exact"
            matches.append(result)
            if len(matches) >= match_count:
                return matches
    return matches


def retrieve(
    query: str,
    business_id: str,
    *,
    match_count: int = 5,
    min_similarity: float = 0.35,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """Return matching chunks for one business, without generating an LLM answer."""
    if not query.strip():
        raise ValueError("Query cannot be empty")
    if not business_id.strip():
        raise ValueError("business_id cannot be empty")

    from backend.database.supabase_repository import get_supabase_client

    supabase = client or get_supabase_client()
    exact_results = _exact_matches(supabase, query, business_id, match_count)
    has_exact_date = bool(
        re.search(r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b", query)
        or re.search(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{4}\b", query)
    )
    if has_exact_date and exact_results:
        return exact_results

    if len(exact_results) >= match_count:
        return exact_results[:match_count]

    query_embedding = embed_texts([query])[0]
    response = supabase.rpc(
        "match_document_chunks",
        {
            "query_embedding": query_embedding,
            "match_business_id": business_id,
            "match_count": match_count,
            "min_similarity": min_similarity,
        },
    ).execute()
    vector_results = response.data or []
    combined = exact_results[:]
    seen = {_result_key(result) for result in combined}
    for result in vector_results:
        if len(combined) >= match_count:
            break
        if _result_key(result) not in seen:
            combined.append(result)
            seen.add(_result_key(result))

    if not combined:
        # Embeddings rank bare-year questions poorly; fall back to the literal year.
        for year in re.findall(r"\b(?:19|20)\d{2}\b", query):
            response = (
                supabase.table("document_chunks")
                .select("*")
                .eq("business_id", business_id)
                .ilike("content", f"%{year}%")
                .limit(match_count)
                .execute()
            )
            combined = [{**row, "similarity": 1.0, "match_type": "exact"} for row in response.data or []]
            if combined:
                break
    return combined
