"""Utility functions for human‑readable LLM cascade answers.

The LLM sometimes returns a compact string like:
    "the invoice total is 59000 why tax appears? the cgst and sgst are both 4500. overall compliance looks ok but missing receipt for line 1"
We want to transform that into a structured, easy‑to‑read response with sections:
    Summary
    Key Findings
    Gaps
    Recommendations

The formatter performs a light‑weight heuristic‑based transformation:
- Normalises whitespace and ensures a trailing period.
- Detects bullet‑style fragments (lines starting with "-" or "* ") and keeps them as list items.
- Looks for cue‑words (summary, findings, gap, recommendation) to insert section headings.
- Capitalises the first word of each sentence.
"""

import re
from typing import List

_SECTION_ORDER = ["summary", "key findings", "findings", "gaps", "gap", "recommendations", "recommendation"]

def _capitalize_sentence(sentence: str) -> str:
    sentence = sentence.strip()
    if not sentence:
        return ""
    return sentence[0].upper() + sentence[1:]

def _split_into_sentences(text: str) -> List[str]:
    # Very simple split on punctuation followed by a space or end‑of‑string.
    # Retain the punctuation.
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]

def format_answer(raw: str) -> str:
    """Return a human‑readable version of *raw* LLM answer.

    The function is deliberately tolerant – it never raises – and always returns a string.
    """
    if not raw:
        return ""

    # Normalise whitespace first.
    text = re.sub(r"\s+", " ", raw).strip()
    # Ensure trailing period.
    if not text.endswith('.'):
        text += '.'

    # Detect pre‑existing bullet list.
    if re.search(r"^[-*] ", text, flags=re.MULTILINE):
        # Preserve list items, just ensure each line ends with a period.
        lines = []
        for line in text.splitlines():
            line = line.strip()
            # Normalize bullet marker: replace leading asterisk with hyphen
            if line.startswith('* '):
                line = '- ' + line[2:]
            if not line.endswith('.'):
                line += '.'
            lines.append(line)
        return "\n".join(lines)

    # Split into sentences.
    sentences = _split_into_sentences(text)
    # Capitalise each sentence.
    sentences = [_capitalize_sentence(s) for s in sentences]

    # Try to infer sections based on cue words.
    sections = {}
    current_key = None
    for s in sentences:
        lowered = s.lower()
        matched = None
        for cue in _SECTION_ORDER:
            if cue in lowered:
                matched = cue
                break
        if matched:
            # Start a new section.
            current_key = matched.title() if matched not in ["gap", "gaps"] else "Gaps"
            # Normalise generic names.
            if current_key.startswith("Key "):
                current_key = "Key Findings"
            elif "recommend" in current_key.lower():
                current_key = "Recommendations"
            sections.setdefault(current_key, []).append(s)
        else:
            # No cue – place in the most recent section, else default to Summary.
            if not current_key:
                current_key = "Summary"
            sections.setdefault(current_key, []).append(s)

    # Build output with headings.
    out_lines: List[str] = []
    for heading, lines in sections.items():
        out_lines.append(f"{heading}")
        out_lines.append("-" * len(heading))
        for line in lines:
            out_lines.append(line)
        out_lines.append("")
    return "\n".join(out_lines).strip()
