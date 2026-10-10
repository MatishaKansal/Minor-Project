"""Parser package with lazy imports so each parser only needs its own dependencies."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "parse_bank_csv_file": ("backend.parsers.bank_csv_parser", "parse_bank_csv_file"),
    "parse_bank_csv_text": ("backend.parsers.bank_csv_parser", "parse_bank_csv_text"),
    "parse_excel_file": ("backend.parsers.excel_parser", "parse_excel_file"),
    "parse_image": ("backend.parsers.image_parser", "parse_image"),
    "parse_pdf": ("backend.parsers.pdf_parser", "parse_pdf"),
    "parse_text": ("backend.parsers.text_parser", "parse_text"),
    "parse_txt_file": ("backend.parsers.text_parser", "parse_txt_file"),
    "parse_voice_bytes": ("backend.parsers.voice_parser", "parse_voice_bytes"),
    "parse_voice_file": ("backend.parsers.voice_parser", "parse_voice_file"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Load a parser only when requested, avoiding unrelated optional dependencies."""
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    return getattr(import_module(module_name), attribute_name)
