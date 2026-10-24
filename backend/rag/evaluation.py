"""Ground-truth metrics for evaluating RAG retrieval quality."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievalCase:
    """A query and the chunk identifiers that are considered relevant."""

    query: str
    relevant_ids: frozenset[str]

    def __init__(self, query: str, relevant_ids: Iterable[str]) -> None:
        if not query.strip():
            raise ValueError("Evaluation query cannot be empty")
        ids = frozenset(str(item) for item in relevant_ids)
        if not ids:
            raise ValueError("Each evaluation query needs at least one relevant id")
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "relevant_ids", ids)


@dataclass(frozen=True)
class RetrievalMetrics:
    """Aggregate ranking metrics over a set of retrieval cases."""

    query_count: int
    hit_rate: float
    precision_at_k: float
    recall_at_k: float
    mean_reciprocal_rank: float


@dataclass(frozen=True)
class GroundingMetrics:
    """Claim-level support measured against the supplied retrieved context."""

    claim_count: int
    supported_claims: int
    support_rate: float


@dataclass(frozen=True)
class ReconciliationMetrics:
    """Agreement metrics for records sharing a business key."""

    group_count: int
    consistent_groups: int
    consistency_rate: float
    conflicting_groups: int


def evaluate_grounding(
    claims: Iterable[str],
    contexts: Iterable[str],
) -> GroundingMetrics:
    """Measure how many declared answer claims occur in retrieved context.

    This is a deterministic, conservative proxy for grounding. It does not
    judge whether a claim is true beyond the supplied context and should be
    reported separately from an LLM judge score.
    """
    claim_list = [claim.strip() for claim in claims if claim.strip()]
    context = " ".join(item.strip() for item in contexts if item.strip()).casefold()
    supported = sum(claim.casefold() in context for claim in claim_list)
    return GroundingMetrics(
        claim_count=len(claim_list),
        supported_claims=supported,
        support_rate=supported / len(claim_list) if claim_list else 0.0,
    )


def evaluate_reconciliation(
    records: Iterable[Mapping[str, Any]],
    *,
    key: str,
    fields: Iterable[str],
) -> ReconciliationMetrics:
    """Check whether repeated records agree on selected fields.

    Missing fields are ignored; a group is conflicting when at least one
    selected field has more than one non-empty value.
    """
    field_list = list(fields)
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        group_key = str(record.get(key, "")).strip()
        if group_key:
            groups.setdefault(group_key, []).append(record)

    conflicting = 0
    for group in groups.values():
        has_conflict = any(
            len(
                {
                    str(record.get(field)).strip().casefold()
                    for record in group
                    if record.get(field) not in (None, "")
                }
            )
            > 1
            for field in field_list
        )
        conflicting += has_conflict

    group_count = len(groups)
    return ReconciliationMetrics(
        group_count=group_count,
        consistent_groups=group_count - conflicting,
        consistency_rate=(group_count - conflicting) / group_count if group_count else 0.0,
        conflicting_groups=conflicting,
    )


def evaluate_retrieval(
    cases: Iterable[RetrievalCase],
    retrieve_fn: Callable[[str], Iterable[Mapping[str, Any]]],
    *,
    k: int = 5,
    id_key: str = "id",
) -> RetrievalMetrics:
    """Run labelled queries and calculate standard retrieval metrics.

    ``retrieve_fn`` should return results in descending relevance order. The
    evaluator intentionally does not inspect answer text: RAG retrieval
    quality must be measured separately from LLM answer correctness.
    """
    if k < 1:
        raise ValueError("k must be positive")

    case_list = list(cases)
    if not case_list:
        raise ValueError("At least one evaluation case is required")

    hits = 0
    precision_total = 0.0
    recall_total = 0.0
    reciprocal_rank_total = 0.0

    for case in case_list:
        results = list(retrieve_fn(case.query))[:k]
        result_ids = [str(result[id_key]) for result in results if id_key in result]
        relevant_results = set(result_ids) & case.relevant_ids
        hits += bool(relevant_results)
        precision_total += len(relevant_results) / k
        recall_total += len(relevant_results) / len(case.relevant_ids)

        for rank, result_id in enumerate(result_ids, start=1):
            if result_id in case.relevant_ids:
                reciprocal_rank_total += 1 / rank
                break

    query_count = len(case_list)
    return RetrievalMetrics(
        query_count=query_count,
        hit_rate=hits / query_count,
        precision_at_k=precision_total / query_count,
        recall_at_k=recall_total / query_count,
        mean_reciprocal_rank=reciprocal_rank_total / query_count,
    )
