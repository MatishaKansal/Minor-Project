# Multimodal Financial Assistant

This repository contains image/OCR and text-based PDF parsing. It turns supported images or a text PDF into extracted text, deterministic financial fields, and field-level provenance.

## Install

From the project directory:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Install Tesseract OCR separately on Windows. The commonly used installer is available from the [UB Mannheim Tesseract builds](https://github.com/UB-Mannheim/tesseract/wiki). During installation, include the languages needed by your documents and note the path to `tesseract.exe`.

If Tesseract is not on `PATH`, configure it for the current PowerShell session:

```powershell
$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

The parser also uses `eng` by default. Pass another installed language code to `parse_image()` when needed.

## Run the image parser

Put an image in `data/input/images/`, then run:

```powershell
python -c "from backend.parsers.image_parser import parse_image; print(parse_image('data/input/images/test.jpg').model_dump_json(indent=2))"
```

## Run the PDF parser

Text-based PDFs are extracted page by page with PyMuPDF. Image-only PDFs are reported as failed until PDF-to-image OCR is implemented:

```powershell
python -c "from backend.parsers.pdf_parser import parse_pdf; print(parse_pdf('data/input/invoice.pdf').model_dump_json(indent=2))"
```

PDF results use the same `Evidence` schema as images. Provenance retains the original PDF line, page number, line number, and deterministic extraction method. PDFs can be persisted with `persist_to_database=True`; the existing Supabase tables and automatic business resolution are reused.

Example output:

```json
{
  "source_type": "image",
  "file_name": "test.jpg",
  "extracted_text": "INVOICE\\nTotal: $42.50",
  "page_number": 1,
  "language": "eng",
  "confidence_score": 91.4,
  "processing_status": "success",
  "error": null
}
```

A file named `data/input/images/test.jpg` is also covered by an optional integration test when it exists.

## Tests

```powershell
pytest -q
```

## Validate Real Images

Place receipt or invoice images in `data/input/images/`, then run the validation runner:

```powershell
python tests/run_image_parser.py
```

The runner processes PNG, JPG/JPEG, WEBP, and TIFF files with the default OCR-only parser settings. It prints each image's status, OCR confidence, deterministic financial fields, and full extracted OCR text. It does not use database persistence or modify Supabase.

To print provenance for each extracted field, the same runner displays the raw OCR source line, line number, extraction method, and confidence after each successful result.

## Persist OCR results to Supabase

The default parser only returns OCR output. To persist an image, run this command; the repository automatically resolves the active/default business:

```powershell
python -c "from backend.parsers.image_parser import parse_image; print(parse_image('data/input/images/test.jpg', persist_to_database=True, file_type='receipt').model_dump_json(indent=2))"
```

This uploads the image to the configured Supabase Storage bucket, then inserts rows into `source_file`, `financial_evidence`, `evidence_details`, and `provenance`. The raw OCR text is stored in `evidence_details.details` and `provenance.source_text`. Server-side writes use `SUPABASE_SERVICE_ROLE_KEY`; never expose that key to browser code.

Configure `SUPABASE_STORAGE_BUCKET` with an existing bucket name. If it is omitted, the repository uses the only existing bucket; it refuses to guess when there are zero or multiple buckets.

### Verify Database Persistence

The opt-in pytest integration test automatically resolves the sole existing business, creates `My Business` when the table is empty, and refuses to choose when multiple businesses exist. It is skipped unless Supabase credentials are configured:

```powershell
pytest -q -o addopts= -m integration tests/test_image_parser_database.py
```

For a manual verification that queries the inserted rows:

```powershell
python tests/test_image_parser_database_manual.py
```

The manual check does not delete, update, truncate, or drop records. It creates new evidence rows and verifies `source_file`, `financial_evidence`, and `provenance` by their returned IDs.

### Business Image Batch

All uploaded documents for one audit business automatically use the same resolved business. Each image still receives a new `source_file.file_id` and `financial_evidence.evidence_id`:

```powershell
python tests/test_business_image_batch.py
```

The batch verifier processes every supported image in `data/input/images/`, prints the IDs for each successful image, and verifies that the records belong to the one configured business. It never creates merchant records as businesses and never deletes existing rows.

## Troubleshooting

- **Tesseract unavailable:** install Tesseract and set `TESSERACT_CMD`, or add its install directory to `PATH`.
- **Poor recognition:** use a sharp, high-contrast scan, straighten the document, and ensure the required Tesseract language data is installed.
- **Missing symbols or dates:** increase source image resolution and check that the selected language matches the document.
- **Unsupported or corrupt image:** convert it to PNG, JPG, WEBP, or TIFF and confirm that it opens normally in an image viewer.
- **Low confidence:** the score is Tesseract's average word confidence; it is not a correctness guarantee and is `null` when Tesseract supplies no usable confidence values.
