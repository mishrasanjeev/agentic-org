# SPDX-License-Identifier: Apache-2.0
"""Run the untrusted-content extractor out of process and fail closed.

``extract`` starts ``core/extraction/_worker.py`` as a separate Python process
(``python -I -S -B``: no environment variables, site-packages or working
directory on the import path, no bytecode written), with a minimal environment and a fresh empty working
directory. It sends the content on stdin and reads at most
``max_output_bytes`` from stdout under a wall-clock limit. A timeout, a crash,
an oversized or malformed response, or a response that does not match the
field schema produces a failed :class:`ExtractionResult` with a reason code and
no fields - never partial data.

On Linux the worker must report an active seccomp filter (sockets and program
execution refused by the kernel) unless ``require_os_isolation=False``; on
other platforms isolation is the separate process, resource limits where the
OS has them and an in-process audit hook. See
``docs/security/untrusted-content.md`` for what each layer does and does not
defend against.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import structlog
from prometheus_client import Counter, Histogram

from core.extraction import _worker
from core.extraction.context import UntrustedTextRegistry
from core.extraction.excerpts import Excerpt, ExcerptStore, excerpt_ref
from core.extraction.schema import (
    CONTENT_TYPES,
    FIELDS,
    WORKER_REASONS,
    ExtractionFailure,
    FieldValue,
    OutputInvalidError,
    SourceKind,
    validate_response,
)

logger = structlog.get_logger()

WORKER_PATH = Path(_worker.__file__).resolve()
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_INPUT_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_OUTPUT_BYTES = 512 * 1024
_MAX_STDERR_BYTES = 64 * 1024
# -I: ignore PYTHON* variables and the user site; -S: no site-packages; -B: write no bytecode.
_INTERPRETER_FLAGS = ("-I", "-S", "-B")

_extraction_total = Counter(
    "agenticorg_extraction_total",
    "Untrusted-content extractions, by source kind and outcome",
    ["kind", "outcome"],
)
_extraction_seconds = Histogram(
    "agenticorg_extraction_duration_seconds",
    "Wall-clock duration of untrusted-content extractions",
    ["kind"],
)


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    kind: SourceKind
    ok: bool
    # Reason code when ``ok`` is false.
    failure: ExtractionFailure | None
    # ``sha256:<hex>`` of the content bytes that were submitted.
    source_hash: str
    # Every schema field for the kind when ``ok``; empty otherwise. Lists are tuples.
    fields: Mapping[str, FieldValue] = field(default_factory=lambda: MappingProxyType({}))
    # Fields whose source value was present but ambiguous or outside its constraints.
    rejected_fields: tuple[str, ...] = ()
    # Field -> excerpt references, for the human reviewer. Texts live in the ExcerptStore.
    excerpt_refs: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))
    # Isolation layers the worker reported as active.
    isolation: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "ok": self.ok,
            "failure": self.failure.value if self.failure else None,
            "source_hash": self.source_hash,
            "fields": {name: list(value) if isinstance(value, tuple) else value for name, value in self.fields.items()},
            "rejected_fields": list(self.rejected_fields),
            "excerpt_refs": {name: list(refs) for name, refs in self.excerpt_refs.items()},
            "isolation": list(self.isolation),
        }


def _default_require_os_isolation() -> bool:
    return sys.platform.startswith("linux")


def _worker_env() -> dict[str, str]:
    env = {"PYTHONIOENCODING": "utf-8", "LC_ALL": "C.UTF-8"}
    # Windows cannot initialise the interpreter without SYSTEMROOT.
    for name in ("SYSTEMROOT", "SystemRoot"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


async def _read_capped(stream: asyncio.StreamReader, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return b"".join(chunks), False
        size += len(chunk)
        if size > limit:
            return b"", True
        chunks.append(chunk)


@dataclass(slots=True)
class _Run:
    returncode: int | None = None
    stdout: bytes = b""
    stderr_bytes: int = 0
    timed_out: bool = False
    output_too_large: bool = False
    start_failed: bool = False


async def _run_worker(
    argv: list[str],
    payload: bytes,
    *,
    timeout_s: float,
    max_output_bytes: int,
    prepare: Callable[[str], None] | None = None,
) -> _Run:
    # Creating and removing the working directory is file-system I/O: keep it off the event loop.
    workdir = await asyncio.to_thread(tempfile.mkdtemp, prefix="agenticorg-extract-")
    try:
        if prepare is not None:
            await asyncio.to_thread(prepare, workdir)
        return await _run_in(workdir, argv, payload, timeout_s=timeout_s, max_output_bytes=max_output_bytes)
    finally:
        # Best-effort removal of a scratch directory; nothing in it is read back.
        await asyncio.to_thread(shutil.rmtree, workdir, True)


async def _run_in(workdir: str, argv: list[str], payload: bytes, *, timeout_s: float, max_output_bytes: int) -> _Run:
    run = _Run()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_worker_env(),
            cwd=workdir,
        )
    except OSError:
        run.start_failed = True
        return run

    stdin, stdout_stream, stderr_stream = process.stdin, process.stdout, process.stderr
    if stdin is None or stdout_stream is None or stderr_stream is None:
        raise RuntimeError("worker pipes were not created")

    async def communicate() -> None:
        async def feed() -> None:
            try:
                stdin.write(payload)
                await stdin.drain()
                stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass  # the worker exited early; its exit status says why

        async def read_stdout() -> bytes:
            data, too_large = await _read_capped(stdout_stream, max_output_bytes)
            if too_large:
                # Stop the worker now; it would otherwise block writing and hold the pipes open.
                run.output_too_large = True
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            return data

        _, stdout, (stderr, _) = await asyncio.gather(
            feed(),
            read_stdout(),
            _read_capped(stderr_stream, _MAX_STDERR_BYTES),
        )
        run.stdout = stdout
        run.stderr_bytes = len(stderr)
        run.returncode = await process.wait()

    try:
        await asyncio.wait_for(communicate(), timeout=timeout_s)
    except TimeoutError:
        run.timed_out = True
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        # Drain what a killed worker left in its pipes so the transport closes now, not at GC.
        for stream in (stdout_stream, stderr_stream):
            try:
                await asyncio.wait_for(stream.read(), timeout=2.0)
            except (TimeoutError, OSError, ValueError):
                pass
    return run


def _decode(stdout: bytes) -> tuple[dict[str, Any] | None, str | None]:
    """Parse a worker response. Any failure (including pathological nesting) is a reason, never an exception."""
    try:
        response = json.loads(stdout)
    except (ValueError, RecursionError) as exc:
        return None, f"response is not valid JSON ({type(exc).__name__})"
    if not isinstance(response, dict):
        return None, "response is not a JSON object"
    return response, None


def _prepare_probe(workdir: str) -> None:
    for name in (_worker.PROBE_DELETE_TARGET, _worker.PROBE_RENAME_TARGET):
        with open(os.path.join(workdir, name), "w", encoding="utf-8") as handle:
            handle.write("probe target")


def _failed(
    kind: SourceKind, source_hash: str, reason: ExtractionFailure, started: float, **log: Any
) -> ExtractionResult:
    _extraction_total.labels(kind=kind.value, outcome=reason.value).inc()
    _extraction_seconds.labels(kind=kind.value).observe(time.monotonic() - started)
    logger.warning("extraction_failed", kind=kind.value, reason=reason.value, source_hash=source_hash, **log)
    return ExtractionResult(kind=kind, ok=False, failure=reason, source_hash=source_hash)


async def extract(
    content: bytes,
    *,
    kind: SourceKind,
    content_type: str,
    excerpts: ExcerptStore,
    untrusted: UntrustedTextRegistry,
    timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    require_os_isolation: bool | None = None,
    worker_path: Path = WORKER_PATH,
) -> ExtractionResult:
    """Extract structured fields from untrusted ``content`` in a sandboxed worker process.

    On success, excerpts are written to ``excerpts`` and every free-text field
    value and excerpt is registered in ``untrusted`` so it can be kept out of
    model context. ``worker_path`` exists for tests.
    """
    started = time.monotonic()
    kind = SourceKind(kind)
    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")
    source_hash = "sha256:" + hashlib.sha256(content).hexdigest()
    if require_os_isolation is None:
        require_os_isolation = _default_require_os_isolation()

    if len(content) > max_input_bytes:
        return _failed(kind, source_hash, ExtractionFailure.INPUT_TOO_LARGE, started, size=len(content))
    if content_type not in CONTENT_TYPES[kind]:
        return _failed(kind, source_hash, ExtractionFailure.UNSUPPORTED_CONTENT_TYPE, started)

    payload = json.dumps(
        {
            "v": _worker.PROTOCOL_VERSION,
            "kind": kind.value,
            "content_type": content_type,
            "content_b64": base64.b64encode(content).decode("ascii"),
        }
    ).encode("ascii")
    argv = [sys.executable, *_INTERPRETER_FLAGS, str(worker_path), "--cpu-seconds", str(max(1, int(timeout_s) + 1))]
    if require_os_isolation:
        argv.append("--require-os-isolation")

    run = await _run_worker(argv, payload, timeout_s=timeout_s, max_output_bytes=max_output_bytes)
    if run.start_failed:
        return _failed(kind, source_hash, ExtractionFailure.START_FAILED, started)
    if run.timed_out:
        return _failed(kind, source_hash, ExtractionFailure.TIMEOUT, started, timeout_s=timeout_s)
    if run.output_too_large:
        return _failed(kind, source_hash, ExtractionFailure.OUTPUT_TOO_LARGE, started)

    response, decode_error = _decode(run.stdout)
    if run.returncode != 0:
        reason = ExtractionFailure.CRASHED
        worker_reason = response.get("reason") if isinstance(response, dict) else None
        if response is not None and response.get("ok") is False and isinstance(worker_reason, str):
            if worker_reason in {r.value for r in WORKER_REASONS}:
                reason = ExtractionFailure(worker_reason)
        return _failed(kind, source_hash, reason, started, returncode=run.returncode, stderr_bytes=run.stderr_bytes)
    if decode_error is not None:
        return _failed(kind, source_hash, ExtractionFailure.OUTPUT_INVALID, started, detail=decode_error)

    try:
        fields, rejected, excerpt_items, isolation = validate_response(kind, response)
    except (OutputInvalidError, TypeError, ValueError, RecursionError) as exc:
        # A response that cannot even be compared against the schema is refused like any other mismatch.
        return _failed(kind, source_hash, ExtractionFailure.OUTPUT_INVALID, started, detail=type(exc).__name__)
    if require_os_isolation and "seccomp" not in isolation:
        return _failed(kind, source_hash, ExtractionFailure.ISOLATION_UNAVAILABLE, started, isolation=list(isolation))

    spec = FIELDS[kind]
    refs: dict[str, list[str]] = {}
    for field_name, text in excerpt_items:
        ref = excerpt_ref(kind.value, field_name, text)
        excerpts.put(Excerpt(excerpt_ref=ref, source_kind=kind.value, field=field_name, text=text))
        refs.setdefault(field_name, []).append(ref)
        # An excerpt of a constrained field is a recognised label and its validated
        # value, which the context may legitimately show; only free-text excerpts
        # are registered as untrusted.
        if spec[field_name].get("untrusted_text"):
            untrusted.register(text)
    for name, value in fields.items():
        if not spec[name].get("untrusted_text"):
            continue
        if isinstance(value, str):
            untrusted.register(value)
        elif isinstance(value, tuple):
            untrusted.register_all(value)

    _extraction_total.labels(kind=kind.value, outcome="ok").inc()
    _extraction_seconds.labels(kind=kind.value).observe(time.monotonic() - started)
    return ExtractionResult(
        kind=kind,
        ok=True,
        failure=None,
        source_hash=source_hash,
        fields=MappingProxyType(fields),
        rejected_fields=rejected,
        excerpt_refs=MappingProxyType({name: tuple(sorted(refs[name])) for name in sorted(refs)}),
        isolation=isolation,
    )


async def probe_isolation(
    *, timeout_s: float = DEFAULT_TIMEOUT_SECONDS, worker_path: Path = WORKER_PATH
) -> dict[str, Any]:
    """Start a worker that attempts network, process and file-write escapes and reports the outcome.

    Returns ``{"isolation": [...], "checks": {name: "denied:<error>" | "allowed"}}``.
    Raises ``RuntimeError`` if the probe itself does not complete.
    """
    argv = [sys.executable, *_INTERPRETER_FLAGS, str(worker_path), "--probe"]
    run = await _run_worker(
        argv, b"", timeout_s=timeout_s, max_output_bytes=DEFAULT_MAX_OUTPUT_BYTES, prepare=_prepare_probe
    )
    result, error = _decode(run.stdout)
    if run.returncode != 0 or result is None:
        raise RuntimeError(
            f"sandbox probe did not complete (returncode={run.returncode}, timed_out={run.timed_out}, error={error})"
        )
    return {"isolation": result["isolation"], "checks": result["checks"]}
