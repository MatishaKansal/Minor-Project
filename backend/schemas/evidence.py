from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class EvidenceProvenance(BaseModel):
    field_name: str
    value: Optional[str] = None
    source_text: str
    line_number: Optional[int] = None
    page_number: int = Field(default=1, ge=1)
    extraction_method: str
    confidence: Optional[float] = None


class InvoiceItem(BaseModel):
    """Normalized product/service row extracted from an Indian invoice."""

    model_config = ConfigDict(extra="allow")

    line_number: Optional[int] = None
    description: Optional[str] = None
    hsn_sac_code: Optional[str] = None
    quantity: Optional[str] = None
    unit: Optional[str] = None
    unit_price: Optional[str] = None
    discount_amount: Optional[str] = None
    taxable_value: Optional[str] = None
    gst_rate: Optional[str] = None
    cgst_amount: Optional[str] = None
    sgst_amount: Optional[str] = None
    igst_amount: Optional[str] = None
    cess_amount: Optional[str] = None
    line_total: Optional[str] = None


class Evidence(BaseModel):
    """Structured evidence produced from one document, image, or text file."""

    model_config = ConfigDict(extra="allow")

    source_type: str = "image"
    file_name: str
    extracted_text: str = ""
    page_number: int = Field(default=1, ge=1)
    language: str = "eng"
    confidence_score: Optional[float] = Field(default=None, ge=0, le=100)
    processing_status: str
    error: Optional[str] = None
    provenance: list[EvidenceProvenance] = Field(default_factory=list)
    database_file_id: Optional[str] = None
    database_evidence_id: Optional[str] = None  # Legacy alias for database_invoice_id.
    database_invoice_id: Optional[str] = None
    page_count: Optional[int] = None

    # Standard financial fields
    date: Optional[str] = None
    amount: Optional[str] = None
    currency: Optional[str] = None
    party_name: Optional[str] = None
    invoice_number: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[str] = None
    invoice_type: Optional[str] = None
    seller_name: Optional[str] = None
    seller_gstin: Optional[str] = None
    seller_pan: Optional[str] = None
    seller_address: Optional[dict] = None
    buyer_name: Optional[str] = None
    buyer_gstin: Optional[str] = None
    buyer_pan: Optional[str] = None
    buyer_address: Optional[dict] = None
    place_of_supply: Optional[str] = None
    reverse_charge: Optional[bool] = None
    subtotal: Optional[str] = None
    discount_amount: Optional[str] = None
    cgst_total: Optional[str] = None
    sgst_total: Optional[str] = None
    igst_total: Optional[str] = None
    cess_total: Optional[str] = None
    round_off: Optional[str] = None
    invoice_total: Optional[str] = None
    payment_terms: Optional[str] = None
    notes: Optional[str] = None
    items: list[InvoiceItem] = Field(default_factory=list)
    payments: list[dict] = Field(default_factory=list)


class BankTransaction(BaseModel):
    """A single normalized transaction row from a bank statement."""

    model_config = ConfigDict(extra="allow")

    date: Optional[str] = None
    description: str = ""
    amount: str
    transaction_type: str = "debit"  # "debit" | "credit"
    balance: Optional[str] = None
    reference_number: Optional[str] = None
    currency: Optional[str] = None
    category: Optional[str] = None
    line_number: Optional[int] = None
    raw_text: Optional[str] = None


class BankStatementSummary(BaseModel):
    """Summary analytics and metadata extracted from a bank statement."""

    model_config = ConfigDict(extra="allow")

    account_number: Optional[str] = None
    bank_name: Optional[str] = None
    statement_period_start: Optional[str] = None
    statement_period_end: Optional[str] = None
    opening_balance: Optional[str] = None
    closing_balance: Optional[str] = None
    total_deposits: Optional[str] = None
    total_withdrawals: Optional[str] = None
    net_cash_flow: Optional[str] = None
    total_transactions: int = 0
    currency: Optional[str] = None


class BankStatementEvidence(Evidence):
    """Structured evidence specialized for multi-transaction bank statements."""

    source_type: str = "bank_csv"
    summary: Optional[BankStatementSummary] = None
    transactions: list[BankTransaction] = Field(default_factory=list)
