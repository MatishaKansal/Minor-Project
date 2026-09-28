"""Shared deterministic financial field extraction across images and PDFs."""

import re
from typing import Any, NamedTuple


class FieldSource(NamedTuple):
    page_number: int
    line_number: int
    source_text: str


DocumentLine = tuple[int, int, str, str]  # (page_number, line_number, raw_line, clean_line)

# ─── Text normalisation helpers ───────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Strip BOM, null bytes, and normalise whitespace/punctuation noise."""
    text = text.lstrip("\ufeff")                     # BOM
    text = text.replace("\x00", "")                  # embedded nulls (PDF artefact)
    text = text.replace("\xa0", " ")                  # non-breaking space → space
    text = text.replace("\u2013", "-").replace("\u2014", "-")  # en-dash, em-dash → hyphen
    text = text.replace("\u2018", "'").replace("\u2019", "'")  # curly single quotes
    text = text.replace("\u201c", '"').replace("\u201d", '"')  # curly double quotes
    text = re.sub(r"[ \t]{2,}", " ", text)            # collapse multiple spaces/tabs
    return text


# ─── Document line conversion ─────────────────────────────────────────────────

def to_document_lines(input_data: "str | list[tuple[int, str]] | list[DocumentLine]") -> list[DocumentLine]:
    """Convert raw text string or list of (page_number, page_text) into DocumentLine tuples.

    Also accepts an already-converted list[DocumentLine] (4-tuples) and returns it unchanged.
    """
    if isinstance(input_data, str):
        pages: list[tuple[int, str]] = [(1, input_data)]
    elif input_data and isinstance(input_data[0], tuple) and len(input_data[0]) == 4:
        return list(input_data)  # type: ignore[return-value]
    else:
        pages = input_data  # type: ignore[assignment]

    result: list[DocumentLine] = []
    for page_num, page_text in pages:
        page_text = _normalize_text(page_text)
        for line_num, raw_line in enumerate(page_text.splitlines(), start=1):
            if raw_line.strip():
                clean_line = raw_line.strip()
                # Strip SROIE-style bounding box coordinates
                sroie_match = re.match(r"^(?:\d+\s*,\s*){8}(.*)$", clean_line)
                if sroie_match:
                    clean_line = sroie_match.group(1).strip()
                result.append((page_num, line_num, raw_line, clean_line))
    return result


def _first_match(patterns: list[str], text: str, flags: int = re.IGNORECASE) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1).strip()
    return None


# ─── Date extraction ──────────────────────────────────────────────────────────

_MONTH_ABBR = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_ORDINAL = r"(?:st|nd|rd|th)?"
_SEP = r"[\s\-/\.]"  # flexible separator between date parts

# All date patterns, ordered from most-specific to least-specific
_DATE_PATTERNS = [
    # "25 March 2024" / "25th March 2024" / "25-Mar-2024" / "25.Mar.2024"
    rf"\b(\d{{1,2}}{_ORDINAL}{_SEP}{_MONTH_ABBR}{_SEP}\d{{4}})\b",
    # "March 25, 2024" / "March 25 2024" / "Mar-25-2024"
    rf"\b({_MONTH_ABBR}{_SEP}\d{{1,2}}{_ORDINAL}[,\s]*\d{{4}})\b",
    # ISO 8601: "2024-09-25" / "2024.09.25"
    r"\b(\d{4}[\-./]\d{1,2}[\-./]\d{1,2})\b",
    # "25/09/2024" / "25-09-2024" / "25.09.2024"
    r"\b(\d{1,2}[\-./]\d{1,2}[\-./]\d{2,4})\b",
    # Month + Day without year (e.g. "October 19th", "October 19", "19th October")
    rf"\b({_MONTH_ABBR}{_SEP}\d{{1,2}}{_ORDINAL})\b",
    rf"\b(\d{{1,2}}{_ORDINAL}{_SEP}{_MONTH_ABBR})\b",
]

# OCR date character correction: O→0, l→1, S→5, I→1
def _fix_ocr_date(text: str) -> str:
    """Fix common OCR misreads inside date-like strings."""
    # Separate date and merged time (e.g. "12-01-1921:13" → "12-01-19 21:13")
    text = re.sub(r"(\d{1,2}[-./]\d{1,2}[-./]\d{2})(\d{2}:\d{2})", r"\1 \2", text)
    # Replace O/o with 0 inside numeric date positions only (e.g. "25/O9/2O24")
    text = re.sub(r"(?<=[/\-\.])[Oo](?=\d)", "0", text)
    text = re.sub(r"(?<=\d)[Oo](?=[/\-\.])", "0", text)
    text = re.sub(r"(?<=[/\-\.])l(?=\d)", "1", text)
    text = re.sub(r"(?<=\d)l(?=[/\-\.])", "1", text)
    return text


# Label prefixes that indicate an *invoice* date (preferred over due date, payment date)
_ISSUE_DATE_LABELS = re.compile(
    r"(?:invoice|issue[d]?|bill|transaction|purchase|sale|order|created|document|original)\s+date",
    re.IGNORECASE,
)
_ANY_DATE_LABEL = re.compile(
    r"(?:date|dated|dt\.?)\s*[:\-]?\s*$|(?:date|dated)\s+of\s+",
    re.IGNORECASE,
)


def _extract_date(lines: list[DocumentLine]) -> tuple[str | None, FieldSource | None]:
    """Extract the most relevant date, preferring invoice/issue date over due/payment date."""
    all_text = _normalize_text("\n".join(raw for _, _, raw, _ in lines))
    ocr_fixed = _fix_ocr_date(all_text)

    # Phase 1: look for a labelled issue/invoice date
    for pno, lno, raw_line, clean in lines:
        if not _ISSUE_DATE_LABELS.search(clean):
            continue
        # Try same line and next line
        search_text = clean
        for off in (0, 1, 2):
            ni = (lines.index((pno, lno, raw_line, clean)) if off == 0 else
                  (lines.index((pno, lno, raw_line, clean)) + off))
            if ni >= len(lines):
                break
            target_text = _fix_ocr_date(lines[ni][3])
            for pat in _DATE_PATTERNS:
                m = re.search(pat, target_text, re.IGNORECASE)
                if m:
                    return m.group(1).strip(), FieldSource(lines[ni][0], lines[ni][1], lines[ni][2])

    # Phase 2: labelled "Date:" on its own, value same or next line
    for idx, (pno, lno, raw_line, clean) in enumerate(lines):
        if not _ANY_DATE_LABEL.search(clean):
            continue
        for off in (0, 1):
            ni = idx + off
            if ni >= len(lines):
                break
            target_text = _fix_ocr_date(lines[ni][3])
            for pat in _DATE_PATTERNS:
                m = re.search(pat, target_text, re.IGNORECASE)
                if m:
                    val = m.group(1).strip()
                    return val, FieldSource(lines[ni][0], lines[ni][1], lines[ni][2])

    # Phase 3: first date anywhere in document (OCR-corrected)
    for pat in _DATE_PATTERNS:
        m = re.search(pat, ocr_fixed, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            # Find which line it came from
            for pno, lno, raw_line, clean in lines:
                if val in _fix_ocr_date(clean) or val in clean:
                    return val, FieldSource(pno, lno, raw_line)
            return val, None

    return None, None


# ─── Monetary candidate extraction ───────────────────────────────────────────

def _normalize_number(value: str) -> str:
    """Normalise a raw number string to plain decimal (e.g. '1.234,56' → '1234.56').

    Also ensures whole numbers are always expressed as X.00 so every amount
    returned has a consistent decimal representation.
    """
    stripped = value.strip()
    # European format: 1.234,56 (dot as thousand-sep, comma as decimal)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d{2}", stripped):
        return stripped.replace(".", "").replace(",", ".")
    # Space as thousands separator: "1 234.56" or "1 234,56"
    if re.fullmatch(r"\d{1,3}(?:\s\d{3})+[.,]\d{2}", stripped):
        val = stripped.replace(" ", "")
        return val.replace(",", ".")
    # Standard US comma as thousands: "1,234.56"
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d{2}", stripped):
        return stripped.replace(",", "")
    # Simple comma as decimal: "48,00"
    if re.fullmatch(r"\d+,\d{2}", stripped):
        return stripped.replace(",", ".")
    result = stripped.replace(",", "")
    # Ensure whole numbers always have two decimal places
    if re.fullmatch(r"\d+", result):
        result = f"{result}.00"
    return result


def _extract_monetary_candidates(line: str) -> list[str]:
    cleaned = line
    # OCR noise: lowercase-L → 1 only when directly BEFORE a digit (not mid-word)
    # e.g. "l9.00" → "19.00" but "lota" stays as-is (label words)
    cleaned = re.sub(r"(?<![A-Za-z])l(?=\d)", "1", cleaned)
    # S→5 only when immediately before a digit+decimal pattern
    cleaned = re.sub(r"\bS(\d{1,6}[.,]\d{2})\b", r"5\1", cleaned)
    # Strip percentages
    cleaned = re.sub(r"\b\d+(?:[.,]\d+)?\s*%", "", cleaned)
    # Remove single noise letters embedded between digits (e.g. 4e8 → 48)
    cleaned = re.sub(r"(?<=\d)[a-zA-Z](?=\d)", "", cleaned)
    # Fix trailing OCR digit confusion (.0g → .00)
    cleaned = re.sub(r"(\d+[.,]\d)[oOqg][a-zA-Z]*", r"\g<1>0", cleaned)
    # Fix space inside decimal: "100. 60" → "100.60", "100 .60" → "100.60"
    cleaned = re.sub(r"(\d)\s*[.,]\s*(\d{2})(?!\d)", r"\1.\2", cleaned)
    # Strip leading currency noise prefix (N63.35 → 63.35)
    cleaned = re.sub(r"\b[Nn](\d+[.,]\d{2})\b", r"\1", cleaned)
    # Strip parenthesised negatives — treat (X) as -X for matching
    cleaned = re.sub(r"\((\d+[.,]\d{2})\)", r"-\1", cleaned)

    # 0. Spoken dollars and cents: "$60 and 30 cents" or "60 dollars and 30 cents"
    spoken_cents = re.findall(
        r"(?:[$\u20ac\xa3\u20b9\xa5\u20a9]|USD|EUR|GBP|RM|MYR|INR)?\s*(\d{1,6})\s*(?:dollars?|bucks?|\$)?\s*(?:and)?\s*(\d{1,2})\s*cents?",
        cleaned,
        re.IGNORECASE,
    )
    if spoken_cents:
        return [f"{d}.{int(c):02d}" for d, c in spoken_cents]

    # 1. Currency-tagged amounts (highest priority)
    _CURR_SYM_PREFIX = r"(?:[$\u20ac\xa3\u20b9\xa5\u20a9]|R[Ss]\.?|RM|MYR|INR|USD|EUR|GBP|AUD|SGD|CAD|CHF|JPY|KRW|SAR|AED|ZAR|NZD)"
    _CURR_SYM_SUFFIX = r"(?:dollars?|euros?|pounds?|rupees?|ringgit|bucks?|USD|EUR|GBP|RM|INR)"
    _NUM_PATTERN = r"(\d{1,3}(?:[,\s]\d{3})+(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?)"

    curr_tagged = re.findall(
        rf"{_CURR_SYM_PREFIX}\s*{_NUM_PATTERN}|{_NUM_PATTERN}\s*{_CURR_SYM_SUFFIX}",
        cleaned,
        re.IGNORECASE,
    )
    if curr_tagged:
        results = []
        for m in curr_tagged:
            val = (m[0] or m[1]).strip()
            if val:
                results.append(_normalize_number(val))
        if results:
            return results

    # Strip dates and reference codes before plain number search
    cleaned_no_dates = re.sub(r"\b\d{1,4}[/\-\.]\d{1,2}[/\-\.]\d{2,4}\b", "", cleaned)
    cleaned_no_dates = re.sub(r"\b[A-Za-z][\w]*[\-/]\d+\b", "", cleaned_no_dates)

    # 2. Amounts with exactly 2 decimal places
    matches = re.findall(r"(?<!\d)(\d{1,3}(?:[\s,]\d{3})*[.,]\d{2})(?!\d)", cleaned_no_dates)
    if matches:
        return [_normalize_number(m) for m in matches]

    # 3. Whole numbers (no decimal)
    whole_matches = re.findall(r"(?<!\d)(\d{1,6})(?!\d)", cleaned_no_dates)
    skip_years = {"2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026", "2027"}
    return [w for w in whole_matches if len(w) <= 5 and w not in skip_years]


# ─── Lines that should NEVER contribute to amount ────────────────────────────

_AMOUNT_EXCLUSION = re.compile(
    r"\b(?:"
    r"sub[- ]?total|total\s*\(?exclud"
    r"|total\s+tax|tax\s+total|total\s+gst|gst\s+total|tax\s+amount"
    r"|total\s+qty|total\s+quantity|total\s+items?|item\s+count"
    r"|total\s+saving|promotional\s+saving|saving"
    r"|(?<!\bin\s)(?<!\bby\s)(?<!\bvia\s)(?<!\bwith\s)cash|tendered|change|kembali|baki"
    r"|discount|rebate|voucher|coupon"
    r"|qty\s+price|price\s+amount|amount\s+tax|u/p\s+amount"
    r"|balance\s+forward|opening\s+balance|previous\s+balance|brought\s+forward"
    r"|minimum\s+payment|credit\s+limit|available\s+credit|credit\s+available"
    r"|reward|point|loyalty|bonus"
    r")\b",
    re.IGNORECASE,
)

_AMOUNT_TAX_TOTAL = re.compile(
    r"^\s*tax\s+total|\b(?:total\s+tax|tax\s+total|total\s+gst|gst\s+total)\b",
    re.IGNORECASE,
)


def extract_final_amount(
    input_data: str | list[tuple[int, str]],
) -> tuple[str | None, FieldSource | None, str | None]:
    """Extract final transaction/payable amount avoiding subtotal, tax, cash, or noise."""
    lines = to_document_lines(input_data)
    if not lines:
        return None, None, None

    total_count = len(lines)
    candidates: list[tuple[int, str, FieldSource, str]] = []

    # Detect rounding adjustment line
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
                    candidates.append((score, amounts[-1], FieldSource(tpno, tlno, traw), "regex_final_total"))

    for idx, (pno, lno, raw, clean) in enumerate(lines):
        low = clean.lower()

        is_explicit_total = bool(re.search(r"\b(?:the\s+total\s+was|total\s+was|total\s*[:=]|total\s+amount|grand\s+total)\b", low))
        if _AMOUNT_EXCLUSION.search(low) and not is_explicit_total:
            continue
        if _AMOUNT_TAX_TOTAL.search(low):
            continue
        if re.search(r"\b(?:qty|price|amount|u/p)\s+(?:qty|price|amount|tax)", low):
            continue

        score = 0
        method = "regex_total_label"

        # Priority 1: explicit final payable / amount due
        if re.search(
            r"\b(?:total|tutal|totai|t0tal|lota|lotal|potal|utal|[\[\{]utal)?\s*(?:rounded|roundeo|rounde0|rounde|after\s+rounding)\b", low
        ) or re.search(
            r"\b(?:final\s+total|final\s+amount|amount\s+payable|total\s+payable|net\s+payable"
            r"|amount\s+due|total\s+due|balance\s+due|please\s+pay|pay\s+this\s+amount"
            r"|payable\s+amount|you\s+owe|outstanding\s+balance)\b",
            low,
        ):
            score = 100
            method = "regex_final_total"
        # Priority 2: grand total
        elif re.search(r"\bgrand\s+total\b", low):
            score = 80
            method = "regex_total_label"
        # Priority 3: standard total forms
        elif (
            re.search(r"\btotal\s+sales\s*(?:inclusive|incl\.?|including)?\s*(?:of)?\s*(?:gst|tax)?\b", low)
            or re.search(r"\btotal\s*(?:rm|myr|inr|usd|eur|bm|ru|rn|rh)?\s*(?:incl\.?|inclusive)\s*(?:of)?\s*(?:gst|tax)\b", low)
            or re.search(r"\b(?:total\s+amount|total\s+amt|total\s+ant|net\s+total|nett\s+total|net\s+amount)\b", low)
            or re.search(r"\b(?:total|tutal|totai|t0tal|lotal?|potal?|potai)\b", low)
            or re.search(r"^[|\[\]{}()]*(?:total|tutal|totai|t0tal|lotal?|potal?|potai|utal)\b", low)
        ):
            score = 60
            method = "regex_total_label"
        # Priority 4: spoken / conversational financial action
        elif re.search(r"\b(?:paid|spent|transferred|transfer|sent|received|cost|charged|payment(?:\s+of)?)\b", low):
            score = 50
            method = "spoken_action_amount"
        elif total_count == 1:
            score = 30
            method = "single_amount_fallback"
        else:
            continue

        amounts = _extract_monetary_candidates(clean)
        target_raw = raw
        target_lno = lno
        target_pno = pno

        if not amounts:
            # Label on its own line — look at next 1-2 lines, skipping truly blank ones
            lookahead_found = False
            for offset in (1, 2):
                next_idx = idx + offset
                if next_idx >= total_count:
                    break
                npno, nlno, nraw, nclean = lines[next_idx]
                nlow = nclean.lower()
                if re.search(r"\b(?:cash|tendered|change|kembali|baki|visa|card|master)\b", nlow):
                    break
                if _AMOUNT_EXCLUSION.search(nlow):
                    continue
                namounts = _extract_monetary_candidates(nclean)
                if namounts:
                    amounts = namounts
                    target_raw = f"{raw} {nraw}"
                    target_lno = nlno
                    target_pno = npno
                    lookahead_found = True
                    break

        if not amounts:
            continue

        # Skip multi-column GST summary rows
        if len(amounts) >= 2 and re.search(r"\b\d+[a-zA-Z]\b", clean):
            continue

        # Reject 0.00 — meaningless as a final payable amount
        try:
            if float(amounts[-1]) == 0.0:
                continue
        except (ValueError, TypeError):
            pass

        if re.search(r"\b(?:rm|myr|inr|usd|eur|bm|ru|rn|rh|sar|aed|cad|chf|jpy|aud|sgd|nzd|zar)\b|[$\u20ac\xa3\u20b9\xa5\u20a9]", low):
            score += 5
        score += (idx * 5) // max(total_count, 1)

        candidates.append((score, amounts[-1], FieldSource(target_pno, target_lno, target_raw), method))

    if not candidates:
        return None, None, None

    best = max(candidates, key=lambda c: c[0])
    return best[1], best[2], best[3]


# ─── Currency extraction ──────────────────────────────────────────────────────

_CURRENCY_PATTERN = re.compile(
    r"\b(RM|MYR|INR|USD|EUR|GBP|AUD|SGD|CAD|CHF|JPY|KRW|SAR|AED|ZAR|NZD"
    r"|dollars?|euros?|pounds?|rupees?|ringgit|yen|won|franc)"
    r"\b|([$\u20ac\xa3\u20b9\xa5\u20a9]|R[Ss]\.?)",
    re.IGNORECASE,
)


def _normalize_currency(val: str) -> str:
    low = val.lower().rstrip(".")
    if "dollar" in low or val == "$":
        return "USD"
    if "euro" in low or val == "\u20ac":
        return "EUR"
    if "pound" in low or val == "\xa3":
        return "GBP"
    if "rupee" in low or val in ("\u20b9",) or re.match(r"^r[s]", low):
        return "INR"
    if "ringgit" in low:
        return "RM"
    if "yen" in low or val == "\xa5":
        return "JPY"
    if "won" in low or val == "\u20a9":
        return "KRW"
    if "franc" in low:
        return "CHF"
    return val.upper().rstrip(".")


def _extract_currency(
    lines: list[DocumentLine],
    amount_source: FieldSource | None = None,
) -> tuple[str | None, FieldSource | None]:
    # 1. Prefer currency on the amount line itself
    if amount_source is not None:
        # Normalise OCR spacing: "R M" → "RM", "US D" → "USD"
        src = re.sub(r"\b(R)\s+(M)\b", r"\1\2", amount_source.source_text, flags=re.IGNORECASE)
        src = re.sub(r"\b(U)\s*(S)\s*(D)\b", "USD", src, flags=re.IGNORECASE)
        match = _CURRENCY_PATTERN.search(src)
        if match:
            return _normalize_currency(match.group(1) or match.group(2)), amount_source
        if re.search(r"\b(?:rh|rn|ru|bm)\b", src, re.IGNORECASE):
            return "RM", amount_source

    # 2. Lines near financial keywords
    for pno, lno, raw_line, line in lines:
        if not re.search(r"total|potal|tutal|lota|paid|spent|received|transferred|amount|cost|due", line, re.IGNORECASE):
            continue
        src = re.sub(r"\b(R)\s+(M)\b", r"\1\2", line, flags=re.IGNORECASE)
        src = re.sub(r"\b(U)\s*(S)\s*(D)\b", "USD", src, flags=re.IGNORECASE)
        match = _CURRENCY_PATTERN.search(src)
        if match:
            return _normalize_currency(match.group(1) or match.group(2)), FieldSource(pno, lno, raw_line)
        if re.search(r"\b(?:rh|rn|ru|bm)\b", line, re.IGNORECASE):
            return "RM", FieldSource(pno, lno, raw_line)

    # 3. Any line with a currency marker
    for pno, lno, raw_line, line in lines:
        src = re.sub(r"\b(R)\s+(M)\b", r"\1\2", line, flags=re.IGNORECASE)
        match = _CURRENCY_PATTERN.search(src)
        if match:
            return _normalize_currency(match.group(1) or match.group(2)), FieldSource(pno, lno, raw_line)

    return None, None


# ─── Document number (invoice/receipt/ref) ───────────────────────────────────

_DOC_LABEL_WORDS = frozenset({
    "number", "no", "num", "date", "total", "amount", "cash", "tax",
    "gst", "invoice", "receipt", "slip", "bill", "ref", "reference", "inv",
    "due", "issue", "issued", "order", "po", "purchase",
})

_DOC_LABEL_PATTERN = (
    r"(?:tax\s+invoice|simplified\s+tax\s+invoice"
    r"|invoice|inv|receipt|slip|bill"
    r"|purchase\s+order|p\.?o\.?"
    r"|order|ref(?:erence)?)"
)
_DOC_VALUE_PATTERN = re.compile(
    rf"\b{_DOC_LABEL_PATTERN}\b[ \t]*(?:no\.?|number|#)?[ \t]*[:\.\-]?[ \t]*"
    rf"(?!date\b|total\b|amount\b|cash\b|change\b|tax\b|gst\b)"
    rf"([A-Z0-9$][A-Z0-9/$\-\s]{{0,30}})",
    re.IGNORECASE,
)
_DOC_LABEL_ONLY = re.compile(
    rf"^\s*{_DOC_LABEL_PATTERN}\b\s*(?:no\.?|number|#)?\s*[:\.]?\s*$",
    re.IGNORECASE,
)


def _is_valid_ref(value: str) -> bool:
    """Return True if value is a plausible document reference.

    Rules:
      - Must match [A-Z0-9][A-Z0-9/- ] pattern
      - Must not be a generic label word
      - Must contain at least one digit (pure-letter phrases are NOT valid refs)
    """
    v = value.strip()
    v_compact = re.sub(r"[\s/\-]", "", v)
    v_lower = v.lower().strip()
    if not re.fullmatch(r"[A-Z0-9$][A-Z0-9/$\-\s]{0,30}", v, re.IGNORECASE):
        return False
    # Reject known label words (whole and compact forms)
    if v_lower in _DOC_LABEL_WORDS or v_lower.replace(" ", "") in _DOC_LABEL_WORDS:
        return False
    if not v_compact:
        return False
    # A valid reference must contain at least one digit
    # (Pure-letter strings like 'Invoice Number', 'Issue Date', 'Status' are not refs)
    if not re.search(r"\d", v_compact):
        return False
    return True


def _extract_document_number(lines: list[DocumentLine]) -> tuple[str | None, FieldSource | None]:
    """Extract invoice/receipt/PO/reference number, handling split-line layouts."""

    def _lookahead(from_idx: int) -> tuple[str | None, FieldSource | None]:
        for offset in (1, 2):
            ni = from_idx + offset
            if ni >= len(lines):
                break
            npno, nlno, nraw, nclean = lines[ni]
            candidate = nclean.strip()
            if _is_valid_ref(candidate):
                return candidate, FieldSource(npno, nlno, nraw)
        return None, None

    for idx, (pno, lno, raw_line, line) in enumerate(lines):
        cleaned = _normalize_text(line)

        # Skip pure taxonomy lines
        if re.fullmatch(r"(?:tax\s+invoice|simplified\s+tax|gst\s+invoice)", cleaned.strip(), re.IGNORECASE):
            continue

        # Case A: entire line is just a label → value on next line(s)
        if _DOC_LABEL_ONLY.match(cleaned):
            val, src = _lookahead(idx)
            if val:
                return val, src
            continue

        # Case B: label + value on same line
        match = _DOC_VALUE_PATTERN.search(cleaned)
        if not match:
            continue

        value = match.group(1).strip().rstrip(".,;:")
        # Trim any trailing word-only token that crept in (e.g. 'CS1802/27714 Date' → 'CS1802/27714')
        # Split on spaces; drop trailing pure-alpha tokens that are not part of a reference
        tokens = value.split()
        while len(tokens) > 1 and re.fullmatch(r"[A-Za-z]+", tokens[-1]):
            tokens.pop()
        value = " ".join(tokens)
        # C$XXXX → CSXXXX (OCR artefact: dollar sign misread as part of number)
        if value.startswith("C$"):
            value = "CS" + value[2:]
        elif re.fullmatch(r"C", value, re.IGNORECASE):
            # Only "C" captured — the rest may have been cut off by the $ in C$1234
            m2 = re.search(r"C\$([A-Z0-9][A-Z0-9/\-]*)", cleaned, re.IGNORECASE)
            if m2:
                value = "CS" + m2.group(1)

        if value.lower().strip() in _DOC_LABEL_WORDS:
            val, src = _lookahead(idx)
            if val:
                return val, src
            continue

        # "no" captured alone → take the token after it
        if re.fullmatch(r"no", value, re.IGNORECASE):
            m2 = re.search(r"\bno\b[^A-Z0-9]*([A-Z0-9][A-Z0-9/\-]*)", cleaned, re.IGNORECASE)
            if m2:
                value = m2.group(1)

        if _is_valid_ref(value):
            return value.strip(), FieldSource(pno, lno, raw_line)

    # Phase 3 fallback: receipt transaction code (e.g. T4R000027830, T4 R000027830, TRX001928, etc.)
    _RECEIPT_CODE_PATTERN = re.compile(
        r"^[|\s]*((?:T[0-9]\s*R|TRX|RCPT|INV|ORD|SLIP|BILL|DOC)\s*[A-Z0-9\-_]{5,25})[|\s]*$",
        re.IGNORECASE,
    )
    for pno, lno, raw_line, line in lines:
        cleaned = line.strip(" |.-")
        m_code = _RECEIPT_CODE_PATTERN.match(cleaned)
        if m_code and _is_valid_ref(m_code.group(1)):
            return m_code.group(1).strip(), FieldSource(pno, lno, raw_line)

    return None, None


# ─── Party name extraction ────────────────────────────────────────────────────

# Patterns that are definitively NOT a company name
_PARTY_BLOCKED_PATTERNS = re.compile(
    r"@"                             # email address
    r"|(?:www\.|https?://|ftp://)"   # URL
    r"|^\+?\d[\d\s\-\(\)]{6,}$"     # phone number
    r"|^\d{1,3}\.\d{1,3}\.\d{1,3}"  # IP address / numeric address
    r"|\bgstin\b|\bpan\b|\btan\b|\bvat\b|\bein\b|\btin\b"  # tax IDs
    r"|\bship\s+to\b|\bbill\s+to\b|\bsold\s+to\b|\bbilled\s+to\b|\bdelivered\s+to\b"
    r"|\bpay\s+to\b|\bremit\s+to\b",
    re.IGNORECASE,
)
_PARTY_SECTION_HEADERS = re.compile(
    r"^\s*(?:bill(?:ed)?\s+to|ship(?:ped)?\s+to|sold\s+to|deliver(?:ed)?\s+to"
    r"|pay(?:able)?\s+to|remit\s+to|client|customer|attn|attention)\s*[:\-]?\s*$",
    re.IGNORECASE,
)


def _extract_party_name(lines: list[DocumentLine]) -> tuple[str | None, FieldSource | None]:
    all_text = "\n".join(raw for _, _, raw, _ in lines)

    # 1. Explicitly labelled vendor/merchant line
    labeled = _first_match(
        [r"(?im)^\s*(?:vendor|seller|merchant|supplier|company|billed\s+by|sold\s+by|from)\s*[:#\-]\s*(.+?)\s*$"],
        all_text,
    )
    if labeled and not _PARTY_BLOCKED_PATTERNS.search(labeled):
        for pno, lno, raw_line, line in lines:
            if labeled in line:
                return labeled, FieldSource(pno, lno, raw_line)

    # 2. Spoken conversational pattern — "paid at Starbucks", "transferred to Shell", "from home decor"
    spoken_match = re.search(
        r"(?i)\b(?:to|at|from)\s+([A-Za-z][A-Za-z0-9\s&.\-]{1,50}?)(?:\s+the\s+total|\s+total|\s+amounting|\s+for|\s+on|\s+dated|\s+ref|\s+via|\s+and|\s+which|\s+cost|\s+priced|[,\.]|$)",
        all_text,
    )
    if spoken_match:
        candidate = spoken_match.group(1).strip()
        _spoken_blocked = {"the", "a", "an", "my", "our", "invoice", "receipt",
                           "account", "cash", "bank", "customer", "client", "you",
                           "me", "us", "them", "store", "shop"}
        if (
            len(candidate) >= 3
            and candidate.lower() not in _spoken_blocked
            and not re.match(r"^\d+$", candidate)
            and not _PARTY_BLOCKED_PATTERNS.search(candidate)
        ):
            for pno, lno, raw_line, _ in lines:
                if candidate in raw_line:
                    return candidate, FieldSource(pno, lno, raw_line)

    # 2b. Sign-off heuristic (e.g. "Thanks,\nSoftwareCorp Billing Dept")
    signoff_match = re.search(
        r"(?im)^\s*(?:thanks|thank\s+you|regards|best\s+regards|warm\s+regards|sincerely|cheers)[,\s]*\n+([A-Za-z0-9\s&.\-]{2,60})",
        all_text,
    )
    if signoff_match:
        candidate = signoff_match.group(1).strip()
        if not _PARTY_BLOCKED_PATTERNS.search(candidate) and candidate.lower() not in {"team", "all", "management"}:
            for pno, lno, raw_line, _ in lines:
                if candidate in raw_line:
                    return candidate, FieldSource(pno, lno, raw_line)

    # 3. Header-line heuristic (page 1, first 12 lines)
    page_1_lines = [item for item in lines if item[0] == 1]
    header_lines = page_1_lines[:15] if page_1_lines else lines[:15]

    _header_blocked = re.compile(
        r"invoice|receipt|tax|gst|tel|fax|phone|mobile|address|jalan|road|street|ave|"
        r"cashier|date|total|customer|^\s*\(?co\.|\\bco\.?\s*no|reg\.?\s*no|"
        r"www\.|http|@|gstin|pan\b|tin\b|vat\b|ein\b|registered",
        re.IGNORECASE,
    )
    _company_markers = re.compile(
        r"sdn\s+bhd|ltd\.?|limited|pvt\.?\s+ltd|private\s+limited|inc\.?|llc|plc|"
        r"\bco\.?\b|perniagaan|enterprise|\bsb\b|\bbhd\b|"
        r"stationery|books|gift|home\s+deco|decor|traders|trading|"
        r"corp|corporation|dept|department|services|technologies|solutions|group|"
        r"petron|shell|store|mart|shop|supermarket|pharmacy|hospital|clinic",
        re.IGNORECASE,
    )

    candidates: list[tuple[int, str, FieldSource]] = []
    for pno, lno, raw_line, line in header_lines:
        # Normalise: strip trailing comma (e.g. "Example, LLC" → keep as-is for matching)
        clean = line.strip(" |.-")
        # Hard blocklist checks
        if not clean:
            continue
        if _PARTY_SECTION_HEADERS.match(clean):
            continue
        if _PARTY_BLOCKED_PATTERNS.search(clean):
            continue
        if _header_blocked.search(clean):
            continue
        if not re.search(r"[A-Za-z]", clean):
            continue

        # Use only the alpha words for word-count check (ignore comma/punctuation)
        words = re.findall(r"[A-Za-z]+", clean)
        if len(words) < 2 or len(clean) > 80:
            continue

        score = 0
        if _company_markers.search(clean):
            score += 8
        if clean.upper() == clean and len(words) >= 2:
            score += 2
        if re.search(r"\d", clean):
            score -= 2
        # Penalise if it looks like an address (has a number then words)
        if re.match(r"^\d+\s+[A-Za-z]", clean):
            score -= 5
        candidates.append((score, clean, FieldSource(pno, lno, raw_line)))

    best = max(candidates, default=(0, None, None), key=lambda item: item[0])
    return (best[1], best[2]) if best[0] >= 5 else (None, None)


# ─── Description extraction ───────────────────────────────────────────────────

def _extract_description(lines: list[DocumentLine]) -> tuple[str | None, FieldSource | None]:
    _desc_blocked = {
        "the", "a", "an", "this", "that", "your", "our",
        "questions", "inquiries", "inquiry", "support", "help",
        "information", "details", "contact", "any questions", "assistance"
    }

    # 1. Explicit label: "Description: Office supplies"
    for pno, lno, raw_line, line in lines:
        match = re.match(r"^\s*(?:description|details?|particulars?|narration|remarks?|memo)\s*[:#\-]\s*(.+?)\s*$", line, re.IGNORECASE)
        if match:
            desc_val = match.group(1).strip()
            if desc_val.lower() not in _desc_blocked:
                return desc_val, FieldSource(pno, lno, raw_line)

    # 2. Tabular invoice first line-item: table header "Item" or "Description" followed by item rows
    for idx, (pno, lno, raw_line, line) in enumerate(lines):
        if re.fullmatch(r"^\s*(?:item|items|item\s+description|service|services)\s*$", line.strip(), re.IGNORECASE):
            for off in range(1, 8):
                ni = idx + off
                if ni < len(lines):
                    cand = lines[ni][3].strip()
                    if not re.search(r"\b(?:unit\s+cost|cost|quantity|qty|amount|price|rate|subtotal|tax)\b", cand, re.IGNORECASE):
                        if len(cand) >= 3 and not re.match(r"^\$?[\d,.]+$", cand) and cand.lower() not in _desc_blocked:
                            return cand, FieldSource(lines[ni][0], lines[ni][1], lines[ni][2])

    # 3. Spoken purchase: "I bought a table lamp and a privilege card"
    for pno, lno, raw_line, line in lines:
        bought_match = re.search(
            r"(?i)\b(?:bought|purchased|ordered|got)\s+(?:a\s+|an\s+|some\s+)?([A-Za-z0-9\s&,\-]{3,60}?)(?:\s+from|\s+at|\s+for|\s+the\s+total|\s+total|[,\.]|$)",
            line,
        )
        if bought_match:
            desc_val = bought_match.group(1).strip()
            if len(desc_val) >= 3 and desc_val.lower() not in _desc_blocked:
                return desc_val, FieldSource(pno, lno, raw_line)

    # 4. Spoken expense category: "under office supplies"
    for pno, lno, raw_line, line in lines:
        cat_match = re.search(r"(?i)\b(?:under|category|account)\s+([A-Za-z0-9\s&.\-]{3,40}?)(?:\s+please|[,\.]|$)", line)
        if cat_match:
            desc_val = cat_match.group(1).strip()
            if len(desc_val) >= 3 and desc_val.lower() not in _desc_blocked:
                return desc_val, FieldSource(pno, lno, raw_line)

    # 5. Payment / subscription notice: "payment for the Annual Cloud Hosting subscription"
    for pno, lno, raw_line, line in lines:
        pay_for = re.search(
            r"(?i)\b(?:payment\s+for|subscription\s+for|invoice\s+for|charge\s+for|bill\s+for|fee\s+for)\s+(?:the\s+)?([A-Za-z0-9\s&.\-]{3,60}?)(?:\s+is|\s+was|\s+due|\s+on|\s+at|[,\.]|$)",
            line,
        )
        if pay_for:
            desc_val = pay_for.group(1).strip()
            if len(desc_val) >= 3 and desc_val.lower() not in _desc_blocked:
                return desc_val, FieldSource(pno, lno, raw_line)

    # 6. Spoken: "for coffee", "towards electric bill payment"
    for pno, lno, raw_line, line in lines:
        spoken = re.search(
            r"(?i)\b(?:for|towards|regarding|purpose\s+of|re\s*:)\s+([A-Za-z0-9\s&.\-]{3,60}?)(?:\s+on|\s+at|\s+dated|\s+ref|\s+via|[,\.]|$)",
            line,
        )
        if spoken:
            desc_val = spoken.group(1).strip()
            if len(desc_val) >= 3 and desc_val.lower() not in _desc_blocked:
                return desc_val, FieldSource(pno, lno, raw_line)

    return None, None


# ─── Top-level field extraction ───────────────────────────────────────────────

def extract_financial_fields(input_data: str | list[tuple[int, str]]) -> dict[str, Any]:
    """Extract financial fields from single-page text or multi-page PDF documents."""
    lines = to_document_lines(input_data)

    date, date_source = _extract_date(lines)
    amount, amount_source, amount_method = extract_final_amount(lines)
    currency, currency_source = _extract_currency(lines, amount_source=amount_source)
    invoice_number, invoice_source = _extract_document_number(lines)
    party_name, party_source = _extract_party_name(lines)
    description, description_source = _extract_description(lines)

    fields: dict[str, Any] = {
        "date": date,
        "amount": _normalize_number(amount) if amount else None,
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
