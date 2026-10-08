# SPDX-License-Identifier: Apache-2.0
"""Intelligent document processing: pages with word boxes, classification, bundle splitting, fields and tables.

``pages``: a PDF or image as pages of words with bounding boxes (text layer
or OCR). ``classify``: the document type of a page by rules. ``bundle``: a
multi-document file split into segments. ``fields``: key-value extraction
per document type with a confidence and a box per field. ``tables``: table
extraction. ``pipeline``: one call that does all of it and says what needs
review.
"""
