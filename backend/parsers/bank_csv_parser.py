"""Bank CSV statement parser and transaction normalization engine."""

import csv
import io
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.schemas.evidence import (
    BankStatementEvidence,
    BankStatementSummary,
    BankTransaction,
    EvidenceProvenance,
)

LOGGER = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".csv", ".tsv", ".txt"}
DEFAULT_LANGUAGE = "eng"


def validate_csv_path(file_path: str | os.PathLike[str]) -> Path:
    """Validate that the given path exists and is a supported CSV/TSV/text file."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"CSV file does not exist: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported CSV format '{path.suffix}'. Supported formats: .csv, .tsv")
    return path


def _detect_delimiter(sample_lines: list[str]) -> str:
    """Detect delimiter from header line candidates."""
    candidate_delimiters = [",", "\t", ";", "|"]
    for line in sample_lines:
        counts = {delim: line.count(delim) for delim in candidate_delimiters}
        best_delim, max_count = max(counts.items(), key=lambda x: x[1])
        if max_count >= 1:
            return best_delim
    return ","


def _clean_amount_str(raw: str | None) -> str | None:
    """Strip currency symbols, commas, and formatting noise from amount string."""
    if not raw:
        return None
    cleaned = raw.strip()
    # Remove currency symbols and spaces
    cleaned = re.sub(r"[$€£¥₹\s]|RM|USD|EUR|GBP|INR|MYR", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace(",", "")
    # Check if enclosed in parentheses (accounting negative)
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    match = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
    return match.group(0) if match else None


def _parse_date_str(raw_date: str | None) -> str | None:
    """Normalize various date formats into ISO YYYY-MM-DD."""
    if not raw_date:
        return None
    cleaned = raw_date.strip()
    date_formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d",
        "%d/%m/%y",
        "%m/%d/%y",
        "%d-%m-%y",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d.%m.%Y",
        "%Y.%m.%d",
    ]
    for fmt in date_formats:
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return cleaned


def _extract_preamble_metadata(lines: list[str]) -> dict[str, Any]:
    """Extract metadata (bank name, account number, currency, statement period) from preamble lines."""
    metadata: dict[str, Any] = {}
    for line in lines:
        cleaned = line.strip(" ,;\t|")
        if not cleaned:
            continue

        # Bank name
        bank_match = re.search(r"\b(?:bank(?:\s+name)?|institution)\s*[:=]\s*([A-Za-z0-9\s&.\-]+)", cleaned, re.IGNORECASE)
        if bank_match and "bank_name" not in metadata:
            metadata["bank_name"] = bank_match.group(1).strip()

        # Account number
        acc_match = re.search(r"\b(?:account|acct|acc)(?:\s+no\.?|\s+number|\s+#)?\s*[:=]\s*([A-Za-z0-9\-*X]+)", cleaned, re.IGNORECASE)
        if acc_match and "account_number" not in metadata:
            metadata["account_number"] = acc_match.group(1).strip()

        # Currency
        curr_match = re.search(r"\b(?:currency|curr)\s*[:=]\s*([A-Z]{3}|RM|EUR|USD|GBP|INR)", cleaned, re.IGNORECASE)
        if curr_match and "currency" not in metadata:
            metadata["currency"] = curr_match.group(1).upper().strip()

        # Period / dates
        period_match = re.search(
            r"\b(?:statement\s+period|period|date\s+range)\s*[:=]\s*(.+?)\s+(?:to|\bthrough\b|\btill\b|-)\s+(.+)$",
            cleaned,
            re.IGNORECASE,
        )
        if period_match:
            metadata["statement_period_start"] = _parse_date_str(period_match.group(1).strip())
            metadata["statement_period_end"] = _parse_date_str(period_match.group(2).strip())

    return metadata


def _locate_header_row(lines: list[str], delimiter: str) -> tuple[int, list[str]]:
    """Find the transaction table header row index and its column names."""
    key_markers = {
        "date", "trans date", "transaction date", "posting date", "value date", "txn date",
        "description", "narration", "particulars", "details", "memo", "payee", "remarks",
        "amount", "debit", "credit", "withdrawal", "deposit", "balance", "ref", "reference"
    }

    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            reader = list(csv.reader([line], delimiter=delimiter))
            if not reader or not reader[0]:
                continue
            cols = [c.strip().lower() for c in reader[0]]
            matched = sum(1 for c in cols if any(marker in c for marker in key_markers))
            if matched >= 2:
                return idx, [c.strip() for c in reader[0]]
        except Exception:
            continue

    return 0, []


def _resolve_column_indices(headers: list[str]) -> dict[str, int]:
    """Map standard fields (date, description, debit, credit, amount, balance, ref, etc.) to column indices."""
    col_map: dict[str, int] = {}
    normalized = [h.strip().lower() for h in headers]

    for idx, name in enumerate(normalized):
        # Date column
        if "date" in name and "date" not in col_map:
            col_map["date"] = idx
        # Description / Particulars
        elif any(k in name for k in ("description", "narration", "particular", "detail", "memo", "payee", "remark")) and "description" not in col_map:
            col_map["description"] = idx
        # Debit / Withdrawal
        elif any(k in name for k in ("debit", "withdrawal", "paid out", "money out", "dr")) and "debit" not in col_map:
            col_map["debit"] = idx
        # Credit / Deposit
        elif any(k in name for k in ("credit", "deposit", "paid in", "money in", "cr")) and "credit" not in col_map:
            col_map["credit"] = idx
        # Balance
        elif any(k in name for k in ("balance", "running", "closing bal", "bal")) and "balance" not in col_map:
            col_map["balance"] = idx
        # Reference / Cheque No
        elif any(k in name for k in ("ref", "cheque", "chq", "txn id", "trans id", "transaction id")) and "ref" not in col_map:
            col_map["ref"] = idx
        # Transaction type column (DR/CR)
        elif any(k in name for k in ("type", "d/c", "dr/cr", "cr/dr")) and "type" not in col_map:
            col_map["type"] = idx
        # Amount (if not already matched to debit/credit)
        elif "amount" in name or "amt" in name or "total" in name:
            if "amount" not in col_map:
                col_map["amount"] = idx

    return col_map


def parse_bank_csv_text(
    csv_text: str,
    source_name: str = "bank_statement.csv",
    language: str = DEFAULT_LANGUAGE,
    business_id: str | None = None,
    persist_to_database: bool = False,
    file_type: str = "bank_statement",
) -> BankStatementEvidence:
    """Parse raw bank statement CSV text and return a structured BankStatementEvidence."""
    try:
        if not csv_text or not csv_text.strip():
            raise ValueError("Bank CSV content is empty")

        lines = [line.rstrip("\r\n") for line in csv_text.splitlines() if line.strip()]
        if not lines:
            raise ValueError("Bank CSV contains no valid data rows")

        delimiter = _detect_delimiter(lines[:10])
        header_row_idx, headers = _locate_header_row(lines[:20], delimiter)

        if not headers:
            # Fallback: assume line 0 is header
            headers = [h.strip() for h in list(csv.reader([lines[0]], delimiter=delimiter))[0]]
            header_row_idx = 0

        preamble_lines = lines[:header_row_idx]
        preamble_meta = _extract_preamble_metadata(preamble_lines)
        col_map = _resolve_column_indices(headers)

        data_lines = lines[header_row_idx + 1:]
        transactions: list[BankTransaction] = []
        provenance_list: list[EvidenceProvenance] = []

        total_deposits_val = 0.0
        total_withdrawals_val = 0.0
        currency = preamble_meta.get("currency")

        csv_reader = csv.reader(io.StringIO("\n".join(data_lines)), delimiter=delimiter)

        detected_opening_balance = None
        detected_start_date = None

        for row_idx, row in enumerate(csv_reader, start=header_row_idx + 2):
            if not row or not any(field.strip() for field in row):
                continue

            # Date
            raw_date = row[col_map["date"]].strip() if "date" in col_map and col_map["date"] < len(row) else None
            parsed_date = _parse_date_str(raw_date)

            # Description
            desc = row[col_map["description"]].strip() if "description" in col_map and col_map["description"] < len(row) else ""

            # Reference
            ref_num = row[col_map["ref"]].strip() if "ref" in col_map and col_map["ref"] < len(row) else None

            # Balance
            raw_balance = row[col_map["balance"]].strip() if "balance" in col_map and col_map["balance"] < len(row) else None
            clean_balance = _clean_amount_str(raw_balance)

            # Check for explicit opening balance / brought forward row
            if re.search(r"\b(?:opening\s+bal(?:ance)?|brought\s+forward|b/f|balance\s+b/f)\b", desc, re.IGNORECASE):
                if clean_balance:
                    detected_opening_balance = clean_balance
                if parsed_date:
                    detected_start_date = parsed_date

            # Determine Amount and Type
            txn_amount: str | None = None
            txn_type = "debit"

            if "debit" in col_map or "credit" in col_map:
                # Separate Debit and Credit columns
                debit_raw = row[col_map["debit"]].strip() if "debit" in col_map and col_map["debit"] < len(row) else ""
                credit_raw = row[col_map["credit"]].strip() if "credit" in col_map and col_map["credit"] < len(row) else ""

                debit_clean = _clean_amount_str(debit_raw)
                credit_clean = _clean_amount_str(credit_raw)

                if credit_clean and float(credit_clean) != 0:
                    txn_amount = f"{abs(float(credit_clean)):.2f}"
                    txn_type = "credit"
                elif debit_clean and float(debit_clean) != 0:
                    txn_amount = f"{abs(float(debit_clean)):.2f}"
                    txn_type = "debit"
            elif "amount" in col_map and col_map["amount"] < len(row):
                # Single Amount column
                raw_amt = row[col_map["amount"]].strip()
                type_indicator = row[col_map["type"]].strip().upper() if "type" in col_map and col_map["type"] < len(row) else ""

                clean_amt = _clean_amount_str(raw_amt)
                if clean_amt:
                    val = float(clean_amt)
                    if type_indicator in {"DR", "DEBIT"} or "DR" in raw_amt.upper() or "-" in raw_amt or val < 0:
                        txn_amount = f"{abs(val):.2f}"
                        txn_type = "debit"
                    elif type_indicator in {"CR", "CREDIT"} or "CR" in raw_amt.upper() or val > 0:
                        txn_amount = f"{abs(val):.2f}"
                        txn_type = "credit"
                    else:
                        txn_amount = f"{abs(val):.2f}"
                        txn_type = "debit"

            if not txn_amount:
                continue

            num_amt = float(txn_amount)
            if txn_type == "credit":
                total_deposits_val += num_amt
            else:
                total_withdrawals_val += num_amt

            raw_row_text = delimiter.join(row)

            transaction = BankTransaction(
                date=parsed_date,
                description=desc,
                amount=txn_amount,
                transaction_type=txn_type,
                balance=clean_balance,
                reference_number=ref_num,
                currency=currency,
                line_number=row_idx,
                raw_text=raw_row_text,
            )
            transactions.append(transaction)

            # Record provenance for each transaction
            provenance_list.append(
                EvidenceProvenance(
                    field_name=f"transaction_{len(transactions)}",
                    value=f"{txn_type.upper()}: {txn_amount} ({desc})",
                    source_text=raw_row_text,
                    line_number=row_idx,
                    page_number=1,
                    extraction_method="csv_table_row",
                )
            )

        if not transactions:
            raise ValueError("Could not extract any valid financial transactions from the CSV")

        # Compute summary analytics
        dates = [t.date for t in transactions if t.date]
        if detected_start_date:
            dates.append(detected_start_date)
        start_date = preamble_meta.get("statement_period_start") or (min(dates) if dates else None)
        end_date = preamble_meta.get("statement_period_end") or (max(dates) if dates else None)

        # Determine chronology (whether CSV is newest-first or oldest-first)
        is_reverse_chronological = False
        if len(transactions) >= 2 and transactions[0].date and transactions[-1].date:
            if transactions[0].date > transactions[-1].date:
                is_reverse_chronological = True

        if is_reverse_chronological:
            closing_balance = preamble_meta.get("closing_balance") or (transactions[0].balance if transactions else None)
            opening_balance = detected_opening_balance or preamble_meta.get("opening_balance") or (transactions[-1].balance if transactions else None)
        else:
            opening_balance = detected_opening_balance or preamble_meta.get("opening_balance") or (transactions[0].balance if transactions else None)
            closing_balance = preamble_meta.get("closing_balance") or (transactions[-1].balance if transactions else None)

        net_cash_flow = total_deposits_val - total_withdrawals_val

        summary = BankStatementSummary(
            account_number=preamble_meta.get("account_number"),
            bank_name=preamble_meta.get("bank_name"),
            statement_period_start=start_date,
            statement_period_end=end_date,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            total_deposits=f"{total_deposits_val:.2f}",
            total_withdrawals=f"{total_withdrawals_val:.2f}",
            net_cash_flow=f"{net_cash_flow:.2f}",
            total_transactions=len(transactions),
            currency=currency,
        )

        evidence = BankStatementEvidence(
            source_type="bank_csv",
            file_name=source_name,
            extracted_text=csv_text,
            language=language,
            processing_status="success",
            provenance=provenance_list,
            date=end_date or start_date,
            amount=f"{abs(net_cash_flow):.2f}",
            currency=currency,
            party_name=summary.bank_name,
            invoice_number=summary.account_number,
            description=f"Bank statement with {len(transactions)} transactions",
            summary=summary,
            transactions=transactions,
        )

        if persist_to_database:
            from backend.database.supabase_repository import persist_bank_statement_evidence

            database_ids = persist_bank_statement_evidence(
                csv_text, evidence, source_name=source_name, business_id=business_id, file_type=file_type
            )
            evidence = evidence.model_copy(update={
                "database_file_id": database_ids["file_id"],
                "database_evidence_id": database_ids["evidence_id"],
            })

        return evidence

    except Exception as exc:
        LOGGER.exception("Bank CSV parsing failed for '%s'", source_name)
        return BankStatementEvidence(
            source_type="bank_csv",
            file_name=source_name,
            language=language,
            processing_status="failed",
            error=str(exc),
            summary=None,
            transactions=[],
        )


def parse_bank_csv_file(
    file_path: str | os.PathLike[str],
    language: str = DEFAULT_LANGUAGE,
    business_id: str | None = None,
    persist_to_database: bool = False,
    file_type: str = "bank_statement",
) -> BankStatementEvidence:
    """Read a bank statement CSV file from disk and return structured BankStatementEvidence."""
    path = Path(file_path)
    try:
        validated_path = validate_csv_path(path)
        try:
            content = validated_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = validated_path.read_text(encoding="latin-1")

        evidence = parse_bank_csv_text(
            csv_text=content,
            source_name=validated_path.name,
            language=language,
            business_id=business_id,
            persist_to_database=False,  # We handle database persistence with validated file path below
            file_type=file_type,
        )

        if evidence.processing_status == "success" and persist_to_database:
            from backend.database.supabase_repository import persist_bank_statement_evidence

            database_ids = persist_bank_statement_evidence(
                content, evidence, source_name=validated_path.name, business_id=business_id, file_type=file_type
            )
            evidence = evidence.model_copy(update={
                "database_file_id": database_ids["file_id"],
                "database_evidence_id": database_ids["evidence_id"],
            })

        return evidence

    except Exception as exc:
        LOGGER.exception("Bank CSV file parsing failed for %s", path)
        return BankStatementEvidence(
            source_type="bank_csv",
            file_name=path.name,
            language=language,
            processing_status="failed",
            error=str(exc),
            summary=None,
            transactions=[],
        )
