"""Utility functions for human-readable LLM cascade answers.

Transforms LLM answers into well-structured, human-readable audit responses:
- Cleans and eliminates raw markdown hashes (#, ##, ###) and asterisks (*, **).
- Structures output with clean headings and subheadings.
- Normalizes bullet lists to clean, consistent hyphens (-).
- Organizes dense unformatted strings into structured audit sections:
    Summary
    Key Findings
    Discrepancy Analysis
    Recommendations
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

_SECTION_CUES: List[Tuple[str, List[str]]] = [
    (
        "Summary",
        [
            "summary",
            "overview",
            "invoice total",
            "total is",
            "audited your request",
            "audit context",
            "financial records",
            "all records comply",
        ],
    ),
    (
        "Key Findings",
        [
            "key finding",
            "finding",
            "tax",
            "gst",
            "cgst",
            "sgst",
            "igst",
            "base amount",
            "subtotal",
            "supplier",
            "total payable",
            "rate",
            "line item",
            "provenance",
        ],
    ),
    (
        "Discrepancy Analysis",
        [
            "discrepan",
            "mismatch",
            "gap",
            "missing",
            "unvouched",
            "error",
            "difference",
            "unbacked",
            "fail",
            "not sum",
            "unmatched",
            "cannot be determined",
            "contradiction",
        ],
    ),
    (
        "Recommendations",
        [
            "recommend",
            "action",
            "verify",
            "clearance",
            "advice",
            "compliance",
            "should",
            "ensure",
            "request",
            "next steps",
        ],
    ),
]

_KNOWN_SECTION_HEADERS = {
    "summary",
    "key findings",
    "findings",
    "audit findings",
    "discrepancy analysis",
    "discrepancy breakdown",
    "tax breakdown",
    "audit determination",
    "audit determination & findings",
    "recommendations",
    "recommendation",
    "conclusion",
    "audit review status",
    "items requiring audit attention",
    "mathematical derivation",
    "verification",
    "gaps",
    "duplicate payments check complete",
    "duplicate payments check",
    "expense audit complete",
    "receipt audit review",
    "financial records summary",
    "supporting citations",
    "subtotal breakdown",
    "line item details",
}


def clean_markdown_artifacts(text: str) -> str:
    """Strip raw markdown hashes, asterisks, and decorative dividers from text."""
    if not text:
        return ""

    # Normalize bullet markers (asterisks, bullet symbols) to clean hyphens
    text = re.sub(r"^[ \t]*[*•][ \t]+", "- ", text, flags=re.MULTILINE)

    # Strip bold / italics markdown asterisks: **text** -> text, *text* -> text
    text = re.sub(r"\*{1,3}([^*\n]+?)\*{1,3}", r"\1", text)
    # Remove any remaining stray asterisks anywhere in the text
    text = text.replace("*", "")

    # Strip markdown heading hashes from line starts: ### Heading -> Heading
    text = re.sub(r"^[ \t]*#{1,6}[ \t]*", "", text, flags=re.MULTILINE)
    # Remove any remaining stray hashes
    text = text.replace("#", "")

    # Strip decorative underline dividers like ---, ===, ___
    text = re.sub(r"^[ \t]*[-=_]{3,}[ \t]*$", "", text, flags=re.MULTILINE)

    return text


def _capitalize_sentence(s: str) -> str:
    s = s.strip()
    if not s:
        return ""
    return s[0].upper() + s[1:]


def _is_heading_or_subheading(line: str, was_md_heading: bool = False) -> bool:
    """Determine whether a line serves as a section heading or subheading."""
    if was_md_heading:
        return True

    trimmed = line.strip()
    if not trimmed:
        return False

    clean = trimmed.rstrip(":").strip()
    clean_lower = clean.lower()

    if clean_lower in _KNOWN_SECTION_HEADERS:
        return True

    if any(clean_lower.startswith(h + " ") or clean_lower.startswith(h + " -") for h in _KNOWN_SECTION_HEADERS):
        return True

    # Numbered headings like "1. Base Price Breakdown" or short lines ending with colon
    if re.match(r"^\d+\.\s+[A-Za-z0-9 ]+$", clean) and len(clean.split()) <= 8:
        return True

    if trimmed.endswith(":") and len(trimmed.split()) <= 6 and not trimmed.startswith("-"):
        return True

    return False


def format_answer(raw: str) -> str:
    """Return a human-readable, beautifully structured version of raw LLM answer.

    Ensures:
    - No markdown asterisks (*) or hashes (#) appear.
    - Headings and subheadings are properly formatted with clean spacing.
    - Bullet points are uniform (-).
    - Tolerant and never raises exceptions.
    """
    if not raw or not str(raw).strip():
        return ""

    raw_text = str(raw).strip()

    # Pre-detect which lines were markdown headings
    raw_lines = raw_text.splitlines()
    md_heading_indices = {
        i for i, line in enumerate(raw_lines) if re.match(r"^[ \t]*#{1,6}[ \t]+", line)
    }

    # Clean markdown artifacts from the entire text
    cleaned = clean_markdown_artifacts(raw_text).strip()
    cleaned_lines = [l.strip() for l in cleaned.splitlines()]
    non_empty = [l for l in cleaned_lines if l]

    if not non_empty:
        return ""

    # Check if text already possesses multiline structure or headings
    has_structure = (
        len(non_empty) >= 3
        or bool(md_heading_indices)
        or any(
            l.startswith("- ")
            or l.endswith(":")
            or _is_heading_or_subheading(l)
            for l in non_empty
        )
    )

    # If it is a single dense unformatted paragraph, structure it into audit sections
    if not has_structure and len(non_empty) <= 2:
        flat_text = " ".join(non_empty)
        sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+", flat_text)
            if s.strip()
        ]
        sections: Dict[str, List[str]] = {}
        for s in sentences:
            s_cap = _capitalize_sentence(s)
            low = s_cap.lower()
            matched_section = "Summary"
            for section_name, cues in _SECTION_CUES:
                if any(cue in low for cue in cues):
                    matched_section = section_name
                    break
            sections.setdefault(matched_section, []).append(s_cap)

        out_blocks: List[str] = []
        for heading, s_list in sections.items():
            content = "\n".join(f"- {item}" if len(s_list) > 1 else item for item in s_list)
            out_blocks.append(f"{heading}\n{content}")
        return "\n\n".join(out_blocks)

    # Process structured lines
    formatted_lines: List[str] = []
    for idx, line in enumerate(cleaned_lines):
        if not line:
            if formatted_lines and formatted_lines[-1] != "":
                formatted_lines.append("")
            continue

        was_heading = idx in md_heading_indices
        if _is_heading_or_subheading(line, was_md_heading=was_heading):
            if formatted_lines and formatted_lines[-1] != "":
                formatted_lines.append("")
            clean_title = line.rstrip(":").strip()
            # Standardize capitalization
            if clean_title.islower() or (clean_title.isupper() and len(clean_title) > 4):
                clean_title = clean_title.title()
            formatted_lines.append(clean_title)
        elif line.startswith("- "):
            item_text = line[2:].strip()
            if item_text:
                item_text = _capitalize_sentence(item_text)
                formatted_lines.append(f"- {item_text}")
        elif re.match(r"^\d+\.\s+", line):
            # Numbered list item
            num_match = re.match(r"^(\d+\.)\s+(.*)", line)
            if num_match:
                prefix, rest = num_match.group(1), num_match.group(2).strip()
                formatted_lines.append(f"{prefix} {_capitalize_sentence(rest)}")
            else:
                formatted_lines.append(_capitalize_sentence(line))
        else:
            formatted_lines.append(_capitalize_sentence(line))

    result = "\n".join(formatted_lines)
    return re.sub(r"\n{3,}", "\n\n", result).strip()
