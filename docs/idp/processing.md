# Intelligent document processing: classification and extraction

With `AGENTICORG_IDP_ENABLED` on, `POST /idp/analyse` takes a PDF or an image and returns the
documents in it, typed, with their fields and tables and the boxes to draw them (`core/idp/`).
Off, `GET /idp/document-types` still answers with `enabled: false` and the rest is not found.

## Pages

`core/idp/pages.py` reads a PDF's text layer into words with bounding boxes in page points
(origin top-left); a page with no text layer is OCR'd (Tesseract through `pytesseract`, 200 dpi,
boxes scaled back to page points, orientation and script detected) when the engine is installed.
When it is not, the page says `ocr: unavailable` and the document is routed to review rather than
silently read as empty. Images are one page each. A file is at most 25 MB and 50 pages. Words are
grouped into lines by vertical position; every word, line and page carries a confidence (1.0 from a
text layer, the engine's figure from OCR).

## Classification and bundle splitting

`core/idp/classify.py` types a page by weighted patterns over a synthetic catalogue of the
documents a bank handles: government identity document, address proof, bank statement, salary
slip, income tax return, invoice, loan application form, cheque, property document, agreement,
KYC form. The confidence is the sum of the weights matched; below 0.5 the page is `unknown`, not a
guess. `core/idp/bundle.py` splits a file into segments of consecutive pages, one document each: a
new segment starts when the type changes or when a page of the same type looks like a first page
again (a second statement in the bundle); untyped continuation pages join the document before
them. `POST /idp/classify-text` is a dry run of the classifier.

## Fields and tables

`core/idp/fields.py` extracts the fields of each document type (account number, balances, IFSC,
statement period; employee, pay period, gross and net pay; invoice number, date, GSTIN, total;
identity number, name, date of birth; applicant, loan amount, tenure; and so on): a label pattern
and a value pattern, the value on the same line or the next. Each field found has a page, a box
(the union of its words' boxes), the source line and a confidence combining the label match, the
value match and the OCR confidence of its words; a required field not found is reported as
`missing`. A generic pass also picks up `Label: value` lines the spec does not name, at a lower
confidence. `core/idp/tables.py` extracts tables: the PDF layout engine's where it finds any, else
runs of consecutive lines whose columns align (how a statement's transaction list reads), each
with its page, box, header and rows.

## Review with overlays

`POST /idp/analyse?store=true` keeps the file and the result (`idp_documents`, migration
`v6z64_idp_documents`, `core/idp/store.py`); a document the pipeline routed to review waits in
`review`, the rest in `processed`. `GET /idp/documents` lists them by status; `GET
/idp/documents/{id}` serves a document with its pages, segments, documents, fields (corrections
applied, the extracted value kept beside), tables and review reasons; `GET
/idp/documents/{id}/pages/{n}.png` renders a page from the kept file at 110 dpi so the overlay draws
boxes on the real page. `POST /idp/documents/{id}/fields` takes a reviewer's value for one field
(who and when are kept); `POST /idp/documents/{id}/decide` approves or rejects, after which the
document is closed to corrections.

The Documents page (`ui/src/pages/Documents.tsx`) lists documents by status, shows the page with
every field drawn where it came from (green at or above the field floor, amber below, red for a
missing required field, indigo once corrected; tables dashed), lets the reviewer click a field to
find its box and page, edit its value and save, and approve or reject the file.

## Reconciliation, stamps and reports

`GET /idp/documents/{id}/reconcile` (`core/idp/reconcile.py`) compares the fields that should
agree across the documents of one file: the name on the identity document, the salary slip, the
application form, the address proof, the KYC form and the statement; the date of birth on the
identity document and the KYC form; the PAN on the identity document and the tax return. Names are
compared after normalisation (case, accents, honorifics, word order; an initial matches the name it
starts), dates after parsing, identifiers without spaces, amounts within two percent. Every
disagreement names every value with its document, page and box; items seen once are reported as
unverified, items seen nowhere as absent.

`GET /idp/documents/{id}/stamps` (`core/idp/stamps.py`) renders each page and finds regions of
saturated ink (blue, purple, red, green) that are not text: the page is downscaled, saturated
pixels are counted per grid cell, dense cells are grouped, and each region becomes a candidate with
its box, colour, coverage, density and a confidence. The document type on the page says whether a
stamp was expected (cheque, agreement, property document, KYC form, loan application). What is
checked is presence, colour and size, never authenticity.

`GET /idp/documents/{id}/report` (`core/idp/report.py`, JSON or `?format=markdown`) assembles the
analysis report: each document with its key fields (corrections applied), missing and weak fields
and tables; the reconciliation; the stamp check; the review reasons; and a narrative of a few
sentences built from those parts by fixed rules, never from a model.

## Statement line items and version comparison

`GET /idp/documents/{id}/statement` (`core/idp/statements.py`) reads the transactions of a kept
bank statement from its extracted table: the header says which column is the date, the
description, the debit, the credit and the balance; amounts are parsed (brackets and Dr as
negative); a row with no date continues the description above it; salary credits and returned or
bounced items are flagged. The running balance is checked row by row against the opening balance
and every debit and credit, so a misread amount shows up as a break rather than a wrong total. The
summary names totals, months covered, first and last dates, average and minimum balance, salary
credits, returned items and whether the closing balance on the statement agrees with the rows.

`GET /idp/documents/{id}/compare/{other}` (`core/idp/compare.py`) compares one document (by index)
in two kept files: fields changed, added and removed with both values and their boxes (names,
dates, identifiers and amounts compared as the reconciliation does), lines of page text added and
removed with a similarity per page, and table rows added and removed. The result says whether the
versions are identical and counts what changed.

## Confidence routing

`core/idp/pipeline.py` decides what needs a person and why: a document whose type is unknown or
below 0.6, a required field missing or below 0.7, or pages the engine could not read. The result
names the documents to review and the reasons per document. The next part draws the boxes in the
review interface and sends routed documents to the review queue.
