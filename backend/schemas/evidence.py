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


class Evidence(BaseModel):
    """Structured evidence produced from one image or text PDF."""

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
    database_evidence_id: Optional[str] = None

    # Reserved fields for future non-LLM financial extraction.
    date: Optional[str] = None
    amount: Optional[str] = None
    currency: Optional[str] = None
    party_name: Optional[str] = None
    invoice_number: Optional[str] = None
    description: Optional[str] = None
