from backend.rag.evaluation import (
    RetrievalCase,
    evaluate_grounding,
    evaluate_reconciliation,
    evaluate_retrieval,
)
from backend.rag.retriever import retrieve


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeSupabase:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return self

    def execute(self):
        return FakeResponse(self.results)


class HybridFakeSupabase(FakeSupabase):
    def table(self, _name):
        return self

    def select(self, _columns):
        return self

    def eq(self, _column, _value):
        return self

    def ilike(self, _column, _value):
        return self

    def limit(self, _count):
        return self


def test_retriever_sends_query_embedding_and_scope(monkeypatch):
    client = FakeSupabase([{"id": "chunk-1", "similarity": 0.92}])
    monkeypatch.setattr("backend.rag.retriever.embed_texts", lambda texts: [[0.1, 0.2]])

    results = retrieve(
        "What was the invoice total?",
        "business-1",
        match_count=3,
        min_similarity=0.7,
        client=client,
    )

    assert results == [{"id": "chunk-1", "similarity": 0.92}]
    assert client.calls == [
        (
            "match_document_chunks",
            {
                "query_embedding": [0.1, 0.2],
                "match_business_id": "business-1",
                "match_count": 3,
                "min_similarity": 0.7,
            },
        )
    ]


def test_retriever_prioritizes_exact_financial_identifier(monkeypatch):
    client = HybridFakeSupabase([{"chunk_id": "chunk-invoice", "content": "invoice_id: INV-08-0007"}])
    client.results = [{"chunk_id": "chunk-invoice", "content": "invoice_id: INV-08-0007"}]
    monkeypatch.setattr("backend.rag.retriever.embed_texts", lambda texts: [[0.1, 0.2]])

    results = retrieve(
        "Which document contains invoice INV-08-0007?",
        "business-1",
        match_count=2,
        min_similarity=0.0,
        client=client,
    )

    assert results[0]["chunk_id"] == "chunk-invoice"
    assert results[0]["match_type"] == "exact"


def test_retriever_rejects_empty_query_and_business():
    for query, business_id in [("", "business-1"), ("   ", "business-1"), ("total", "")]:
        try:
            retrieve(query, business_id, client=FakeSupabase([]))
        except ValueError:
            pass
        else:
            raise AssertionError("retrieve should reject empty input")


def test_evaluation_metrics_measure_hit_rank_and_coverage():
    cases = [
        RetrievalCase("invoice total", ["a"]),
        RetrievalCase("tax rate", ["b", "c"]),
        RetrievalCase("unknown", ["missing"]),
    ]
    ranked_results = {
        "invoice total": [{"id": "noise"}, {"id": "a"}],
        "tax rate": [{"id": "b"}, {"id": "noise"}],
        "unknown": [{"id": "noise"}],
    }

    metrics = evaluate_retrieval(
        cases,
        lambda query: ranked_results[query],
        k=2,
    )

    assert metrics.query_count == 3
    assert metrics.hit_rate == 2 / 3
    assert metrics.precision_at_k == (1 / 2 + 1 / 2 + 0) / 3
    assert metrics.recall_at_k == (1 + 1 / 2 + 0) / 3
    assert metrics.mean_reciprocal_rank == (1 / 2 + 1 + 0) / 3


def test_evaluation_uses_only_top_k_results():
    metrics = evaluate_retrieval(
        [RetrievalCase("q", ["late"])],
        lambda _query: [{"id": "noise"}, {"id": "late"}],
        k=1,
    )

    assert metrics.hit_rate == 0
    assert metrics.recall_at_k == 0


def test_evaluation_rejects_invalid_cases():
    try:
        RetrievalCase("", ["chunk"])
    except ValueError:
        pass
    else:
        raise AssertionError("empty query should be rejected")

    try:
        evaluate_retrieval([], lambda _query: [], k=0)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid evaluation configuration should be rejected")


def test_grounding_counts_claims_supported_by_context():
    metrics = evaluate_grounding(
        ["invoice total is 42.50", "GST rate is 18%"],
        ["Invoice total is 42.50", "seller: Acme"],
    )

    assert metrics.claim_count == 2
    assert metrics.supported_claims == 1
    assert metrics.support_rate == 0.5


def test_reconciliation_detects_conflicting_duplicate_documents():
    metrics = evaluate_reconciliation(
        [
            {"invoice_id": "INV-1", "invoice_total": "100.00"},
            {"invoice_id": "INV-1", "invoice_total": "100.00"},
            {"invoice_id": "INV-2", "invoice_total": "80.00"},
            {"invoice_id": "INV-2", "invoice_total": "90.00"},
        ],
        key="invoice_id",
        fields=["invoice_total"],
    )

    assert metrics.group_count == 2
    assert metrics.consistent_groups == 1
    assert metrics.conflicting_groups == 1
    assert metrics.consistency_rate == 0.5
