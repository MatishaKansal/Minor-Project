"""Tests for LLM Answer Formatting & Markdown Artifact Cleansing."""

import pytest
from backend.cascade.formatting import clean_markdown_artifacts, format_answer


def test_clean_markdown_artifacts_removes_hashes_and_asterisks():
    raw = "### Summary\nThis is **bold** and *italic* text with #gst-tag."
    cleaned = clean_markdown_artifacts(raw)
    assert "#" not in cleaned
    assert "*" not in cleaned
    assert "Summary" in cleaned
    assert "bold and italic text with gst-tag" in cleaned


def test_clean_markdown_artifacts_converts_bullet_points():
    raw = "* Line 1\n* Line 2\n• Line 3"
    cleaned = clean_markdown_artifacts(raw)
    assert "*" not in cleaned
    assert "•" not in cleaned
    assert cleaned.splitlines() == ["- Line 1", "- Line 2", "- Line 3"]


def test_format_answer_with_structured_markdown():
    raw = """### Summary
The invoice **INV-2026-101** total is ₹14,160. There is an expense discrepancy.

### Key Findings
* **Base Amount:** ₹12,000 matches PO line items.
* **Taxes Applied:** 18% GST (#prov_tax_1) adds ₹2,160.

#### Subheading - Tax Analysis
* CGST 9% is ₹1,080.
* SGST 9% is ₹1,080.

### Recommendations
1. approve updated purchase order.
2. clear invoice payment."""

    formatted = format_answer(raw)

    # 1. Zero hashes and zero asterisks
    assert "#" not in formatted
    assert "*" not in formatted

    # 2. Proper headings and subheadings present
    assert "Summary" in formatted
    assert "Key Findings" in formatted
    assert "Subheading - Tax Analysis" in formatted
    assert "Recommendations" in formatted

    # 3. Clean list formatting
    assert "- Base Amount: ₹12,000 matches PO line items." in formatted
    assert "- Taxes Applied: 18% GST (prov_tax_1) adds ₹2,160." in formatted
    assert "1. Approve updated purchase order." in formatted
    assert "2. Clear invoice payment." in formatted


def test_format_answer_with_dense_unformatted_string():
    raw = "the invoice total is 59000 why tax appears? the cgst and sgst are both 4500. missing receipt for line 1. overall compliance looks ok recommend verification."
    formatted = format_answer(raw)

    assert "#" not in formatted
    assert "*" not in formatted
    assert "Summary" in formatted
    assert "Key Findings" in formatted
    assert "Discrepancy Analysis" in formatted
    assert "Recommendations" in formatted


def test_format_answer_handles_empty_and_whitespace():
    assert format_answer("") == ""
    assert format_answer("   \n\t  ") == ""
    assert format_answer(None) == ""
