## What Knowledge Base does

Knowledge Base extracts text, preserves provenance, chunks documents and supports tenant-scoped retrieval. It is not model training. Extraction/indexing success and a useful retrieved answer are separate checks. Your selected agent still needs the appropriate knowledge/tool path.

Open **Knowledge Base** in the authenticated workspace. The deployed supported-type and limit metadata is the authority for your environment; administrators can inspect `GET /api/v1/knowledge/supported-types` with approved credentials.

## Supported document families

| Family | Typical formats | Handling |
| --- | --- | --- |
| Text/data | TXT, MD, CSV, TSV, JSON, YAML, XML, LOG | Native parsing/decoding |
| Web/email | HTML, HTM, EML | Body text extraction; active content removed |
| PDF | PDF | Native text first; OCR for low-text/scanned pages |
| Modern Office | DOCX, XLSX, PPTX | Paragraph, table, sheet and slide extraction |
| Legacy/OpenDocument | DOC, XLS, PPT, ODT, ODS, ODP | Isolated conversion, then extraction |
| Other | RTF | Text parsing |
| Images/scans | PNG, JPG/JPEG, TIF/TIFF, BMP, WEBP | OCR with confidence and page provenance |

Audio and video are not document uploads. File size, pages, decompressed pixels and archive expansion are bounded. Do not bypass limits by renaming an unsupported file.

## Upload and verify

1. Confirm data-owner approval, company context and the procedure's effective version.
2. Choose a clearly named document, such as `Complaint Routing - reviewed 2026-09`.
3. Upload and wait for a visible successful extraction/indexing outcome.
4. Inspect extraction method, whether OCR ran and the confidence metadata.
5. Search a known phrase and review source/page/sheet/slide details.
6. Run a grounded question and verify it against the original document.

If a duplicate dialog appears, choose the appropriate supported action deliberately. Replacement extraction is performed before old content is removed, but you should still verify that search returns the intended version. Do not leave two conflicting policies current without an explicit effective-date rule.

```flow
Approved upload | Size and type are validated before parsing.
Extract or OCR | Native text is preferred; scanned pages are recognized where needed.
Index with provenance | Chunks retain page, sheet, slide and freshness context.
Search and review | Verify a known answer against the source before operational use.
```

## Get better results from scans

Use clean, upright, complete scans with readable text. Avoid extreme compression, glare, cut-off pages and mixed orientations. Keep the original for comparison. Production OCR includes India-first language packs, but operators must check installed languages and deployment settings for their material.

OCR confidence is diagnostic metadata, not the probability that a bank account number or monetary value is correct. Manually verify identifiers, totals, dates, negatives, handwritten fields and multi-column reading order. For a financial process, route ambiguous extraction to a reviewer; never reconstruct unreadable digits from context.

## Errors and maintenance

Corrupt, oversized, unsupported or zero-text documents fail explicitly. A failed upload is not an empty valid policy. If OCR fails, ask the operator to check Poppler/Tesseract/LibreOffice availability and language packs. If extraction works but search fails, investigate embeddings and indexing separately.

Assign a content owner, review date and replacement/deletion procedure. Record which agents use the knowledge set. Use approved retention and access policies for customer documents; deleting a document and deleting derived evidence/history are different operations.

Next: [First agent](/docs/first-agent), [Insurance assistance](/docs/bfsi-insurance), [Security and data](/docs/security-and-data).
