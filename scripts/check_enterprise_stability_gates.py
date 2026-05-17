#!/usr/bin/env python3
"""Enterprise stability release gates.

The gate blocks new production-code regressions while allowing known legacy
debt through a checked-in baseline. Update the baseline only as part of an
intentional hardening/rebaseline PR.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO_ROOT / "docs" / "enterprise_stability_gate_baseline.json"
DEFAULT_ROUTE_INVENTORY = REPO_ROOT / "docs" / "route_inventory.json"

PRODUCTION_PREFIXES = (
    "api/",
    "auth/",
    "bridge/",
    "connectors/",
    "core/",
    "scaling/",
    "workflows/",
)
STUB_PROTECTED_PREFIXES = (
    "api/v1/",
    "bridge/",
    "connectors/",
    "core/tasks/",
    "workflows/",
)
EXCLUDED_PREFIXES = (
    ".git/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "build/",
    "codex-pytest-basetemp/",
    "codex-pytest-cache/",
    "codex-pytest-temp/",
    "dist/",
    "docs/",
    "migrations/",
    "node_modules/",
    "tests/",
    "ui/dist/",
    "ui/node_modules/",
)
EXCLUDED_SUFFIXES = (".pyc",)

BROAD_EXCEPT_SLUG = "broad-except-ok"
PROCESS_LOCAL_SLUG = "process-local-ok"
STUB_SLUG = "stub-ok"
ROUTE_SLUG = "enterprise-route:"

BROAD_EXCEPT_GENERIC_REASON_PHRASES = {
    "safe",
    "fallback",
    "best effort",
    "best effort cleanup",
    "legacy",
    "handled",
    "cleanup",
}
BROAD_EXCEPT_GENERIC_REASON_WORDS = {
    "allowed",
    "best",
    "catch",
    "cleanup",
    "effort",
    "fallback",
    "handled",
    "legacy",
    "ok",
    "safe",
}
BROAD_EXCEPT_SAFETY_PROPERTIES = (
    "after callback verification",
    "after durable",
    "after run terminal",
    "authoritative",
    "before reconsent",
    "before tool failure",
    "cache",
    "cleanup only",
    "continues to next",
    "degrade",
    "degrades",
    "defaults safe false",
    "does not",
    "durable",
    "empty list",
    "ends in",
    "error count",
    "explicit",
    "expected cancellation",
    "fail",
    "failed",
    "failure",
    "fails",
    "false flag",
    "falls back to",
    "falls through",
    "fallbacks to",
    "fresh flow",
    "isolates",
    "keeps",
    "logged",
    "logs",
    "marked",
    "marks",
    "metadata",
    "no success",
    "noncritical",
    "nonfatal",
    "optional",
    "partial error count",
    "primary operation",
    "public status",
    "query params",
    "read model",
    "read only",
    "records",
    "reports",
    "requires",
    "reraises",
    "raises",
    "refuses",
    "revoke only",
    "retry",
    "retryable",
    "returns",
    "schema api",
    "sidecar",
    "skips",
    "stale cache",
    "static names",
    "token blacklist",
    "unavailable",
    "unhealthy",
    "unprocessed",
)

SUSPICIOUS_STATE_NAMES = {
    "_active_bridges",
    "_connections",
    "_event_store",
    "_pending_requests",
    "_seen_ids",
}
SUSPICIOUS_STATE_SUFFIXES = (
    "_cache",
    "_registry",
    "_map",
    "_maps",
    "_tokens",
    "_token",
    "_window",
    "_windows",
    "_bucket",
    "_buckets",
    "_buffer",
    "_buffers",
    "_queue",
    "_queues",
    "_pending",
    "_listeners",
    "_sessions",
    "_locks",
    "_states",
)
MUTABLE_CALLS = {
    "deque",
    "defaultdict",
    "dict",
    "LifoQueue",
    "list",
    "PriorityQueue",
    "Queue",
    "set",
    "SimpleQueue",
}
ROUTE_METHODS = {
    "get": ("GET",),
    "post": ("POST",),
    "put": ("PUT",),
    "patch": ("PATCH",),
    "delete": ("DELETE",),
    "options": ("OPTIONS",),
    "head": ("HEAD",),
    "websocket": ("WEBSOCKET",),
}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
STATUS_PATTERN = re.compile(
    r"([\"']status[\"']\s*:\s*[\"'](?P<dict_status>completed|sent|ok)[\"']|"
    r"\bstatus\s*=\s*[\"'](?P<kw_status>completed|sent|ok)[\"'])",
    re.IGNORECASE,
)
STUB_CONTEXT_PATTERN = re.compile(r"\b(stub|fake|empty)\b|[\"']output[\"']\s*:\s*(\{\}|None)", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    category: str
    path: str
    line: int
    code: str
    message: str
    key: str


@dataclass(frozen=True)
class RouteEntry:
    path: str
    methods: list[str]
    module: str
    function: str
    auth_required: bool | None
    tenant_required: bool | None
    scope: str | None
    public_exempt_reason: str | None
    idempotency: str | None
    rate_limit: str | None
    audit_event: str | None
    metadata_present: bool
    source_line: int

    @property
    def key(self) -> str:
        return f"{','.join(self.methods)} {self.path} {self.module}:{self.function}"


def _rel(path: Path, root: Path = REPO_ROOT) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _is_excluded(rel_path: str) -> bool:
    return rel_path.endswith(EXCLUDED_SUFFIXES) or rel_path.startswith(EXCLUDED_PREFIXES)


def _is_production_path(rel_path: str) -> bool:
    return rel_path.endswith(".py") and not _is_excluded(rel_path) and rel_path.startswith(PRODUCTION_PREFIXES)


def _is_stub_protected_path(rel_path: str) -> bool:
    return rel_path.endswith(".py") and not _is_excluded(rel_path) and rel_path.startswith(STUB_PROTECTED_PREFIXES)


def _git_tracked_python_files(root: Path) -> list[Path]:
    try:
        out = subprocess.check_output(["git", "ls-files", "*.py"], cwd=root, text=True)  # noqa: S603, S607
        paths = [root / line.strip() for line in out.splitlines() if line.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        paths = [p for p in root.rglob("*.py") if ".git" not in p.parts]
    return paths


def production_python_files(root: Path = REPO_ROOT) -> list[Path]:
    return [path for path in _git_tracked_python_files(root) if _is_production_path(_rel(path, root))]


def _source_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _normalize_reason(reason: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", reason.lower()).strip()


def _is_valid_broad_exception_reason(reason: str) -> bool:
    normalized = _normalize_reason(reason)
    if not normalized:
        return False
    if normalized in BROAD_EXCEPT_GENERIC_REASON_PHRASES:
        return False
    tokens = set(normalized.split())
    if tokens and tokens <= BROAD_EXCEPT_GENERIC_REASON_WORDS:
        return False
    return any(term in normalized for term in BROAD_EXCEPT_SAFETY_PROPERTIES)


def _annotation_reason(lines: list[str], line_number: int, slug: str) -> str | None:
    for idx in (line_number, line_number - 1):
        if idx < 1 or idx > len(lines):
            continue
        line = lines[idx - 1]
        marker = f"enterprise-gate: {slug}"
        if marker not in line:
            continue
        match = re.search(r"\breason=(?P<reason>.+?)(?:\s*$)", line)
        if not match:
            continue
        reason = match.group("reason").strip()
        if not reason:
            continue
        if slug == BROAD_EXCEPT_SLUG and not _is_valid_broad_exception_reason(reason):
            continue
        return reason
    return None


def _route_metadata(lines: list[str], start_line: int, end_line: int) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for idx in range(max(1, start_line - 2), min(len(lines), end_line) + 1):
        line = lines[idx - 1]
        if ROUTE_SLUG not in line:
            continue
        tail = line.split(ROUTE_SLUG, 1)[1].strip()
        for token in tail.split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            metadata[key.strip()] = value.strip()
    return metadata


def _route_meta_decorator_metadata(decorator: ast.AST) -> dict[str, str]:
    if not isinstance(decorator, ast.Call):
        return {}
    func = decorator.func
    name = ""
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    if name != "route_meta":
        return {}

    metadata: dict[str, str] = {}
    for kw in decorator.keywords:
        if not kw.arg:
            continue
        value = kw.value
        if isinstance(value, ast.Constant):
            if isinstance(value.value, bool):
                metadata[kw.arg] = "true" if value.value else "false"
            elif isinstance(value.value, str):
                metadata[kw.arg] = value.value
    return metadata


def _bool_value(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.lower()
    if lowered in {"1", "true", "yes"}:
        return True
    if lowered in {"0", "false", "no"}:
        return False
    return None


def _finding(category: str, path: str, line: int, code: str, message: str) -> Finding:
    digest = hashlib.sha1(f"{category}:{path}:{line}:{code}".encode()).hexdigest()[:12]
    return Finding(category=category, path=path, line=line, code=code, message=message, key=f"{category}:{path}:{line}:{digest}")


def _is_broad_exception(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    if isinstance(handler.type, ast.Name):
        return handler.type.id == "Exception"
    if isinstance(handler.type, ast.Attribute):
        return handler.type.attr == "Exception"
    if isinstance(handler.type, ast.Tuple):
        return any(
            isinstance(elt, ast.Name) and elt.id == "Exception"
            or isinstance(elt, ast.Attribute) and elt.attr == "Exception"
            for elt in handler.type.elts
        )
    return False


def scan_broad_exceptions(paths: list[Path], root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        rel_path = _rel(path, root)
        if not _is_production_path(rel_path):
            continue
        lines = _source_lines(path)
        try:
            tree = ast.parse("\n".join(lines), filename=rel_path)
        except SyntaxError as exc:
            findings.append(_finding("parse_error", rel_path, exc.lineno or 1, "syntax-error", str(exc)))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_broad_exception(node):
                if _annotation_reason(lines, node.lineno, BROAD_EXCEPT_SLUG):
                    continue
                code = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else "except Exception"
                findings.append(
                    _finding(
                        "broad_exception",
                        rel_path,
                        node.lineno,
                        code,
                        f"Broad exception handler needs `# enterprise-gate: {BROAD_EXCEPT_SLUG} reason=<short reason>`.",
                    )
                )
    return findings


def _target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for elt in target.elts:
            names.extend(_target_names(elt))
        return names
    return []


def _mutable_value(value: ast.AST | None) -> bool:
    if value is None:
        return False
    if isinstance(value, (ast.Dict, ast.List, ast.Set)):
        return True
    if isinstance(value, ast.Call):
        func = value.func
        if isinstance(func, ast.Name):
            return func.id in MUTABLE_CALLS
        if isinstance(func, ast.Attribute):
            return func.attr in MUTABLE_CALLS
    return False


def _suspicious_state_name(name: str) -> bool:
    lowered = name.lower()
    return lowered in SUSPICIOUS_STATE_NAMES or lowered.endswith(SUSPICIOUS_STATE_SUFFIXES)


def scan_process_local_state(paths: list[Path], root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        rel_path = _rel(path, root)
        if not _is_production_path(rel_path):
            continue
        lines = _source_lines(path)
        try:
            tree = ast.parse("\n".join(lines), filename=rel_path)
        except SyntaxError:
            continue
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign):
                names = [name for target in stmt.targets for name in _target_names(target)]
                value = stmt.value
            elif isinstance(stmt, ast.AnnAssign):
                names = _target_names(stmt.target)
                value = stmt.value
            else:
                continue
            if not _mutable_value(value):
                continue
            suspicious = [name for name in names if _suspicious_state_name(name)]
            if not suspicious:
                continue
            if _annotation_reason(lines, stmt.lineno, PROCESS_LOCAL_SLUG):
                continue
            findings.append(
                _finding(
                    "process_local_state",
                    rel_path,
                    stmt.lineno,
                    ", ".join(suspicious),
                    f"Process-local mutable state needs `# enterprise-gate: {PROCESS_LOCAL_SLUG} reason=<short reason>`.",
                )
            )
    return findings


def scan_stub_success(paths: list[Path], root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        rel_path = _rel(path, root)
        if not _is_stub_protected_path(rel_path):
            continue
        lines = _source_lines(path)
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _annotation_reason(lines, idx, STUB_SLUG):
                continue
            if "stub" in stripped.lower():
                findings.append(
                    _finding(
                        "stub_path",
                        rel_path,
                        idx,
                        stripped,
                        f"Production stub path needs `# enterprise-gate: {STUB_SLUG} reason=<short reason>`.",
                    )
                )
                continue
            if STATUS_PATTERN.search(stripped):
                window = "\n".join(lines[max(0, idx - 4): min(len(lines), idx + 4)])
                if STUB_CONTEXT_PATTERN.search(window):
                    findings.append(
                        _finding(
                            "stub_success_status",
                            rel_path,
                            idx,
                            stripped,
                            "Success status is adjacent to stub/fake/empty-output behavior.",
                        )
                    )
    return findings


def parse_alembic_heads(output: str) -> list[str]:
    heads: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "(head)" in stripped:
            heads.append(stripped.split()[0])
            continue
        if re.match(r"^[A-Za-z0-9_]+$", stripped):
            heads.append(stripped)
    return heads


def check_alembic_heads(root: Path = REPO_ROOT) -> tuple[list[Finding], list[str]]:
    try:
        output = subprocess.check_output(  # noqa: S603
            [sys.executable, "-m", "alembic", "heads"],
            cwd=root,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        finding = _finding("alembic_heads", "migrations", 0, "alembic-heads-error", exc.output.strip())
        return [finding], []
    heads = parse_alembic_heads(output)
    if len(heads) == 1:
        return [], heads
    message = (
        f"Alembic must have exactly one head; found {len(heads)}. "
        "Add an empty Alembic merge revision that merges the parallel heads."
    )
    return [_finding("alembic_heads", "migrations", 0, ",".join(heads) or "none", message)], heads


def _literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _literal_string_list(node: ast.AST | None) -> list[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [_literal_string(elt) for elt in node.elts]
        return [value for value in values if value]
    return []


def _router_prefixes(tree: ast.Module) -> dict[str, str]:
    prefixes: dict[str, str] = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not isinstance(stmt.value, ast.Call):
            continue
        func = stmt.value.func
        if not (isinstance(func, ast.Name) and func.id == "APIRouter"):
            continue
        prefix = ""
        for kw in stmt.value.keywords:
            if kw.arg == "prefix":
                prefix = _literal_string(kw.value) or ""
        for target in stmt.targets:
            for name in _target_names(target):
                prefixes[name] = prefix
    return prefixes


def _route_decorator_info(decorator: ast.AST) -> tuple[str, list[str], bool] | None:
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return None
    if not isinstance(func.value, ast.Name):
        return None
    attr = func.attr
    if attr not in ROUTE_METHODS and attr != "api_route":
        return None
    route_path = _literal_string(decorator.args[0]) if decorator.args else None
    if route_path is None:
        return None
    if attr == "api_route":
        methods: list[str] = []
        for kw in decorator.keywords:
            if kw.arg == "methods":
                methods = [method.upper() for method in _literal_string_list(kw.value)]
        methods = methods or ["GET"]
    else:
        methods = list(ROUTE_METHODS[attr])
    has_openapi_extra = any(kw.arg == "openapi_extra" for kw in decorator.keywords)
    return func.value.id, methods, has_openapi_extra


def _join_path(*parts: str) -> str:
    joined = "/" + "/".join(part.strip("/") for part in parts if part)
    joined = re.sub(r"/+", "/", joined)
    return joined.rstrip("/") or "/"


def _route_base_prefix(rel_path: str) -> str:
    return "/api/v1" if rel_path.startswith(("api/v1/", "api/websocket/", "bridge/")) else ""


def _infer_auth_required(path: str, methods: list[str]) -> bool | None:
    if "WEBSOCKET" in methods:
        return None
    if path in {
        "/api/v1/health",
        "/api/v1/health/liveness",
        "/api/v1/auth/login",
        "/api/v1/auth/google",
        "/api/v1/auth/config",
        "/api/v1/auth/signup",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/reset-password",
        "/api/v1/org/accept-invite",
        "/api/v1/demo-request",
        "/api/v1/billing/callback",
        "/api/v1/billing/callback/stripe",
        "/api/v1/oauth/callback",
        "/api/v1/billing/plans",
        "/api/v1/billing/health",
        "/api/v1/knowledge/health",
        "/api/v1/branding",
        "/api/v1/status",
        "/api/v1/product-facts",
    }:
        return False
    if path.startswith(("/api/v1/evals", "/api/v1/billing/webhook/", "/api/v1/auth/sso/")):
        return False
    return True


def _infer_public_reason(path: str, methods: list[str], auth_required: bool | None) -> str | None:
    if auth_required is not False:
        return None
    if "/health" in path:
        return "health-liveness-safe"
    if "/webhook" in path or "/callback" in path:
        return "webhook-or-callback-signature/state-protected"
    if "/auth/" in path or path.endswith("/auth/config"):
        return "auth-route"
    return "public-read-or-doc-route"


def scan_routes(paths: list[Path], root: Path = REPO_ROOT) -> list[RouteEntry]:
    routes: list[RouteEntry] = []
    for path in paths:
        rel_path = _rel(path, root)
        if not rel_path.endswith(".py") or not rel_path.startswith(("api/v1/", "api/websocket/", "bridge/")):
            continue
        if _is_excluded(rel_path):
            continue
        lines = _source_lines(path)
        try:
            tree = ast.parse("\n".join(lines), filename=rel_path)
        except SyntaxError:
            continue
        prefixes = _router_prefixes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                info = _route_decorator_info(decorator)
                if not info:
                    continue
                router_name, methods, has_openapi_extra = info
                route_prefix = prefixes.get(router_name, "")
                decorator_path = _literal_string(decorator.args[0]) if isinstance(decorator, ast.Call) and decorator.args else ""
                full_path = _join_path(_route_base_prefix(rel_path), route_prefix, decorator_path or "")
                first_decorator_line = min(getattr(dec, "lineno", node.lineno) for dec in node.decorator_list)
                metadata = _route_metadata(lines, first_decorator_line, node.lineno)
                for route_decorator in node.decorator_list:
                    metadata.update(_route_meta_decorator_metadata(route_decorator))
                metadata_present = bool(metadata) or has_openapi_extra
                auth_required = _bool_value(metadata.get("auth_required"))
                if auth_required is None:
                    auth_required = _infer_auth_required(full_path, methods)
                public_reason = metadata.get("public_reason") or _infer_public_reason(full_path, methods, auth_required)
                routes.append(
                    RouteEntry(
                        path=full_path,
                        methods=methods,
                        module=rel_path,
                        function=node.name,
                        auth_required=auth_required,
                        tenant_required=_bool_value(metadata.get("tenant_required")),
                        scope=metadata.get("scope"),
                        public_exempt_reason=public_reason,
                        idempotency=metadata.get("idempotency"),
                        rate_limit=metadata.get("rate_limit"),
                        audit_event=metadata.get("audit_event"),
                        metadata_present=metadata_present,
                        source_line=node.lineno,
                    )
                )
    return sorted(routes, key=lambda route: (route.path, route.methods, route.module, route.function))


def route_metadata_findings(routes: list[RouteEntry]) -> list[Finding]:
    findings: list[Finding] = []
    for route in routes:
        if route.metadata_present:
            continue
        methods = ",".join(route.methods)
        findings.append(
            _finding(
                "route_missing_metadata",
                route.module,
                route.source_line,
                f"{methods} {route.path}",
                "Route needs `@route_meta(...)` or `# enterprise-route: auth_required=<true|false> "
                "tenant_required=<true|false> scope=<...> idempotency=<...> rate_limit=<...> "
                "audit_event=<...>` near the decorator.",
            )
        )
        if route.auth_required is False and any(method in MUTATING_METHODS for method in route.methods):
            findings.append(
                _finding(
                    "public_mutating_route_missing_metadata",
                    route.module,
                    route.source_line,
                    f"{methods} {route.path}",
                    "Public/exempt mutating route needs an explicit public_reason risk annotation.",
                )
            )
    return findings


def load_baseline(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "allowed_findings": {}, "routes_missing_metadata": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _allowed_keys(baseline: dict[str, Any], category: str) -> set[str]:
    return set(baseline.get("allowed_findings", {}).get(category, []))


def filter_baselined(findings: list[Finding], baseline: dict[str, Any]) -> tuple[list[Finding], list[Finding]]:
    blocked: list[Finding] = []
    allowed: list[Finding] = []
    for finding in findings:
        if finding.key in _allowed_keys(baseline, finding.category):
            allowed.append(finding)
        else:
            blocked.append(finding)
    return blocked, allowed


def route_metadata_baseline_findings(
    baseline: dict[str, Any],
    baseline_path: Path = DEFAULT_BASELINE,
) -> list[Finding]:
    routes = baseline.get("routes_missing_metadata", [])
    if not routes:
        return []
    return [
        _finding(
            "route_metadata_baseline",
            _rel(baseline_path, REPO_ROOT),
            1,
            f"{len(routes)} route metadata baseline allowance(s)",
            "Route metadata baseline allowances are no longer permitted; annotate every route instead.",
        )
    ]


def collect_findings(paths: list[Path], root: Path = REPO_ROOT) -> tuple[list[Finding], list[str], list[RouteEntry]]:
    findings: list[Finding] = []
    findings.extend(scan_broad_exceptions(paths, root))
    findings.extend(scan_process_local_state(paths, root))
    findings.extend(scan_stub_success(paths, root))
    routes = scan_routes(paths, root)
    findings.extend(route_metadata_findings(routes))
    alembic_findings, heads = check_alembic_heads(root)
    findings.extend(alembic_findings)
    return findings, heads, routes


def build_baseline(findings: list[Finding]) -> dict[str, Any]:
    allowed: dict[str, list[str]] = {}
    for finding in findings:
        if finding.category in {"route_missing_metadata", "public_mutating_route_missing_metadata"}:
            continue
        else:
            allowed.setdefault(finding.category, []).append(finding.key)
    return {
        "version": 1,
        "description": "Baseline of existing enterprise stability gate debt. New unannotated findings fail CI.",
        "allowed_findings": {category: sorted(keys) for category, keys in sorted(allowed.items())},
        "routes_missing_metadata": [],
    }


def route_inventory_payload(routes: list[RouteEntry]) -> list[dict[str, Any]]:
    return [asdict(route) | {"key": route.key} for route in routes]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json_if_exists(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def print_findings(findings: list[Finding]) -> None:
    for finding in findings:
        location = finding.path if finding.line == 0 else f"{finding.path}:{finding.line}"
        print(f"::error file={finding.path},line={finding.line}::{finding.category}: {finding.message}")
        print(f"  - {location}: {finding.code}")


def scorecard(findings: list[Finding], allowed: list[Finding], blocked: list[Finding], heads: list[str], routes: list[RouteEntry]) -> dict[str, int]:
    counts = {
        "alembic_head_count": len(heads),
        "broad_exceptions_allowed": sum(f.category == "broad_exception" for f in allowed),
        "broad_exceptions_blocked": sum(f.category == "broad_exception" for f in blocked),
        "process_local_allowed": sum(f.category == "process_local_state" for f in allowed),
        "process_local_blocked": sum(f.category == "process_local_state" for f in blocked),
        "routes_missing_metadata": sum(f.category == "route_missing_metadata" for f in findings),
        "stub_paths_allowed": sum(f.category in {"stub_path", "stub_success_status"} for f in allowed),
        "stub_paths_blocked": sum(f.category in {"stub_path", "stub_success_status"} for f in blocked),
        "total_blocked": len(blocked),
        "total_routes": len(routes),
    }
    print("Enterprise stability scorecard:")
    for key in sorted(counts):
        print(f"  {key}: {counts[key]}")
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--route-inventory", type=Path, default=DEFAULT_ROUTE_INVENTORY)
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--write-route-inventory", action="store_true")
    parser.add_argument("--scorecard", action="store_true")
    args = parser.parse_args(argv)

    paths = production_python_files(REPO_ROOT)
    findings, heads, routes = collect_findings(paths, REPO_ROOT)
    route_payload = route_inventory_payload(routes)

    if args.update_baseline:
        _write_json(args.baseline, build_baseline(findings))
        print(f"Updated baseline: {_rel(args.baseline, REPO_ROOT)}")

    if args.write_route_inventory:
        _write_json(args.route_inventory, route_payload)
        print(f"Updated route inventory: {_rel(args.route_inventory, REPO_ROOT)}")

    baseline = load_baseline(args.baseline)
    findings.extend(route_metadata_baseline_findings(baseline, args.baseline))
    blocked, allowed = filter_baselined(findings, baseline)

    inventory_stale = False
    if args.route_inventory.exists() and not args.write_route_inventory:
        expected = _read_json_if_exists(args.route_inventory)
        inventory_stale = expected != route_payload
        if inventory_stale:
            print(
                "::error::Route inventory is stale. Run "
                "`python scripts/check_enterprise_stability_gates.py --write-route-inventory`.",
                file=sys.stderr,
            )

    scorecard(findings, allowed, blocked, heads, routes)

    if blocked:
        print_findings(blocked)
    if blocked or inventory_stale:
        print(
            "\nEnterprise stability gate failed. Add a local enterprise-gate annotation with a non-empty reason, "
            "or intentionally update docs/enterprise_stability_gate_baseline.json.",
            file=sys.stderr,
        )
        return 1

    print("Enterprise stability gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
