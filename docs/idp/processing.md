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

## Confidence routing

`core/idp/pipeline.py` decides what needs a person and why: a document whose type is unknown or
below 0.6, a required field missing or below 0.7, or pages the engine could not read. The result
names the documents to review and the reasons per document. The next part draws the boxes in the
review interface and sends routed documents to the review queue.
