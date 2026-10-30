"""Cascade Decision Maker service integrating multimodal evidence with the 4-tier LLM Cascade."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import ollama

from backend.cascade.config import DEFAULT_JUDGE_MODEL, OLLAMA_HOST, PHASE4_MODEL_CONFIG
from backend.cascade.gate import ConservativeEscalationGate
from backend.cascade.judge import call_runtime_judge, combine_verifications
from backend.cascade.router import Phase4CascadeRouter
from backend.cascade.runner import call_tier_model_detailed
from backend.cascade.signals import extract_signals, signal_vector
from backend.cascade.verifier import verify_runtime
from backend.schemas.evidence import Evidence

LOGGER = logging.getLogger(__name__)

TIER_ORDER: List[str] = ["layer_1", "layer_2", "layer_3", "layer_4"]


def evidence_to_cascade_dict(
    evidence_list: List[Evidence],
    retrieved_chunks: Optional[List[Dict[str, Any]]] = None,
    business_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Transform live multimodal Evidence models and retrieved RAG chunks into the
    structured evidence format expected by the LLM-Cascade decision maker.
    """
    invoices: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []
    payments: List[Dict[str, Any]] = []
    provenance: List[Dict[str, Any]] = []
    chunks: List[Dict[str, Any]] = []

    for ev in evidence_list:
        inv_data = {
            "invoice_number": ev.invoice_number,
            "invoice_date": ev.date,
            "due_date": ev.due_date,
            "invoice_type": ev.invoice_type,
            "seller_name": ev.seller_name or ev.party_name,
            "seller_gstin": ev.seller_gstin,
            "buyer_name": ev.buyer_name,
            "buyer_gstin": ev.buyer_gstin,
            "place_of_supply": ev.place_of_supply,
            "reverse_charge": ev.reverse_charge,
            "subtotal": float(ev.subtotal) if ev.subtotal and ev.subtotal.replace('.', '', 1).isdigit() else ev.subtotal,
            "discount_amount": float(ev.discount_amount) if ev.discount_amount and ev.discount_amount.replace('.', '', 1).isdigit() else ev.discount_amount,
            "cgst_total": float(ev.cgst_total) if ev.cgst_total and ev.cgst_total.replace('.', '', 1).isdigit() else ev.cgst_total,
            "sgst_total": float(ev.sgst_total) if ev.sgst_total and ev.sgst_total.replace('.', '', 1).isdigit() else ev.sgst_total,
            "igst_total": float(ev.igst_total) if ev.igst_total and ev.igst_total.replace('.', '', 1).isdigit() else ev.igst_total,
            "cess_total": float(ev.cess_total) if ev.cess_total and ev.cess_total.replace('.', '', 1).isdigit() else ev.cess_total,
            "round_off": float(ev.round_off) if ev.round_off and ev.round_off.replace('.', '', 1).isdigit() else ev.round_off,
            "invoice_total": float(ev.invoice_total) if ev.invoice_total and ev.invoice_total.replace('.', '', 1).isdigit() else (float(ev.amount) if ev.amount and ev.amount.replace('.', '', 1).isdigit() else ev.amount),
            "currency": ev.currency or "INR",
            "file_name": ev.file_name,
            "source_type": ev.source_type,
        }
        invoices.append(inv_data)

        for item in ev.items or []:
            item_dict = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            item_dict["file_name"] = ev.file_name
            items.append(item_dict)

        for pmt in ev.payments or []:
            pmt_dict = dict(pmt)
            pmt_dict["file_name"] = ev.file_name
            payments.append(pmt_dict)

        for prov in ev.provenance or []:
            prov_dict = prov.model_dump() if hasattr(prov, "model_dump") else dict(prov)
            prov_dict["provenance_id"] = f"prov_{prov_dict.get('field_name', 'field')}_{len(provenance)+1}"
            provenance.append(prov_dict)

    if retrieved_chunks:
        for idx, ch in enumerate(retrieved_chunks):
            chunks.append({
                "chunk_id": ch.get("chunk_id") or ch.get("id") or f"chunk_{idx+1}",
                "content": ch.get("content", ""),
                "similarity": ch.get("similarity"),
                "file_name": ch.get("file_name"),
            })

    primary_invoice = invoices[0] if invoices else {}

    return {
        "business": business_metadata or {
            "business_name": primary_invoice.get("buyer_name") or primary_invoice.get("seller_name") or "Primary Entity",
            "gstin": primary_invoice.get("buyer_gstin") or primary_invoice.get("seller_gstin"),
        },
        "invoice": primary_invoice,
        "invoices": invoices,
        "items": items,
        "payments": payments,
        "provenance": provenance,
        "document_chunks": chunks,
    }


class CascadeDecisionMaker:
    """Production Cascade Decision Maker executing live LLM cascading with learned stopping."""

    def __init__(self, ollama_host: str = OLLAMA_HOST):
        self.ollama_host = ollama_host
        self.client: Optional[ollama.Client] = None
        self.router = Phase4CascadeRouter()
        self.gate = ConservativeEscalationGate()
        self._init_client()

    def _init_client(self) -> None:
        try:
            self.client = ollama.Client(host=self.ollama_host)
            # Quick connectivity check
            self.client.list()
            LOGGER.info("Connected to Ollama at %s", self.ollama_host)
        except Exception as exc:
            LOGGER.warning("Could not connect to Ollama at %s (%s). Will fall back to mock if needed.", self.ollama_host, exc)
            self.client = None

    def execute_decision(
        self,
        query: str,
        evidence: Dict[str, Any],
        force_mock: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute the 4-tier LLM cascade on the provided query and evidence.
        Dynamically stops at the lowest sufficient tier using the learned router.
        """
        problem = {"query": query, "evidence": evidence}
        tier_evaluations: Dict[str, Any] = {}
        prev_response: Optional[str] = None
        prev_feedback: Optional[str] = None
        stopped_tier: str = "layer_4"
        stop_probability: Optional[float] = None
        is_live_decision = self.client is not None and not force_mock

        for tier in TIER_ORDER:
            model_info = PHASE4_MODEL_CONFIG[tier]
            model_name = model_info["model"]

            # Generate tier response
            ans_text, gen_stats, is_mock = call_tier_model_detailed(
                problem=problem,
                layer_key=tier,
                previous_response=prev_response,
                feedback=prev_feedback,
                client=self.client if is_live_decision else None,
                force_mock=not is_live_decision,
            )

            # Runtime verifier (zero ground-truth leakage)
            runtime_ver = verify_runtime(
                answer_text=ans_text,
                evidence=evidence,
                query=query,
            )

            # Runtime judge
            runtime_judge = call_runtime_judge(
                query=query,
                answer=ans_text,
                evidence=evidence,
                judge_model=DEFAULT_JUDGE_MODEL,
                client=self.client if is_live_decision else None,
                allow_mock_fallback=True,
                runtime_verifier_result=runtime_ver,
            )

            runtime_comb = combine_verifications(runtime_ver, runtime_judge)

            # Extract feature signals for the router
            signals = extract_signals(
                response=ans_text,
                problem=problem,
                previous_response=prev_response,
            )

            tier_evaluations[tier] = {
                "model": model_name,
                "response": ans_text,
                "tokens": gen_stats.get("total_tokens", 0),
                "latency_ms": gen_stats.get("wall_latency_ms", 0.0),
                "is_mock": is_mock,
                "runtime_verifier_pass": runtime_ver["runtime_pass"],
                "runtime_verifier_reasons": runtime_ver.get("reasons", []),
                "runtime_judge_verdict": runtime_judge.get("overall_verdict"),
                "runtime_overall_pass": runtime_comb["overall_pass"],
                "signals": signals,
            }

            # Check if router decides to stop here
            if tier != "layer_4":
                should_stop, prob = self.router.should_stop(
                    layer_key=tier,
                    signals=signals,
                    runtime_verifier_result=runtime_ver,
                )
                if should_stop:
                    stopped_tier = tier
                    stop_probability = prob
                    LOGGER.info("Cascade Router stopped at %s (prob=%.3f)", tier, prob)
                    break
            else:
                stopped_tier = "layer_4"
                stop_probability = 1.0
                break

            prev_response = ans_text
            prev_feedback = "; ".join(runtime_ver.get("reasons", [])) or runtime_judge.get("reasoning", "")

        chosen = tier_evaluations[stopped_tier]

        return {
            "query": query,
            "answer": chosen["response"],
            "stopped_tier": stopped_tier,
            "stopped_model": chosen["model"],
            "stop_probability": stop_probability,
            "is_mock": chosen["is_mock"],
            "runtime_pass": chosen["runtime_overall_pass"],
            "tokens_consumed": chosen["tokens"],
            "latency_ms": chosen["latency_ms"],
            "tier_history": [
                {
                    "tier": t,
                    "model": data["model"],
                    "runtime_pass": data["runtime_overall_pass"],
                    "latency_ms": data["latency_ms"],
                    "is_mock": data["is_mock"],
                }
                for t, data in tier_evaluations.items()
            ],
        }
