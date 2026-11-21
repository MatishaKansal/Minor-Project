"""Evaluate selected images against visually transcribed labels."""

import csv
import hashlib
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.parsers.image_parser import parse_image

IMAGE_DIR = PROJECT_ROOT / "dataset" / "img"
LABELS = IMAGE_DIR / "image_ground_truth.csv"
FIELDS = ("invoice_number", "date", "amount")


def normalize(field: str, value: str | None) -> str | Decimal | None:
    if value is None:
        return None
    value = value.strip()
    if field == "amount":
        try:
            amount = Decimal(value.replace(",", ""))
            return amount if amount.is_finite() else value
        except InvalidOperation:
            return value
    if field == "date":
        for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y"):
            try:
                return datetime.strptime(value, date_format).date().isoformat()
            except ValueError:
                continue
    return value


def main() -> int:
    with LABELS.open(encoding="utf-8", newline="") as handle:
        labels = list(csv.DictReader(handle))
    if not labels:
        raise ValueError(f"No image labels in {LABELS}")

    counts = {field: 0 for field in FIELDS}
    seen = set()
    failures = 0
    for row in labels:
        name = row["file_name"]
        if not name or Path(name).name != name or name in seen:
            raise ValueError(f"Invalid or duplicate image filename: {name!r}")
        seen.add(name)
        path = IMAGE_DIR / name
        if not path.is_file():
            raise FileNotFoundError(f"Annotated image is missing: {path}")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != row["sha256"]:
            raise ValueError(f"Image changed since annotation: {path}")
        if any(not row[field] for field in FIELDS):
            raise ValueError(f"Missing field label for {name}")

        result = parse_image(path)
        if result.processing_status != "success":
            failures += 1
            print(f"{name}: parser failed: {result.error}")
            continue
        for field in FIELDS:
            expected = normalize(field, row[field])
            actual_value = getattr(result, field)
            actual = normalize(field, actual_value)
            if actual == expected:
                counts[field] += 1
            else:
                print(f"{name} {field}: expected {row[field]!r}, got {actual_value!r}")

    print(f"Images: {len(labels)}; parser failures: {failures}")
    for field in FIELDS:
        print(f"{field}: {counts[field]}/{len(labels)} ({counts[field] / len(labels):.1%})")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
