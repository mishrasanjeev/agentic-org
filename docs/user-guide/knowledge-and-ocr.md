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

Each search result shows the document and, where recorded, its citation: page, sheet, cell range, paragraph or heading. **Open excerpt** shows the whole cited passage, with **Previous** and **Next** for the neighbouring passages. Where the deployment turns on query transformation (`AGENTICORG_KNOWLEDGE_QUERY_TRANSFORM_ENABLED`, off by default), **How this was retrieved** lists each retrieval step. Documents uploaded from this page are shared with the whole tenant. An API upload can name a domain (`?domain=`), and then users limited to other domains do not see that document.

If a duplicate dialog appears, choose **Replace** to index a new version before retiring the previous searchable copy, **Keep both** for a deliberate second copy, or **Cancel** to leave it unchanged. A failed replacement does not retire the original. Verify that search returns the intended version; do not leave conflicting policies current without an effective-date rule.

| Status | Meaning | Next step |
| --- | --- | --- |
| Processing | Stored, but not yet confirmed searchable | The page refreshes while processing. Do not repeatedly upload the same file. |
| Indexed | Indexing succeeded | Search a distinctive phrase and open its cited excerpt. |
| Failed | Indexing did not succeed | Inspect the error, then upload again and choose Replace. The prior good copy is retained. |

Where the administrator enables native upload recovery, temporary embedding timeouts, connection failures, rate limits and server errors receive at most four retries after the initial attempt, with delays of 30, 60, 120 and 240 seconds. The existing worker checks once a minute, so these are minimum delays, not completion guarantees. Invalid files, authorization errors and permanent indexing errors are not automatically retried. Large extractions outside the recovery storage bound and external RAGFlow indexing use the normal upload path instead. Your administrator can confirm which path is enabled.

```flow
Approved upload | Size and type are validated before parsing.
Extract or OCR | Native text is preferred; scanned pages are recognized where needed.
Index with provenance | Chunks retain page, sheet, slide and freshness context.
Search and review | Verify a known answer against the source before operational use.
```

## Get better results from scans

Use clean, upright, complete scans with readable text. Avoid extreme compression, glare, truncated pages and mixed orientations. Keep the original for comparison. The OCR image includes India-first language packs, but operators must check installed languages and deployment settings for their material.

OCR confidence is diagnostic metadata, not the probability that a bank account number or monetary value is correct. Manually verify identifiers, totals, dates, negatives, handwritten fields and multi-column reading order. For a financial process, route ambiguous extraction to a reviewer; never reconstruct unreadable digits from context.

## Errors and maintenance

Corrupt, oversized, unsupported or zero-text documents fail explicitly. A failed upload is not an empty valid policy. If OCR fails, ask the operator to check Poppler/Tesseract/LibreOffice availability and language packs. If extraction works but search fails, investigate embeddings and indexing separately.

Assign a content owner, review date and replacement/deletion procedure. Record which agents use the knowledge set. Use approved retention and access policies for customer documents; deleting a document and deleting derived evidence/history are different operations.

Next: [First agent](/docs/first-agent), [Insurance assistance](/docs/bfsi-insurance), [Security and data](/docs/security-and-data).
