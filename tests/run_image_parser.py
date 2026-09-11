"""Run the image parser against every supported image in the input directory."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.parsers.image_parser import parse_image


IMAGE_DIRECTORY = PROJECT_ROOT / "data" / "input" / "images"
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
SEPARATOR = "-" * 50


def main() -> int:
    image_paths = sorted(
        (path for path in IMAGE_DIRECTORY.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS),
        key=lambda path: path.name.lower(),
    ) if IMAGE_DIRECTORY.is_dir() else []

    successful = 0
    failed = 0

    for image_path in image_paths:
        result = parse_image(image_path)
        if result.processing_status == "success":
            successful += 1
        else:
            failed += 1

        print(SEPARATOR)
        print(f"FILE: {image_path.name}")
        print(f"STATUS: {result.processing_status}")
        print(f"OCR CONFIDENCE: {result.confidence_score}")
        print(f"DATE: {result.date}")
        print(f"AMOUNT: {result.amount}")
        print(f"CURRENCY: {result.currency}")
        print(f"PARTY: {result.party_name}")
        print(f"INVOICE NUMBER: {result.invoice_number}")
        if result.error:
            print(f"ERROR: {result.error}")
        if result.processing_status == "success" and result.provenance:
            print("PROVENANCE:")
            for item in result.provenance:
                print(f"- field: {item.field_name}")
                print(f"  value: {item.value}")
                print(f"  source: {item.source_text}")
                print(f"  line: {item.line_number}")
                print(f"  method: {item.extraction_method}")
                print(f"  confidence: {item.confidence}")
        print(SEPARATOR)
        print("EXTRACTED OCR TEXT:")
        print(result.extracted_text or "")

    print(SEPARATOR)
    print(f"Total images: {len(image_paths)}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
