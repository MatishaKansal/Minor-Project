"""Point-of-Sale (POS) & Billing Software Ingestion Connector.

Supports:
- Option A: Production Webhook & REST API ingestion (Vyapar, Tally Prime, Zoho Books, Petpooja, Barcode Scanners)
- Option B: Interactive Barcode Scanner Simulation & Daily Sales Batch Sync
- Strict Frontend-driven Authorization Gate (blocks webhook/sync/scan if unauthorized)
- Automated GST / HSN Rate Validation & Sales-to-Bank Reconciliation Patrol
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
import secrets
import tempfile
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.actions.agent_orchestrator import evaluate_agentic_actions
from backend.actions.reconciliation_patrol import run_reconciliation_patrol
from backend.cascade.service import evidence_to_cascade_dict
from backend.services.ingestion_service import ingest_file

LOGGER = logging.getLogger(__name__)

# Sample Indian retail barcode catalog with authentic EAN-13, HSN codes, and GST rates
BARCODE_CATALOG: Dict[str, Dict[str, Any]] = {
    "8901030383712": {
        "barcode": "8901030383712",
        "name": "Maggi 2-Minute Masala Noodles 70g",
        "hsn": "1902",
        "mrp": 14.00,
        "taxable_price": 12.50,
        "gst_rate": 12.0,
        "unit": "PKT",
        "category": "Packaged Food",
    },
    "8901725181222": {
        "barcode": "8901725181222",
        "name": "Red Bull Energy Drink 250ml",
        "hsn": "2202",
        "mrp": 125.00,
        "taxable_price": 97.66,
        "gst_rate": 28.0,
        "unit": "CAN",
        "category": "Beverages",
    },
    "8901491101831": {
        "barcode": "8901491101831",
        "name": "Amul Pasteurised Butter 100g",
        "hsn": "0405",
        "mrp": 58.00,
        "taxable_price": 51.78,
        "gst_rate": 12.0,
        "unit": "BOX",
        "category": "Dairy",
    },
    "8901058852443": {
        "barcode": "8901058852443",
        "name": "Tata Salt Vacuum Evaporated 1kg",
        "hsn": "2501",
        "mrp": 28.00,
        "taxable_price": 28.00,
        "gst_rate": 0.0,
        "unit": "BAG",
        "category": "Staples",
    },
    "8901063012481": {
        "barcode": "8901063012481",
        "name": "Britannia Good Day Cashew 100g",
        "hsn": "1905",
        "mrp": 35.00,
        "taxable_price": 29.66,
        "gst_rate": 18.0,
        "unit": "PKT",
        "category": "Confectionery",
    },
    "8901030823416": {
        "barcode": "8901030823416",
        "name": "Dettol Liquid Handwash Refill 175ml",
        "hsn": "3401",
        "mrp": 99.00,
        "taxable_price": 83.90,
        "gst_rate": 18.0,
        "unit": "POUCH",
        "category": "Personal Care",
    },
    "8902080004562": {
        "barcode": "8902080004562",
        "name": "Aashirvaad Shudh Chakki Atta 5kg",
        "hsn": "1101",
        "mrp": 255.00,
        "taxable_price": 242.86,
        "gst_rate": 5.0,
        "unit": "BAG",
        "category": "Staples",
    },
}


class POSAuthorizePayload(BaseModel):
    mode: str = "demo"  # "demo" or "live"
    provider: str = "demo"  # "demo", "vyapar", "tally", "zoho_books", "petpooja", "custom_pos"
    store_name: str = "SmallBiz Supermart"
    gstin: str = "07AAAAA0000A1Z5"
    terminal_id: str = "POS-TERMINAL-01"


class POSLineItem(BaseModel):
    barcode: str
    item_name: str
    hsn_code: str
    quantity: float = 1.0
    unit_price: float
    taxable_amount: float
    gst_rate: float
    cgst: float
    sgst: float
    line_total: float


class POSBillPayload(BaseModel):
    bill_number: str
    store_name: Optional[str] = "SmallBiz Supermart"
    gstin: Optional[str] = "07AAAAA0000A1Z5"
    terminal_id: Optional[str] = "POS-TERMINAL-01"
    cashier_name: Optional[str] = "Cashier 01"
    customer_name: Optional[str] = "Walk-in Customer"
    customer_phone: Optional[str] = None
    payment_method: str = "UPI"  # "UPI", "Cash", "Card", "Split"
    payment_ref: Optional[str] = None
    items: List[POSLineItem]
    subtotal: float
    total_cgst: float
    total_sgst: float
    total_tax: float
    grand_total: float
    timestamp: Optional[str] = None


class BarcodeScanRequest(BaseModel):
    barcode: str
    quantity: float = 1.0
    payment_method: str = "UPI"
    business_id: Optional[str] = None


class POSConnector:
    """Manages Point-of-Sale / Billing Software integration and verification."""

    def __init__(self) -> None:
        self.is_authorized: bool = False
        self.mode: str = "demo"
        self.provider: str = "demo"
        self.store_name: str = "SmallBiz Supermart"
        self.gstin: str = "07AAAAA0000A1Z5"
        self.terminal_id: str = "POS-TERMINAL-01"
        self.api_key: Optional[str] = None  # Kept in-memory session only
        self.last_synced_at: Optional[str] = None
        self.bills_synced_count: int = 0
        self.total_sales_amount: float = 0.0
        self.recent_bills: List[Dict[str, Any]] = []
        self._bill_sequence: int = 1001

    def get_status(self) -> Dict[str, Any]:
        """Returns connector authorization status and configuration."""
        return {
            "is_authorized": self.is_authorized,
            "mode": self.mode,
            "provider": self.provider,
            "store_name": self.store_name,
            "gstin": self.gstin,
            "terminal_id": self.terminal_id,
            "has_api_key": bool(self.api_key),
            "webhook_endpoint": "/api/connectors/pos/webhook",
            "last_synced_at": self.last_synced_at,
            "bills_synced_count": self.bills_synced_count,
            "total_sales_amount": round(self.total_sales_amount, 2),
            "recent_bills": self.recent_bills[-5:] if self.recent_bills else [],
            "catalog_items": list(BARCODE_CATALOG.values()),
        }

    def authorize(self, payload: POSAuthorizePayload) -> Dict[str, Any]:
        """Authorizes the POS / Billing software connector via the frontend UI."""
        self.is_authorized = True
        self.mode = payload.mode.lower()
        self.provider = payload.provider.lower()
        self.store_name = payload.store_name or "SmallBiz Supermart"
        self.gstin = payload.gstin or "07AAAAA0000A1Z5"
        self.terminal_id = payload.terminal_id or "POS-TERMINAL-01"

        # Generate a secure in-memory session API key for webhook/REST integration
        self.api_key = f"pos_live_{secrets.token_hex(16)}"

        LOGGER.info(
            "POS Connector authorized in %s mode for store '%s' (Provider: %s)",
            self.mode, self.store_name, self.provider
        )

        return {
            "success": True,
            "message": f"Successfully authorized POS & Billing Connector for {self.store_name}.",
            "api_key": self.api_key,
            "webhook_endpoint": "/api/connectors/pos/webhook",
            "status": self.get_status(),
        }

    def revoke(self) -> Dict[str, Any]:
        """Revokes POS connector authorization and invalidates session token."""
        self.is_authorized = False
        self.api_key = None
        LOGGER.info("POS Connector authorization revoked.")
        return {
            "success": True,
            "message": "POS & Billing Connector access has been revoked.",
            "status": self.get_status(),
        }

    def process_incoming_bill(
        self,
        bill: POSBillPayload,
        business_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Processes an incoming POS sales invoice, generates provenance text, and indexes in Supabase."""
        if not self.is_authorized:
            raise PermissionError("POS Connector is not authorized. Authorize via the dashboard modal first.")

        timestamp = bill.timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Format bill into official GST POS Tax Receipt text
        lines = [
            "=" * 50,
            f"          {bill.store_name or self.store_name}",
            f"   GSTIN: {bill.gstin or self.gstin} | Retail Invoice",
            f"   Terminal: {bill.terminal_id or self.terminal_id} | Cashier: {bill.cashier_name or 'Counter 1'}",
            "-" * 50,
            f"Invoice No : {bill.bill_number}",
            f"Date & Time: {timestamp}",
            f"Customer   : {bill.customer_name or 'Walk-in'}",
            f"Payment    : {bill.payment_method} {f'(Ref: {bill.payment_ref})' if bill.payment_ref else ''}",
            "-" * 50,
            f"{'Barcode':<14} {'Item Name':<20} {'Qty':<4} {'Price':<7} {'Total':<7}",
            "-" * 50,
        ]

        for it in bill.items:
            short_name = it.item_name[:19]
            lines.append(f"{it.barcode:<14} {short_name:<20} {int(it.quantity):<4} {it.unit_price:<7.2f} {it.line_total:<7.2f}")

        lines.extend([
            "-" * 50,
            f"Taxable Amount:                     INR {bill.subtotal:>10.2f}",
            f"CGST Amount   :                     INR {bill.total_cgst:>10.2f}",
            f"SGST Amount   :                     INR {bill.total_sgst:>10.2f}",
            f"Total GST Tax :                     INR {bill.total_tax:>10.2f}",
            f"Grand Total   :                     INR {bill.grand_total:>10.2f}",
            "=" * 50,
            f"Terms: GST paid under normal composition. Goods once sold cannot be returned.",
        ])

        bill_text = "\n".join(lines)

        # Ingest into Supabase via ingest_file
        with tempfile.TemporaryDirectory(prefix="pos-bill-") as tmpdir:
            file_name = f"{bill.bill_number}.txt"
            bill_path = Path(tmpdir) / file_name
            bill_path.write_text(bill_text, encoding="utf-8")

            evidence = ingest_file(
                file_path=bill_path,
                persist_to_database=True,
                business_id=business_id,
            )

        self.last_synced_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.bills_synced_count += 1
        self.total_sales_amount += bill.grand_total

        bill_summary = {
            "bill_number": bill.bill_number,
            "timestamp": timestamp,
            "item_count": len(bill.items),
            "payment_method": bill.payment_method,
            "grand_total": round(bill.grand_total, 2),
            "tax_total": round(bill.total_tax, 2),
            "database_file_id": evidence.database_file_id or "",
            "file_name": file_name,
        }
        self.recent_bills.append(bill_summary)

        # Tax compliance and HSN check
        tax_anomalies = []
        for it in bill.items:
            expected_cat = BARCODE_CATALOG.get(it.barcode)
            if expected_cat:
                expected_rate = expected_cat.get("gst_rate", 18.0)
                if abs(it.gst_rate - expected_rate) > 0.5:
                    tax_anomalies.append(
                        f"HSN {it.hsn_code} ({it.item_name}): Billed at {it.gst_rate}% GST, expected schedule rate is {expected_rate}%."
                    )

        # Evaluate agentic actions for the newly generated retail bill
        cascade_evidence = evidence_to_cascade_dict(
            evidence_list=[evidence],
            retrieved_chunks=[],
        )
        proposals = evaluate_agentic_actions(
            query=f"Verify POS retail bill {bill.bill_number} tax line items and sales reconciliation",
            audit_decision={
                "answer": f"POS Sales Bill {bill.bill_number} verified for INR {bill.grand_total:.2f}. "
                          f"Payment received via {bill.payment_method}. {len(tax_anomalies)} tax warning(s).",
                "stopped_tier": "layer_1",
            },
            evidence=cascade_evidence,
        )

        return {
            "success": True,
            "bill_number": bill.bill_number,
            "grand_total": bill.grand_total,
            "file_name": file_name,
            "database_file_id": evidence.database_file_id or "",
            "tax_anomalies": tax_anomalies,
            "proposals": [p.model_dump() for p in proposals],
            "message": f"POS Bill {bill.bill_number} successfully ingested and indexed into RAG.",
        }

    def scan_barcode(
        self,
        barcode: str,
        quantity: float = 1.0,
        payment_method: str = "UPI",
        business_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Simulates an interactive checkout barcode scan and immediately generates a POS bill."""
        if not self.is_authorized:
            raise PermissionError("POS Connector is not authorized. Authorize via the dashboard modal first.")

        item_info = BARCODE_CATALOG.get(barcode)
        if not item_info:
            # Fallback for unknown barcode
            item_info = {
                "barcode": barcode,
                "name": f"Retail Item #{barcode[-4:] if len(barcode)>=4 else barcode}",
                "hsn": "9999",
                "mrp": 100.00,
                "taxable_price": 84.75,
                "gst_rate": 18.0,
                "unit": "PCS",
            }

        unit_mrp = item_info["mrp"]
        rate = item_info["gst_rate"]
        taxable_unit = round(unit_mrp / (1 + (rate / 100.0)), 2)
        total_taxable = round(taxable_unit * quantity, 2)
        total_amount = round(unit_mrp * quantity, 2)
        total_tax = round(total_amount - total_taxable, 2)
        half_tax = round(total_tax / 2.0, 2)

        self._bill_sequence += 1
        bill_num = f"POS-2026-{self._bill_sequence}"

        line_item = POSLineItem(
            barcode=item_info["barcode"],
            item_name=item_info["name"],
            hsn_code=item_info["hsn"],
            quantity=quantity,
            unit_price=unit_mrp,
            taxable_amount=total_taxable,
            gst_rate=rate,
            cgst=half_tax,
            sgst=half_tax,
            line_total=total_amount,
        )

        bill_payload = POSBillPayload(
            bill_number=bill_num,
            store_name=self.store_name,
            gstin=self.gstin,
            terminal_id=self.terminal_id,
            cashier_name="Ramesh Sharma",
            customer_name="Walk-in Retail Customer",
            payment_method=payment_method,
            payment_ref=f"TXN_{secrets.token_hex(4).upper()}",
            items=[line_item],
            subtotal=total_taxable,
            total_cgst=half_tax,
            total_sgst=half_tax,
            total_tax=total_tax,
            grand_total=total_amount,
        )

        return self.process_incoming_bill(bill_payload, business_id=business_id)

    def sync_batch_register(
        self,
        business_id: Optional[str] = None,
        batch_size: int = 3,
    ) -> Dict[str, Any]:
        """Simulates pulling a daily sales register batch from the billing software."""
        if not self.is_authorized:
            raise PermissionError("POS Connector is not authorized. Authorize via the dashboard modal first.")

        # Batch simulation: pull multiple items into customer checkout bills
        barcodes = list(BARCODE_CATALOG.keys())
        processed_bills = []
        all_proposals = []

        for i in range(min(batch_size, len(barcodes))):
            bc = barcodes[(self._bill_sequence + i) % len(barcodes)]
            pay_method = "UPI" if i % 2 == 0 else "Cash"
            res = self.scan_barcode(
                barcode=bc,
                quantity=float((i % 3) + 1),
                payment_method=pay_method,
                business_id=business_id,
            )
            processed_bills.append(res)
            all_proposals.extend(res.get("proposals", []))

        # Run reconciliation patrol on recent sales bills vs bank deposits
        invoices_for_patrol = [
            {"invoice_number": b["bill_number"], "amount": b["grand_total"]}
            for b in processed_bills
        ]
        mock_bank_deposits = [
            {"amount": processed_bills[0]["grand_total"], "description": "UPI Merchant Settlement"}
        ]
        patrol_digest = run_reconciliation_patrol(
            invoices=invoices_for_patrol,
            bank_transactions=mock_bank_deposits,
        )

        return {
            "success": True,
            "bills_ingested": len(processed_bills),
            "bills": processed_bills,
            "proposals": all_proposals,
            "reconciliation_digest": patrol_digest.model_dump(),
            "message": f"Batch synchronized {len(processed_bills)} sales bills from POS register. "
                       f"Patrol detected {patrol_digest.anomalies_detected} reconciliation item(s).",
        }


# Global singleton instance
POS_CONNECTOR = POSConnector()

