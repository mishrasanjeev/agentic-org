"""Custom Presidio recognizers for Indian PII entities.

Covers Aadhaar, PAN, GSTIN, and UPI ID patterns that are not included
in Presidio's built-in recognizer registry.
"""

from __future__ import annotations

try:
    from presidio_analyzer import Pattern, PatternRecognizer
except ImportError:  # pragma: no cover
    # Stub base class when presidio is not installed (tests mock it)
    class Pattern:  # type: ignore[no-redef]
        def __init__(self, name: str = "", regex: str = "", score: float = 0.0) -> None:
            self.name = name
            self.regex = regex
            self.score = score

    class PatternRecognizer:  # type: ignore[no-redef]
        def __init__(self, supported_entity: str = "", patterns: list | None = None,
                     name: str = "", context: list | None = None, **kwargs: object) -> None:
            self.supported_entity = supported_entity
            self.patterns = patterns or []
            self.name = name
            self.context = context or []


class AadhaarRecognizer(PatternRecognizer):
    """Recognise Indian Aadhaar numbers (12 digits, optional spaces/dashes).

    Pattern: ``\\b\\d{4}[\\s-]?\\d{4}[\\s-]?\\d{4}\\b``
    """

    AADHAAR_PATTERN = r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"

    def __init__(self) -> None:
        patterns = [
            Pattern(
                name="aadhaar",
                regex=self.AADHAAR_PATTERN,
                score=0.85,
            ),
        ]
        super().__init__(
            supported_entity="AADHAAR",
            patterns=patterns,
            name="AadhaarRecognizer",
            context=["aadhaar", "uid", "uidai", "aadhar"],
        )


class PANRecognizer(PatternRecognizer):
    """Recognise Indian PAN numbers (XXXXX1234X).

    Pattern: ``\\b[A-Z]{5}\\d{4}[A-Z]\\b``
    """

    PAN_PATTERN = r"\b[A-Z]{5}\d{4}[A-Z]\b"

    def __init__(self) -> None:
        patterns = [
            Pattern(
                name="pan",
                regex=self.PAN_PATTERN,
                score=0.85,
            ),
        ]
        super().__init__(
            supported_entity="PAN",
            patterns=patterns,
            name="PANRecognizer",
            context=["pan", "permanent account number", "income tax"],
        )


class GSTINRecognizer(PatternRecognizer):
    """Recognise Indian GSTIN numbers (15 characters).

    Pattern: ``\\b\\d{2}[A-Z]{5}\\d{4}[A-Z]\\d[Z][A-Z\\d]\\b``
    """

    GSTIN_PATTERN = r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]\b"

    def __init__(self) -> None:
        patterns = [
            Pattern(
                name="gstin",
                regex=self.GSTIN_PATTERN,
                score=0.90,
            ),
        ]
        super().__init__(
            supported_entity="GSTIN",
            patterns=patterns,
            name="GSTINRecognizer",
            context=["gstin", "gst", "goods and services tax"],
        )


class UPIRecognizer(PatternRecognizer):
    """Recognise Indian UPI IDs (name@bankname format).

    Pattern: ``[\\w.]+@[\\w]+``

    Scored conservatively because the pattern overlaps with email.
    Context words boost confidence.
    """

    UPI_PATTERN = r"[\w.]+@[\w]+"

    def __init__(self) -> None:
        patterns = [
            Pattern(
                name="upi",
                regex=self.UPI_PATTERN,
                score=0.40,
            ),
        ]
        super().__init__(
            supported_entity="UPI",
            patterns=patterns,
            name="UPIRecognizer",
            context=["upi", "vpa", "unified payment", "pay", "gpay", "phonepe", "paytm"],
        )


# Convenience list for bulk registration
ALL_INDIA_RECOGNIZERS = [
    AadhaarRecognizer,
    PANRecognizer,
    GSTINRecognizer,
    UPIRecognizer,
]
