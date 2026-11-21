import csv
import hashlib

import pytest

from backend.schemas.evidence import Evidence
from tests import evaluate_image_dataset
from tests.evaluate_image_dataset import normalize


def test_image_evaluation_normalizes_dates_and_amounts():
    assert normalize("date", "29-Jun-2025") == "2025-06-29"
    assert normalize("date", "04-12-2021") == "2021-12-04"
    assert normalize("amount", "1,18,000.00") == normalize("amount", "118000.00")
    assert normalize("amount", "1.0") == normalize("amount", "1.00")


def test_image_evaluation_does_not_match_missing_or_invalid_values():
    assert normalize("date", "Due 15/05/2024 30/04/2024") != "2024-04-30"
    assert normalize("amount", None) is None
    assert normalize("invoice_number", "wrong") != "DOM2425-1"


def test_image_evaluation_counts_parser_failures_in_denominator(tmp_path, monkeypatch, capsys):
    rows = []
    for name in ("good.png", "failed.png"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        rows.append({
            "file_name": name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "invoice_number": "INV-1",
            "date": "2025-01-12",
            "amount": "100.00",
        })
    labels = tmp_path / "labels.csv"
    with labels.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    monkeypatch.setattr(evaluate_image_dataset, "LABELS", labels)
    monkeypatch.setattr(evaluate_image_dataset, "IMAGE_DIR", tmp_path)

    def fake_parse(path):
        if path.name == "failed.png":
            return Evidence(file_name=path.name, processing_status="failed", error="OCR failed")
        return Evidence(
            file_name=path.name, processing_status="success", invoice_number="INV-1",
            date="12/01/2025", amount="99.00",
        )

    monkeypatch.setattr(evaluate_image_dataset, "parse_image", fake_parse)
    assert evaluate_image_dataset.main() == 1
    output = capsys.readouterr().out
    assert "parser failures: 1" in output
    assert "invoice_number: 1/2" in output
    assert "amount: 0/2" in output
    assert "OCR failed" in output


def test_image_evaluation_rejects_changed_image(tmp_path, monkeypatch):
    (tmp_path / "sample.png").write_bytes(b"changed")
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "file_name,sha256,invoice_number,date,amount\n"
        "sample.png,incorrect,INV-1,2025-01-12,100.00\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(evaluate_image_dataset, "LABELS", labels)
    monkeypatch.setattr(evaluate_image_dataset, "IMAGE_DIR", tmp_path)
    with pytest.raises(ValueError, match="changed since annotation"):
        evaluate_image_dataset.main()
