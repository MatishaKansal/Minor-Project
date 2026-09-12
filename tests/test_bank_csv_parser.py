from pathlib import Path
import pytest

from backend.parsers.bank_csv_parser import parse_bank_csv_file, parse_bank_csv_text
from backend.schemas.evidence import BankStatementEvidence, BankTransaction


def test_parse_bank_csv_separate_debit_credit_columns(tmp_path: Path):
    csv_content = (
        "Date,Description,Debit,Credit,Balance\n"
        "2026-01-05,Salary Deposit,,3500.00,3500.00\n"
        "2026-01-08,Grocery Store,120.50,,3379.50\n"
        "2026-01-12,Electric Utility,85.00,,3294.50\n"
        "2026-01-15,Freelance Payment,,450.00,3744.50\n"
    )
    csv_file = tmp_path / "statement_split.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    result = parse_bank_csv_file(csv_file)

    assert result.processing_status == "success"
    assert result.source_type == "bank_csv"
    assert result.file_name == "statement_split.csv"
    assert len(result.transactions) == 4

    # Verify transactions
    t1 = result.transactions[0]
    assert t1.date == "2026-01-05"
    assert t1.description == "Salary Deposit"
    assert t1.amount == "3500.00"
    assert t1.transaction_type == "credit"
    assert t1.balance == "3500.00"

    t2 = result.transactions[1]
    assert t2.date == "2026-01-08"
    assert t2.description == "Grocery Store"
    assert t2.amount == "120.50"
    assert t2.transaction_type == "debit"
    assert t2.balance == "3379.50"

    # Verify summary
    summary = result.summary
    assert summary is not None
    assert summary.total_transactions == 4
    assert summary.total_deposits == "3950.00"
    assert summary.total_withdrawals == "205.50"
    assert summary.net_cash_flow == "3744.50"
    assert summary.opening_balance == "3500.00"
    assert summary.closing_balance == "3744.50"
    assert summary.statement_period_start == "2026-01-05"
    assert summary.statement_period_end == "2026-01-15"


def test_parse_bank_csv_single_amount_column():
    csv_content = (
        "Txn Date,Particulars,Amount,Running Balance\n"
        "10/02/2026,Client Invoice Payment,1200.00,5200.00\n"
        "12/02/2026,Office Supplies,-75.40,5124.60\n"
        "15/02/2026,Software Subscription,(49.99),5074.61\n"
    )

    result = parse_bank_csv_text(csv_content, source_name="single_amt.csv")

    assert result.processing_status == "success"
    assert len(result.transactions) == 3

    assert result.transactions[0].date == "2026-02-10"
    assert result.transactions[0].amount == "1200.00"
    assert result.transactions[0].transaction_type == "credit"

    assert result.transactions[1].date == "2026-02-12"
    assert result.transactions[1].amount == "75.40"
    assert result.transactions[1].transaction_type == "debit"

    assert result.transactions[2].date == "2026-02-15"
    assert result.transactions[2].amount == "49.99"
    assert result.transactions[2].transaction_type == "debit"

    assert result.summary.total_deposits == "1200.00"
    assert result.summary.total_withdrawals == "125.39"


def test_parse_bank_csv_with_drcr_type_column():
    csv_content = (
        "Date,Narration,Amount,Type,Balance\n"
        "2026-03-01,ATM Withdrawal,100.00,DR,900.00\n"
        "2026-03-02,Interest Credit,15.20,CR,915.20\n"
    )

    result = parse_bank_csv_text(csv_content)

    assert result.processing_status == "success"
    assert len(result.transactions) == 2
    assert result.transactions[0].transaction_type == "debit"
    assert result.transactions[0].amount == "100.00"
    assert result.transactions[1].transaction_type == "credit"
    assert result.transactions[1].amount == "15.20"


def test_parse_bank_csv_with_preamble_metadata():
    csv_content = (
        "Bank Name: Maybank Islamic\n"
        "Account No: 514012345678\n"
        "Currency: MYR\n"
        "Statement Period: 01/01/2026 to 31/01/2026\n"
        "\n"
        "Date,Details,Ref No,Debit,Credit,Balance\n"
        "02/01/2026,Transfer from Ali,TRX-001,,250.00,1250.00\n"
        "10/01/2026,Petrol Station,POS-882,60.00,,1190.00\n"
    )

    result = parse_bank_csv_text(csv_content, source_name="maybank.csv")

    assert result.processing_status == "success"
    assert result.currency == "MYR"
    assert result.party_name == "Maybank Islamic"
    assert result.invoice_number == "514012345678"

    summary = result.summary
    assert summary.bank_name == "Maybank Islamic"
    assert summary.account_number == "514012345678"
    assert summary.currency == "MYR"
    assert summary.statement_period_start == "2026-01-01"
    assert summary.statement_period_end == "2026-01-31"
    assert summary.total_transactions == 2
    assert len(result.provenance) == 2


def test_parse_bank_csv_semicolon_delimiter():
    csv_content = (
        "Date;Description;Debit;Credit;Balance\n"
        "2026-04-01;Consulting Fee;;2000.00;2000.00\n"
        "2026-04-05;Rent;800.00;;1200.00\n"
    )

    result = parse_bank_csv_text(csv_content)

    assert result.processing_status == "success"
    assert len(result.transactions) == 2
    assert result.transactions[0].amount == "2000.00"
    assert result.transactions[0].transaction_type == "credit"
    assert result.transactions[1].amount == "800.00"
    assert result.transactions[1].transaction_type == "debit"


def test_parse_bank_tsv_tab_delimiter():
    tsv_content = (
        "Date\tDescription\tAmount\tBalance\n"
        "2026-05-01\tDeposit\t500.00\t1500.00\n"
        "2026-05-02\tLunch\t-25.00\t1475.00\n"
    )

    result = parse_bank_csv_text(tsv_content, source_name="statement.tsv")

    assert result.processing_status == "success"
    assert len(result.transactions) == 2


def test_parse_empty_bank_csv_fails_gracefully():
    result = parse_bank_csv_text("   \n\t  ")

    assert result.processing_status == "failed"
    assert result.error is not None
    assert "empty" in result.error.lower()


def test_parse_nonexistent_bank_csv_file_fails_gracefully(tmp_path: Path):
    missing_file = tmp_path / "does_not_exist.csv"

    result = parse_bank_csv_file(missing_file)

    assert result.processing_status == "failed"
    assert "does not exist" in result.error


def test_parse_unsupported_bank_csv_format_fails(tmp_path: Path):
    invalid_file = tmp_path / "document.pdf"
    invalid_file.write_text("dummy", encoding="utf-8")

    result = parse_bank_csv_file(invalid_file)

    assert result.processing_status == "failed"
    assert "Unsupported CSV format" in result.error


def test_parse_bank_csv_persists_to_database_with_mock(monkeypatch):
    import backend.database.supabase_repository as repo

    def mock_persist_bank_statement_evidence(csv_text, evidence, source_name="bank_statement.csv", business_id=None, file_type="bank_statement", client=None):
        return {"file_id": "bank-file-uuid-1", "evidence_id": "bank-evidence-uuid-2"}

    monkeypatch.setattr(repo, "persist_bank_statement_evidence", mock_persist_bank_statement_evidence)

    csv_content = (
        "Date,Description,Amount,Balance\n"
        "2026-01-01,Deposit,1000.00,1000.00\n"
    )

    result = parse_bank_csv_text(csv_content, persist_to_database=True)

    assert result.processing_status == "success"
    assert result.database_file_id == "bank-file-uuid-1"
    assert result.database_evidence_id == "bank-evidence-uuid-2"


def test_bank_statement_evidence_schema_compatibility():
    csv_content = (
        "Date,Description,Amount\n"
        "2026-01-01,Service Fee,-15.00\n"
    )
    result = parse_bank_csv_text(csv_content)

    dumped = result.model_dump()
    round_trip = BankStatementEvidence.model_validate(dumped)

    assert round_trip == result
    assert round_trip.transactions[0].amount == "15.00"
    assert round_trip.transactions[0].transaction_type == "debit"
