"""Pin the ``replace`` vs ``allow_duplicate`` contract on KB upload.

Codex 2026-04-22 flagged that the UI alert said "check the duplicate box
to replace it" while the backend duplicate-box simply added another
document with the same filename — it did not replace anything. The
frontend now offers an honest "Replace / Upload as second copy" choice
and the backend has a real ``?replace=true`` path that soft-deletes the
existing document before ingesting the new one.

We don't drive the full upload via TestClient here because that requires
a live DB; we just pin the route signature and the mutual-exclusion
guard. Full integration coverage is in
``tests/integration/test_db_api_endpoints.py``.
"""

from __future__ import annotations

import inspect

from api.v1 import knowledge as kb_mod


class TestUploadEndpointSignature:
    def test_accepts_both_replace_and_allow_duplicate(self) -> None:
        sig = inspect.signature(kb_mod.upload_document)
        for flag in ("allow_duplicate", "replace"):
            assert flag in sig.parameters, (
                f"Missing {flag} query parameter — users can't tell the "
                "backend what duplicate behaviour they want."
            )

    def test_both_default_to_false(self) -> None:
        sig = inspect.signature(kb_mod.upload_document)
        # Query(default=False, ...) — unwrap the FastAPI ``Query`` to
        # get the default value.
        for flag in ("allow_duplicate", "replace"):
            param = sig.parameters[flag]
            default = getattr(param.default, "default", param.default)
            assert default is False, (
                f"{flag} must default to False — opting in to duplicate "
                "or replace should be an explicit user choice."
            )


class TestSourceHonesty:
    """The regression classes from the bug-sweep-patterns memory file
    (#13 in the revised list): UI copy must match what the backend
    actually does. Pin the phrase that's safe to render."""

    def test_backend_error_message_no_longer_says_check_duplicate_box(self) -> None:
        src = inspect.getsource(kb_mod.upload_document)
        # The backend's 409 detail message must mention the real flags:
        # ?replace=true or ?allow_duplicate=true. The old text "check
        # the duplicate box" was misleading because the UI had no such
        # "duplicate box" and the flag it did use didn't replace.
        assert "?replace=true" in src, (
            "409 detail must document the replace option so API callers "
            "know how to get real replacement."
        )
        assert "allow_duplicate=true" in src
