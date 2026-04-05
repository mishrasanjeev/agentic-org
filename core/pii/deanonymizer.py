"""De-anonymization: restore original PII values from token placeholders.

Takes redacted text containing tokens like ``<PERSON_1>``, ``<AADHAAR_1>``
and a token_map that links each token to its original value.
"""

from __future__ import annotations

import re


def deanonymize(text: str, token_map: dict[str, str]) -> str:
    """Replace PII tokens in *text* with original values from *token_map*.

    The replacement is performed in a single pass using ``re.sub`` so that
    nested or overlapping tokens are handled safely (no double-replacement).

    Args:
        text: The redacted text containing ``<TOKEN>`` placeholders.
        token_map: Mapping of ``"<TOKEN>"`` -> ``"original_value"``.

    Returns:
        Text with all known tokens replaced by their originals.
    """
    if not token_map:
        return text

    # Build a combined regex that matches any of the known tokens.
    # Tokens are escaped to prevent regex injection.
    escaped_tokens = [re.escape(tok) for tok in token_map]
    pattern = re.compile("|".join(escaped_tokens))

    return pattern.sub(lambda m: token_map.get(m.group(0), m.group(0)), text)
