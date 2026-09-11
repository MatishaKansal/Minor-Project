"""Shared deterministic financial field extraction across images and PDFs."""

import re
from typing import Any, NamedTuple


class FieldSource(NamedTuple):
    page_number: int
    line_number: int
    source_text: str


DocumentLine = tuple[int, int, str, str]  # (page_number, line_number, raw_line, clean_line)


def to_document_lines(input_data: "str | list[tuple[int, str]] | list[DocumentLine]") -> list[DocumentLine]:
    """Convert raw text string or list of (page_number, page_text) into DocumentLine tuples.

    Also accepts an already-converted list[DocumentLine] (4-tuples) and returns it unchanged.
    """
    if isinstance(input_data, str):
        pages: list[tuple[int, str]] = [(1, input_data)]
    elif input_data and isinstance(input_data[0], tuple) and len(input_data[0]) == 4:
        # Already a list of DocumentLine — pass through
        return list(input_data)  # type: ignore[return-value]
    else:
        pages = input_data  # type: ignore[assignment]

    result: list[DocumentLine] = []
    for page_num, page_text in pages:
        for line_num, raw_line in enumerate(page_text.splitlines(), start=1):
            if raw_line.strip():
                result.append((page_num, line_num, raw_line, raw_line.strip()))
    return result


def _first_match(patterns: list[str], text: str, flags: int = re.IGNORECASE) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1).strip()
    return None


def _extract_monetary_candidates(line: str) -> list[str]:
    cleaned = line
    # Remove single noise letters embedded between digits (e.g. 4e8 -> 48)
    cleaned = re.sub(r"(?<=\d)[a-zA-Z](?=\d)", "", cleaned)
    # Fix trailing OCR digit confusion (e.g. .0gK -> .00, .0o -> .00)
    cleaned = re.sub(r"(\d+[.,]\d)[oOqg][a-zA-Z]*", r"\g<1>0", cleaned)
    # Strip leading currency noise prefix (e.g. N63.35 -> 63.35)
    cleaned = re.sub(r"\b[Nn](\d+[.,]\d{2})\b", r"\1", cleaned)

    # 1. Look for amounts with 2 decimal places (dot or comma, optional spaces around)
    matches = re.findall(r"(?<!\d)(\d{1,6})\s*[.,]\s*(\d{2})(?!\d)", cleaned)
    if matches:
        return [f"{m[0]}.{m[1]}" for m in matches]

    # 2. Whole numbers if no decimals present
    whole_matches = re.findall(r"(?<!\d)(\d{1,6})(?!\d)", cleaned)
    valid_whole = [
        w for w in whole_matches
        if len(w) <= 5 and w not in {"2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026"}
    ]
    return valid_whole


def extract_final_amount(
    input_data: str | list[tuple[int, str]],
) -> tuple[str | None, FieldSource | None, str | None]:
    """Extract final transaction/payable amount avoiding subtotal, tax, cash, or noise."""
    lines = to_document_lines(input_data)
    if not lines:
        return None, None, None

    total_count = len(lines)
    candidates: list[tuple[int, str, FieldSource, str]] = []

    # Detect rounding adjustment line to prioritize post-rounding payable amount
    rounding_index = None
    for idx, (_, _, _, clean) in enumerate(lines):
        low = clean.lower()
        if re.search(r"\b(?:rounding|round|rnunding|ruunuing)\b", low) and not re.search(
            r"total\s+rounded|after\s+rounding", low
        ):
            rounding_index = idx

    if rounding_index is not None:
        for offset in (1, 2):
            target_idx = rounding_index + offset
            if target_idx < total_count:
                tpno, tlno, traw, tclean = lines[target_idx]
                tlow = tclean.lower()
                if re.search(r"\b(?:cash|tendered|change|kembali|baki|visa|card|master)\b", tlow):
                    break
                amounts = _extract_monetary_candidates(tclean)
                if amounts:
                    score = 115 if re.search(r"rounded|after\s+rounding", tlow) else 95
                    candidates.append(
                        (score, amounts[-1], FieldSource(tpno, tlno, traw), "regex_final_total")
                    )

    for idx, (pno, lno, raw, clean) in enumerate(lines):
        low = clean.lower()
        # Explicit exclusions
        if re.search(r"\bsub[- ]?total\b|\btotal\s*\(?exclud", low):
            continue
        if re.search(r"\b(?:total\s+tax|tax\s+total|total\s+gst|gst\s+total|tax\s+amount)\b", low) or re.search(
            r"^\s*tax\s+total", low
        ):
            continue
        if re.search(
            r"\b(?:total\s+qty|total\s+quantity|total\s+item|item\s+count|total\s+saving|promotional\s+saving)\b", low
        ):
            continue
        if re.search(r"\b(?:cash|tendered|change|kembali|baki)\b", low):
            continue
        if re.search(r"\b(?:discount|rebate|voucher)\b", low):
            continue
        if re.search(r"\b(?:qty|price|amount|u/p)\s+(?:qty|price|amount|tax)", low):
            continue

        amounts = _extract_monetary_candidates(clean)
        if not amounts:
            continue

        # Ignore multi-column summary tables (e.g. GST summary codes with amount and tax)
        if len(amounts) >= 2 and re.search(r"\b\d+[a-zA-Z]\b", clean):
            continue

        score = 0
        method = "regex_total_label"

        # Priority 1: explicit rounded / final payable total
        if re.search(
            r"\b(?:total|tutal|totai|t0tal|lota|lotal|potal|utal|[\[\{]utal)?\s*(?:rounded|after\s+rounding)\b", low
        ) or re.search(
            r"\b(?:final\s+total|final\s+amount|amount\s+payable|total\s+payable|net\s+payable|amount\s+due|total\s+due|balance\s+due)\b",
            low,
        ):
            score = 100
            method = "regex_final_total"
        # Priority 2: grand total
        elif re.search(r"\bgrand\s+total\b", low):
            score = 80
            method = "regex_total_label"
        # Priority 3: transaction / standard total
        elif (
            re.search(r"\btotal\s+sales\s*(?:inclusive|incl\.?|including)?\s*(?:of)?\s*(?:gst|tax)?\b", low)
            or re.search(r"\btotal\s*(?:rm|myr|inr|usd|eur|bm|ru|rn|rh)?\s*(?:incl\.?|inclusive)\s*(?:of)?\s*(?:gst|tax)\b", low)
            or re.search(r"\b(?:total\s+amount|total\s+amt|total\s+ant|net\s+total)\b", low)
            or re.search(r"\b(?:total|tutal|totai|t0tal|lotal?|potal?|potai)\b", low)
            or re.search(r"^[|\[\]{}()]*(?:total|tutal|totai|t0tal|lotal?|potal?|potai|utal)\b", low)
        ):
            score = 60
            method = "regex_total_label"
        else:
            continue

        if re.search(r"\b(?:rm|myr|inr|usd|eur|bm|ru|rn|rh)\b|[$€£₹]", low):
            score += 5
        score += (idx * 5) // max(total_count, 1)

        candidates.append((score, amounts[-1], FieldSource(pno, lno, raw), method))

    if not candidates:
        return None, None, None

    best = max(candidates, key=lambda c: c[0])
    return best[1], best[2], best[3]


def _extract_currency(
    lines: list[DocumentLine],
    amount_source: FieldSource | None = None,
) -> tuple[str | None, FieldSource | None]:
    currency_pattern = r"\b(RM|MYR|INR|USD|EUR|GBP|AUD|SGD)\b|([$€£₹])"

    # 1. Prefer currency on the selected amount line
    if amount_source is not None:
        match = re.search(currency_pattern, amount_source.source_text, re.IGNORECASE)
        if match:
            return match.group(1) or match.group(2), amount_source
        if re.search(r"\b(?:rh|rn|ru|bm)\b", amount_source.source_text, re.IGNORECASE):
            return "RM", amount_source

    # 2. Search lines mentioning total
    for pno, lno, raw_line, line in lines:
        if not re.search(r"total|potal|tutal|lota", line, re.IGNORECASE):
            continue
        match = re.search(currency_pattern, line, re.IGNORECASE)
        if match:
            return match.group(1) or match.group(2), FieldSource(pno, lno, raw_line)
        if re.search(r"\b(?:rh|rn|ru|bm)\b", line, re.IGNORECASE):
            return "RM", FieldSource(pno, lno, raw_line)
    return None, None


def _extract_document_number(lines: list[DocumentLine]) -> tuple[str | None, FieldSource | None]:
    label = r"(?:invoice|inv|receipt|slip|bill|ref(?:erence)?)"
    pattern = rf"\b{label}\b[ \t]*(?:no\.?|number|#)?[ \t]*[:.\-]?[ \t]*(?!date\b|total\b|amount\b|cash\b|change\b|tax\b|gst\b)([A-Z0-9][A-Z0-9/\-$]*)"
    for pno, lno, raw_line, line in lines:
        cleaned = line.replace("“", " ").replace("”", " ").replace("’", " ")
        if re.search(r"tax\s+invoice|simplified\s+tax|gst\s+invoice", cleaned, re.IGNORECASE):
            continue
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip().rstrip(".,;:")
        if value.startswith("C$"):
            value = "CS" + value[2:]
        if value.lower() == "no":
            value_match = re.search(r"\bno\b[^A-Z0-9]*([A-Z0-9][A-Z0-9/\-$]*)", cleaned, re.IGNORECASE)
            if value_match:
                value = value_match.group(1)
        if re.fullmatch(r"[A-Z0-9][A-Z0-9/\-]*", value, re.IGNORECASE):
            return value, FieldSource(pno, lno, raw_line)
    return None, None


def _extract_party_name(lines: list[DocumentLine]) -> tuple[str | None, FieldSource | None]:
    # 1. Labeled vendor/merchant pattern
    all_text = "\n".join(raw for _, _, raw, _ in lines)
    labeled = _first_match(
        [r"(?im)^\s*(?:vendor|seller|merchant|supplier|company|billed\s+by|sold\s+by)\s*[:#-]\s*(.+?)\s*$"],
        all_text,
    )
    if labeled:
        for pno, lno, raw_line, line in lines:
            if labeled in line:
                return labeled, FieldSource(pno, lno, raw_line)

    # 2. Look at header lines of page 1
    page_1_lines = [item for item in lines if item[0] == 1]
    header_lines = page_1_lines[:12] if page_1_lines else lines[:12]

    blocked = re.compile(
        r"invoice|receipt|tax|gst|tel|fax|address|jalan|road|cashier|date|total|customer|^\s*\(?co\.|\bco\.?\s*no|reg\.?\s*no",
        re.I,
    )
    company_markers = re.compile(
        r"sdn\s+bhd|ltd|limited|pvt\s+ltd|inc\.?|llc|\bco\.?\b|perniagaan|stationery|books|gift|home\s+deco|traders",
        re.I,
    )
    candidates: list[tuple[int, str, FieldSource]] = []
    for pno, lno, raw_line, line in header_lines:
        clean = line.strip(" |.-")
        if not clean or blocked.search(clean) or not re.search(r"[A-Za-z]", clean):
            continue
        words = re.findall(r"[A-Za-z]+", clean)
        if len(words) < 2 or len(clean) > 80:
            continue
        score = 0
        if company_markers.search(clean):
            score += 8
        if clean.upper() == clean and len(words) >= 2:
            score += 2
        if re.search(r"\d", clean):
            score -= 2
        candidates.append((score, clean, FieldSource(pno, lno, raw_line)))

    best = max(candidates, default=(0, None, None), key=lambda item: item[0])
    return (best[1], best[2]) if best[0] >= 5 else (None, None)


def _extract_description(lines: list[DocumentLine]) -> tuple[str | None, FieldSource | None]:
    for pno, lno, raw_line, line in lines:
        match = re.match(r"^\s*(?:description|details?|particulars?)\s*[:#-]\s*(.+?)\s*$", line, re.IGNORECASE)
        if match:
            return match.group(1).strip(), FieldSource(pno, lno, raw_line)
    return None, None


def extract_financial_fields(input_data: str | list[tuple[int, str]]) -> dict[str, Any]:
    """Extract financial fields from single-page text or multi-page PDF documents."""
    lines = to_document_lines(input_data)
    all_text = "\n".join(raw for _, _, raw, _ in lines)

    date = _first_match(
        [r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", r"\b(\d{4}[/-]\d{1,2}[/-]\d{1,2})\b"],
        all_text,
    )
    date_source: FieldSource | None = None
    if date:
        for pno, lno, raw_line, line in lines:
            if date in line:
                date_source = FieldSource(pno, lno, raw_line)
                break

    amount, amount_source, amount_method = extract_final_amount(lines)
    currency, currency_source = _extract_currency(lines, amount_source=amount_source)
    invoice_number, invoice_source = _extract_document_number(lines)
    party_name, party_source = _extract_party_name(lines)
    description, description_source = _extract_description(lines)

    fields = {
        "date": date,
        "amount": amount.replace(",", "") if amount else None,
        "currency": currency,
        "party_name": party_name,
        "invoice_number": invoice_number,
        "description": description,
    }

    provenance: list[dict[str, Any]] = []
    for field_name, value, source, method in (
        ("date", date, date_source, "regex_date"),
        ("amount", fields["amount"], amount_source, amount_method or "regex_total_label"),
        ("currency", currency, currency_source, "currency_label"),
        ("party_name", party_name, party_source, "merchant_header_pattern"),
        ("invoice_number", invoice_number, invoice_source, "receipt_or_invoice_label"),
        ("description", description, description_source, "description_label"),
    ):
        if value is not None and source is not None:
            provenance.append(
                {
                    "field_name": field_name,
                    "value": value,
                    "source_text": source.source_text,
                    "line_number": source.line_number,
                    "page_number": source.page_number,
                    "extraction_method": method,
                    "confidence": None,
                }
            )

    fields["provenance"] = provenance
    return fields
