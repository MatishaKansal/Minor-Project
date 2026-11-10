"""Persistence API for audit engagements, testing, and findings."""

import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.audit.engine import evaluate_population
from backend.auth import (
    AuthenticatedUser,
    get_authenticated_database_client,
    get_current_user,
    require_roles,
)

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit", tags=["audit"])


class EngagementCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    entity_name: str = Field(min_length=1, max_length=200)
    period_start: date
    period_end: date
    financial_year: str = Field(min_length=4, max_length=20)
    audit_type: str = Field(min_length=1, max_length=100)
    audit_scope: str = Field(min_length=1, max_length=4000)
    materiality_threshold: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    reporting_currency: str = Field(min_length=3, max_length=3)
    assigned_auditor: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_period(self) -> "EngagementCreate":
        if self.period_end < self.period_start:
            raise ValueError("period_end must not precede period_start")
        return self


class TransactionLink(BaseModel):
    invoice_ids: list[UUID] = Field(min_length=1, max_length=500)


class FindingDecision(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: Literal["Under Review", "Confirmed", "False Positive", "Resolved", "Closed"]
    comment: str = Field(min_length=1, max_length=4000)


class EngagementStatusUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: Literal["Draft", "Planning", "In Progress", "Under Review", "Completed", "Archived"]
    comment: str = Field(min_length=1, max_length=4000)


def _client_and_business(user: AuthenticatedUser) -> tuple[Any, str]:
    return get_authenticated_database_client(), user.business_id


def _log_event(
    client: Any,
    business_id: str,
    engagement_id: str,
    event_type: str,
    details: dict[str, Any],
    user: AuthenticatedUser,
) -> None:
    client.table("audit_log").insert({
        "business_id": business_id,
        "engagement_id": engagement_id,
        "event_type": event_type,
        "actor_user_id": user.user_id,
        "actor_label": user.email or user.user_id,
        "details": details,
    }).execute()


@router.get("/engagements")
def list_engagements(user: AuthenticatedUser = Depends(require_roles("administrator", "auditor", "reviewer", "read_only_viewer"))) -> dict[str, Any]:
    client, active_business = _client_and_business(user)
    rows = client.table("audit_engagement").select("*").eq("business_id", active_business).order("created_at", desc=True).execute().data or []
    return {"engagements": rows}


@router.get("/summary")
def audit_summary(user: AuthenticatedUser = Depends(require_roles("administrator", "auditor", "reviewer", "read_only_viewer"))) -> dict[str, Any]:
    client, active_business = _client_and_business(user)
    engagements = client.table("audit_engagement").select(
        "engagement_id,entity_name,period_start,period_end,status,materiality_threshold,reporting_currency"
    ).eq("business_id", active_business).order("created_at", desc=True).execute().data or []
    if not engagements:
        return {
            "engagement_count": 0,
            "active_engagement_count": 0,
            "transaction_count": 0,
            "transaction_value": "0",
            "transaction_value_complete": True,
            "transaction_currency": "INR",
            "tested_transaction_count": 0,
            "coverage_percent": 0,
            "open_findings": 0,
            "high_risk_findings": 0,
            "recent_findings": [],
            "engagements": [],
        }
    engagement_ids = [row["engagement_id"] for row in engagements]
    links = client.table("audit_engagement_transaction").select(
        "engagement_id,invoice_id"
    ).in_("engagement_id", engagement_ids).execute().data or []
    invoice_ids = list({row["invoice_id"] for row in links})
    invoices = []
    if invoice_ids:
        invoices = client.table("invoice").select(
            "invoice_id,invoice_total,currency"
        ).eq("business_id", active_business).in_("invoice_id", invoice_ids).execute().data or []
    results = client.table("audit_test_result").select(
        "invoice_id"
    ).in_("engagement_id", engagement_ids).execute().data or []
    all_findings = client.table("audit_finding").select(
        "severity,status"
    ).in_("engagement_id", engagement_ids).execute().data or []
    findings = client.table("audit_finding").select(
        "finding_id,engagement_id,invoice_id,rule_id,title,severity,severity_basis,financial_exposure,status,created_at"
    ).in_("engagement_id", engagement_ids).order("created_at", desc=True).limit(20).execute().data or []

    tested_ids = {row["invoice_id"] for row in results}
    total_linked = len({row["invoice_id"] for row in links})
    amounts = [_decimal_amount(row.get("invoice_total")) for row in invoices]
    available_amounts = [value for value in amounts if value is not None]
    amount = sum(available_amounts, start=Decimal(0))
    currencies = {
        str(row.get("currency") or "").strip().upper()
        for row in invoices
    }
    value_complete = total_linked == 0 or (
        len(invoices) == total_linked
        and len(available_amounts) == total_linked
        and len(currencies) == 1
        and "" not in currencies
    )
    open_statuses = {"Open", "Under Review", "Confirmed"}
    return {
        "engagement_count": len(engagements),
        "active_engagement_count": sum(row["status"] in {"Planning", "In Progress", "Under Review"} for row in engagements),
        "transaction_count": total_linked,
        "transaction_value": str(amount) if total_linked and value_complete else None,
        "transaction_value_complete": value_complete,
        "transaction_currency": next(iter(currencies)) if len(currencies) == 1 and "" not in currencies else None,
        "tested_transaction_count": len(tested_ids),
        "coverage_percent": round(len(tested_ids) * 100 / total_linked) if total_linked else 0,
        "open_findings": sum(row["status"] in open_statuses for row in all_findings),
        "high_risk_findings": sum(row["severity"] in {"High", "Critical"} and row["status"] in open_statuses for row in all_findings),
        "recent_findings": findings,
        "engagements": engagements,
    }


@router.post("/engagements", status_code=201)
def create_engagement(
    payload: EngagementCreate,
    user: AuthenticatedUser = Depends(require_roles("administrator", "auditor")),
) -> dict[str, Any]:
    client, active_business = _client_and_business(user)
    row = payload.model_dump(mode="json")
    row["business_id"] = active_business
    row["reporting_currency"] = payload.reporting_currency.upper()
    row["created_by_user_id"] = user.user_id
    created = client.table("audit_engagement").insert(row).execute().data or []
    if not created:
        raise HTTPException(status_code=502, detail="Supabase did not return the created engagement.")
    engagement = created[0]
    _log_event(client, active_business, engagement["engagement_id"], "engagement.created", {"entity_name": payload.entity_name}, user)
    return {"engagement": engagement}


@router.patch("/engagements/{engagement_id}/status")
def update_engagement_status(
    engagement_id: UUID,
    payload: EngagementStatusUpdate,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    if payload.status == "Completed" and user.role not in {"administrator", "reviewer"}:
        raise HTTPException(status_code=403, detail="Only a reviewer or administrator can approve engagement completion.")
    if payload.status == "Archived" and user.role != "administrator":
        raise HTTPException(status_code=403, detail="Only an administrator can archive a completed engagement.")
    if payload.status not in {"Completed", "Archived"} and user.role not in {"administrator", "auditor"}:
        raise HTTPException(status_code=403, detail="Only an auditor or administrator can change this engagement status.")

    client, active_business = _client_and_business(user)
    engagement_id_str = str(engagement_id)
    rows = client.table("audit_engagement").select(
        "engagement_id,status"
    ).eq("engagement_id", engagement_id_str).eq("business_id", active_business).limit(1).execute().data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Engagement was not found.")
    current = rows[0]["status"]
    allowed = {
        "Draft": {"Planning"},
        "Planning": {"In Progress"},
        "In Progress": {"Under Review"},
        "Under Review": {"In Progress", "Completed"},
        "Completed": {"Archived"},
        "Archived": set(),
    }
    if payload.status not in allowed[current]:
        raise HTTPException(status_code=409, detail=f"Engagement cannot transition from {current} to {payload.status}.")
    if payload.status == "Completed":
        runs = client.table("audit_run").select("run_id").eq("engagement_id", engagement_id_str).limit(1).execute().data or []
        if not runs:
            raise HTTPException(status_code=409, detail="Run audit procedures before completing this engagement.")
        unresolved = client.table("audit_finding").select("finding_id").eq("engagement_id", engagement_id_str).in_("status", ["Open", "Under Review", "Confirmed"]).limit(1).execute().data or []
        if unresolved:
            raise HTTPException(status_code=409, detail="Resolve or close all findings before completing this engagement.")
    updated = client.table("audit_engagement").update({
        "status": payload.status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("engagement_id", engagement_id_str).execute().data or []
    _log_event(client, active_business, engagement_id_str, "engagement.status_changed", {
        "from": current,
        "to": payload.status,
        "comment": payload.comment,
    }, user)
    return {"engagement": updated[0] if updated else {"engagement_id": engagement_id_str, "status": payload.status}}


@router.get("/invoices")
def list_invoices(
    user: AuthenticatedUser = Depends(require_roles("administrator", "auditor", "reviewer", "read_only_viewer")),
) -> dict[str, Any]:
    client, active_business = _client_and_business(user)
    rows = client.table("invoice").select(
        "invoice_id,file_id,invoice_number,invoice_date,seller_name,invoice_total,currency"
    ).eq("business_id", active_business).order("invoice_date", desc=True).limit(500).execute().data or []
    return {"invoices": rows}


@router.get("/engagements/{engagement_id}")
def get_engagement(
    engagement_id: UUID,
    user: AuthenticatedUser = Depends(require_roles("administrator", "auditor", "reviewer", "read_only_viewer")),
) -> dict[str, Any]:
    client, active_business = _client_and_business(user)
    engagement_rows = client.table("audit_engagement").select("*").eq("engagement_id", str(engagement_id)).eq("business_id", active_business).limit(1).execute().data or []
    if not engagement_rows:
        raise HTTPException(status_code=404, detail="Engagement was not found.")
    links = client.table("audit_engagement_transaction").select("invoice_id").eq("engagement_id", str(engagement_id)).execute().data or []
    invoice_ids = [row["invoice_id"] for row in links]
    invoices = []
    if invoice_ids:
        invoices = client.table("invoice").select(
            "invoice_id,file_id,invoice_number,invoice_date,due_date,seller_name,subtotal,cgst_total,sgst_total,igst_total,cess_total,round_off,invoice_total,currency"
        ).in_("invoice_id", invoice_ids).eq("business_id", active_business).execute().data or []
    findings = client.table("audit_finding").select("*").eq("engagement_id", str(engagement_id)).order("created_at", desc=True).limit(500).execute().data or []
    runs = client.table("audit_run").select("*").eq("engagement_id", str(engagement_id)).order("created_at", desc=True).limit(20).execute().data or []
    latest_results = []
    if runs:
        latest_results = client.table("audit_test_result").select(
            "invoice_id,rule_id,outcome,result_details,created_at"
        ).eq("run_id", runs[0]["run_id"]).order("created_at").limit(5000).execute().data or []
    return {"engagement": engagement_rows[0], "invoices": invoices, "findings": findings, "runs": runs, "latest_results": latest_results}


@router.post("/engagements/{engagement_id}/transactions", status_code=201)
def link_transactions(
    engagement_id: UUID,
    payload: TransactionLink,
    user: AuthenticatedUser = Depends(require_roles("administrator", "auditor")),
) -> dict[str, Any]:
    client, active_business = _client_and_business(user)
    engagement_id_str = str(engagement_id)
    engagement = client.table("audit_engagement").select("engagement_id").eq("engagement_id", engagement_id_str).eq("business_id", active_business).limit(1).execute().data or []
    if not engagement:
        raise HTTPException(status_code=404, detail="Engagement was not found.")
    ids = list(dict.fromkeys(str(value) for value in payload.invoice_ids))
    invoices = client.table("invoice").select("invoice_id").eq("business_id", active_business).in_("invoice_id", ids).execute().data or []
    valid_ids = {row["invoice_id"] for row in invoices}
    if valid_ids != set(ids):
        raise HTTPException(status_code=422, detail="Every invoice must belong to the engagement's business.")
    existing = client.table("audit_engagement_transaction").select("invoice_id").eq("engagement_id", engagement_id_str).in_("invoice_id", ids).execute().data or []
    existing_ids = {row["invoice_id"] for row in existing}
    additions = [{"engagement_id": engagement_id_str, "invoice_id": value} for value in ids if value not in existing_ids]
    if additions:
        client.table("audit_engagement_transaction").insert(additions).execute()
    _log_event(client, active_business, engagement_id_str, "transactions.linked", {"invoice_ids": ids}, user)
    return {"linked": len(additions), "already_linked": len(existing_ids)}


@router.post("/engagements/{engagement_id}/run", status_code=201)
def execute_audit(
    engagement_id: UUID,
    user: AuthenticatedUser = Depends(require_roles("administrator", "auditor")),
) -> dict[str, Any]:
    client, active_business = _client_and_business(user)
    engagement_id_str = str(engagement_id)
    engagement_rows = client.table("audit_engagement").select("*").eq("engagement_id", engagement_id_str).eq("business_id", active_business).limit(1).execute().data or []
    if not engagement_rows:
        raise HTTPException(status_code=404, detail="Engagement was not found.")
    engagement = engagement_rows[0]
    links = client.table("audit_engagement_transaction").select("invoice_id").eq("engagement_id", engagement_id_str).execute().data or []
    invoice_ids = [row["invoice_id"] for row in links]
    if not invoice_ids:
        raise HTTPException(status_code=409, detail="Link at least one invoice before running audit procedures.")
    invoices = client.table("invoice").select(
        "invoice_id,file_id,invoice_number,invoice_date,seller_name,subtotal,cgst_total,sgst_total,igst_total,cess_total,round_off,invoice_total,currency"
    ).eq("business_id", active_business).in_("invoice_id", invoice_ids).execute().data or []
    if len(invoices) != len(invoice_ids):
        raise HTTPException(status_code=409, detail="One or more linked invoices are no longer available in this business.")
    item_rows = client.table("invoice_item").select(
        "invoice_id,taxable_value,gst_rate,cgst_amount,sgst_amount,igst_amount,cess_amount"
    ).in_("invoice_id", invoice_ids).execute().data or []
    items_by_invoice: dict[str, list[dict[str, Any]]] = {}
    for item in item_rows:
        items_by_invoice.setdefault(item["invoice_id"], []).append(item)

    test_results = evaluate_population(
        invoices,
        items_by_invoice,
        engagement["period_start"],
        engagement["period_end"],
        engagement.get("materiality_threshold"),
    )
    run_rows = client.table("audit_run").insert({
        "engagement_id": engagement_id_str,
        "population_count": len(invoices),
        "procedure_count": len(test_results),
        "created_by_user_id": user.user_id,
        "created_by_label": user.email or user.user_id,
    }).execute().data or []
    if not run_rows:
        raise HTTPException(status_code=502, detail="Supabase did not return the audit run.")
    run_id = run_rows[0]["run_id"]
    for offset in range(0, len(test_results), 500):
        chunk = test_results[offset:offset + 500]
        client.table("audit_test_result").insert([
            {
                "run_id": run_id,
                "engagement_id": engagement_id_str,
                "invoice_id": result["invoice_id"],
                "rule_id": result["rule_id"],
                "outcome": result["outcome"],
                "result_details": {key: value for key, value in result.items() if key not in {"invoice_id", "rule_id", "outcome"}},
            }
            for result in chunk
        ]).execute()

    findings_created = 0
    for result in test_results:
        if result["outcome"] != "EXCEPTION":
            continue
        invoice = next(row for row in invoices if row["invoice_id"] == result["invoice_id"])
        fingerprint = f'{result["invoice_id"]}:{result["rule_id"]}'
        fields = {
            "last_seen_run_id": run_id,
            "title": f'{result["rule_id"]}: {result["procedure"]}',
            "description": result["explanation"],
            "expected_value": result["expected_value"],
            "actual_value": result["actual_value"],
            "financial_discrepancy": result["discrepancy"],
            "financial_exposure": result["financial_exposure"],
            "severity": result["severity"],
            "severity_basis": result["severity_basis"],
            "evidence_refs": [{"invoice_id": invoice["invoice_id"], "file_id": invoice.get("file_id")}],
        }
        old = client.table("audit_finding").select("finding_id").eq("engagement_id", engagement_id_str).eq("fingerprint", fingerprint).limit(1).execute().data or []
        if old:
            client.table("audit_finding").update(fields).eq("finding_id", old[0]["finding_id"]).execute()
        else:
            client.table("audit_finding").insert({
                **fields,
                "engagement_id": engagement_id_str,
                "run_id": run_id,
                "invoice_id": invoice["invoice_id"],
                "rule_id": result["rule_id"],
                "fingerprint": fingerprint,
                "status": "Open",
                "created_by_user_id": user.user_id,
            }).execute()
            findings_created += 1
    counts: dict[str, int] = {}
    for result in test_results:
        counts[result["outcome"]] = counts.get(result["outcome"], 0) + 1
    _log_event(client, active_business, engagement_id_str, "audit.run.completed", {"run_id": run_id, "outcomes": counts}, user)
    return {"run_id": run_id, "population_count": len(invoices), "procedure_count": len(test_results), "outcomes": counts, "findings_created": findings_created, "results": test_results}


@router.post("/findings/{finding_id}/decision")
def decide_finding(
    finding_id: UUID,
    payload: FindingDecision,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    if payload.status in {"Confirmed", "False Positive", "Closed"} and user.role not in {"administrator", "reviewer"}:
        raise HTTPException(status_code=403, detail="Only a reviewer or administrator can make this finding decision.")
    if payload.status in {"Under Review", "Resolved"} and user.role not in {"administrator", "auditor", "reviewer"}:
        raise HTTPException(status_code=403, detail="Your audit role cannot change this finding status.")

    client, active_business = _client_and_business(user)
    rows = client.table("audit_finding").select("*").eq("finding_id", str(finding_id)).limit(1).execute().data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Finding was not found.")
    finding = rows[0]
    engagement = client.table("audit_engagement").select("business_id").eq("engagement_id", finding["engagement_id"]).limit(1).execute().data or []
    if not engagement or engagement[0]["business_id"] != active_business:
        raise HTTPException(status_code=404, detail="Finding was not found.")
    allowed = {
        "Open": {"Under Review", "Confirmed", "False Positive", "Resolved", "Closed"},
        "Under Review": {"Confirmed", "False Positive", "Resolved", "Closed"},
        "Confirmed": {"Resolved", "Closed"},
        "False Positive": {"Closed"},
        "Resolved": {"Closed"},
        "Closed": set(),
    }
    if payload.status not in allowed[finding["status"]]:
        raise HTTPException(status_code=409, detail=f"Finding cannot transition from {finding['status']} to {payload.status}.")
    comments = finding.get("review_comments") or []
    changed_at = datetime.now(timezone.utc).isoformat()
    comments.append({
        "comment": payload.comment,
        "status": payload.status,
        "created_at": changed_at,
        "actor_user_id": user.user_id,
        "actor_label": user.email or user.user_id,
    })
    updated = client.table("audit_finding").update({
        "status": payload.status,
        "review_comments": comments,
        "updated_at": changed_at,
    }).eq("finding_id", str(finding_id)).execute().data or []
    _log_event(client, active_business, finding["engagement_id"], "finding.status_changed", {
        "finding_id": str(finding_id),
        "status": payload.status,
        "comment": payload.comment,
    }, user)
    return {"finding": updated[0] if updated else {**finding, "status": payload.status, "review_comments": comments}}


def _decimal_amount(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        LOGGER.warning("Skipping non-numeric invoice total in audit summary: %r", value)
        return None
