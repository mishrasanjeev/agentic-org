# Knowledge base document ingestion and OCR

Status: shipped runtime. Exact limits and extensions are advertised by the
authenticated `GET /api/v1/knowledge/supported-types` endpoint. See
[current product status](PRODUCT_STATUS.md) for the production boundary.

The Knowledge Base accepts business documents only when AgenticOrg can extract
usable text before changing durable state. Accepted uploads pass through one
canonical extraction, chunking, provenance, embedding, and tenant-scoped search
pipeline.

## Supported documents

| Family | Extensions | Extraction |
| --- | --- | --- |
| Text and data | `txt`, `md`, `csv`, `tsv`, `json`, `yaml`, `xml`, `log` | Native decode/structured parser |
| Web and email | `html`, `htm`, `eml` | Active content removed; body text only |
| PDF | `pdf` | Native page text first; OCR for low-text/scanned pages |
| Microsoft Office | `docx`, `xlsx`, `pptx` | Native paragraph, sheet, slide, and table extraction |
| Legacy Office | `doc`, `xls`, `ppt` | Isolated headless LibreOffice conversion, then native extraction |
| OpenDocument | `odt`, `ods`, `odp` | Isolated headless LibreOffice conversion, then native extraction |
| Other documents | `rtf` | RTF text parser |
| Scans and images | `png`, `jpg`, `jpeg`, `tif`, `tiff`, `bmp`, `webp` | Tesseract OCR with confidence |

Audio and video are not document uploads and are explicitly rejected. The API
advertises the current matrix through `GET /api/v1/knowledge/supported-types`.

## Layout and chunking

Extraction keeps the layout it can see. A PDF page is split into paragraphs at blank lines and
each paragraph is numbered through the document; a short line that reads as a section title
(numbered, in capitals, or in title case without a final stop) is a heading, and the paragraphs
under it carry its text. A Word document keeps its Heading and Title styles as headings, numbers
its paragraphs, and attaches table rows to the section they sit in. Every chunk records the
page, the paragraph number and the nearest heading of its first span in `knowledge_chunk_sources`
(`paragraph`, `heading`), beside the page, sheet, cell range and frame provenance it already had,
so a citation can name the place (`core/rag/extractors.py`).

How spans become chunks is a tenant setting, `chunk_strategy` in the tenant AI settings
(`core/rag/chunking.py`), with `chunk_size` (tokens, four characters a token here) as the size
band:

| Strategy | What it does |
|---|---|
| `sentence` (default) | The original behaviour: cut at sentence boundaries into a 120 to `chunk_size` band and merge short neighbours. Layout is not consulted. A tenant that has set nothing is chunked exactly as before. |
| `paragraph` | One chunk per paragraph, table row or list item; short consecutive paragraphs under the same heading merge up to the band; a long paragraph is cut at sentence boundaries. A chunk never crosses a heading. |
| `heading` | Spans are grouped under their nearest heading until the band is full, and each chunk starts with the heading's text, so a retrieved chunk says which section it came from. A chunk never crosses a heading. |

Changing the strategy affects documents ingested after the change; existing chunks are not
re-chunked (re-indexing is a later part of this package). Heading detection on PDFs is a
heuristic over line shape; a document whose titles end with a full stop or run to several lines
is read as paragraphs only, which the `sentence` and `paragraph` strategies handle as before.

## Search: filters and re-ranking

`POST /knowledge/search` takes optional `filters` (`core/rag/filters.py`): `category`, `source`
and `file_type` (lists; a document matches any value), and a `created_from` / `created_to` date
window. Every filter is an `AND` condition on the knowledge documents, applied inside the dense
and the sparse rankings alike, so the fused result only ever holds documents that pass.
`category` is the source object type ingestion records for a chunk (`document`, `invoice` and so
on) and `source` the upload it came from; a document's branch, product line or business segment
is not yet a field of its own, which the access-control part of this package adds. A narrowed
search answers from the knowledge base only: the upload-metadata fallback carries nothing to
filter on and is not consulted.

With `AGENTICORG_KNOWLEDGE_HYBRID_SEARCH` on, a search fuses the dense (pgvector) and sparse
(PostgreSQL full text) rankings by reciprocal rank. With `AGENTICORG_KNOWLEDGE_RERANK_ENABLED` on
as well (`core/rag/rerank.py`), the fused pool (four times `top_k`, at most 200) is re-scored on
the query's own terms: coverage of the query terms, the query as a phrase, how close the terms
sit, a title match, and the fused score as the tie-breaker, weighted into one score from 0 to 1.
The re-ranker reads the texts only and calls no model; it knows no synonyms and no meaning, which
is why it sits behind a switch and why its scores are explainable. Off, the fused order is
returned as it was.

## Citations and excerpts

Every search hit carries a `citation` (`core/rag/citations.py`): the chunk row's id, its source,
its chunk number, and the place in the source that ingestion recorded for it (page, paragraph
number, nearest heading, sheet, cell range). The search paths read it through a join on
`knowledge_chunk_sources`, so a hit from a chunk that keeps no provenance (an older upload, a
RAGFlow document) carries `null`; the three original fields of a hit are unchanged.

`GET /knowledge/documents/{id}/excerpt?q=` returns the cited chunk whole, with the query's terms
located in it as character spans, its citation, and the ids of the chunks either side in the same
upload, so the console can open the place in the source and move through it. The text passes the
retrieval guardrails as a search result does; a chunk they withhold is not found. The Knowledge
Base page shows the citation beside each hit and opens the excerpt with the terms marked and
previous/next links.

An excerpt is the chunk as it was ingested, not the original file: a page image, a table's
formatting or a figure are not shown. Access to a document is the tenant's as a whole; document
access control is the next part of this package.

## OCR flow

```mermaid
flowchart LR
    A[Bounded upload] --> B{Native text available?}
    B -->|Yes| C[Preserve page/slide/sheet provenance]
    B -->|No or low text PDF page| D[Render at 300 DPI]
    D --> E[Grayscale, contrast, resize, orientation]
    E --> F[Tesseract OCR]
    F --> G[Text + mean confidence + OCR page list]
    C --> H[Canonical chunks]
    G --> H
    H --> I[Tenant-scoped embeddings and search]
```

The production image includes Poppler, LibreOffice, and Tesseract language data
for English, Hindi, Bengali, Gujarati, Kannada, Malayalam, Marathi, Punjabi,
Tamil, Telugu, and Urdu. By default, orientation/script detection chooses the
matching installed India-first language pack together with English. Operators
can set `AGENTICORG_OCR_LANGUAGES` to an explicit installed combination.

## Safety and accuracy rules

- Uploads are streamed with a hard size limit before parser invocation.
- OOXML/ODF ZIP members and expanded size are bounded to resist archive bombs.
- PDF pages, CPU-heavy OCR pages, and decompressed image pixels are bounded.
- Multi-page TIFF documents are OCRed frame by frame with page provenance.
- Replacement extraction runs before the old document is deleted.
- A corrupt, unsupported, or zero-text document returns an explicit 4xx error.
- The UI shows extraction method, whether OCR ran, and OCR confidence.
- Raw files are not interpreted as executable HTML, scripts, or macros.
- Retrieval preserves source page, slide, sheet, row, and freshness metadata.

## Local and production checks

Run the focused extractor tests and then build the production Docker image. The
image check must create an actual scanned image/PDF, execute Tesseract and
Poppler in the container, and assert the expected phrase is extracted. Mocked
OCR tests are useful for edge cases but are not sufficient release evidence.

Production validation should use an approved smoke tenant and a synthetic,
non-sensitive document. A successful supported-types response proves the
capability matrix is deployed; it does not by itself prove extraction quality
for every language, layout, handwriting style, or damaged scan.

Run the [local multichannel simulation](local-multichannel-simulation.md) for a
real synthetic-image OCR proof alongside voice, email, and RPA checks. Its JSON
evidence records extraction method, confidence, and page count without storing
a customer document.
