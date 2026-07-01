"""Path gates for operator-supplied local artifact reads and writes."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path


class ArtifactPathError(ValueError):
    """Raised when an artifact path escapes its intended workspace."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _norm(path: Path) -> str:
    return os.path.normcase(str(path))


def _same_path(left: Path, right: Path) -> bool:
    return _norm(left) == _norm(right)


def _under_or_same(path: Path, root: Path) -> bool:
    path_text = _norm(path)
    root_text = _norm(root)
    return path_text == root_text or path_text.startswith(root_text + os.sep)


def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _has_reparse_component(path: Path, *, repo_root: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if not _under_or_same(current, repo_root):
            continue
        if current.exists() and _is_reparse_point(current):
            return True
    return False


def _reject_drive_relative_or_rooted(path: Path, *, field_name: str) -> None:
    if path.drive and not path.is_absolute():
        raise ArtifactPathError(
            f"{field_name}_path_unanchored",
            f"{field_name} must not use a drive-relative path.",
        )
    if path.root and not path.is_absolute():
        raise ArtifactPathError(
            f"{field_name}_path_unanchored",
            f"{field_name} must not use a rooted path without a drive.",
        )


def _safe_allowed_root(root: Path, *, repo_root: Path, field_name: str) -> Path:
    root_path = root if root.is_absolute() else repo_root / root
    resolved_root = root_path.resolve()
    if not _under_or_same(resolved_root, repo_root):
        raise ArtifactPathError(
            f"{field_name}_root_outside_repo",
            f"{field_name} allowed root resolves outside the repository.",
        )
    if _has_reparse_component(root_path, repo_root=repo_root):
        raise ArtifactPathError(
            f"{field_name}_root_reparse_point",
            f"{field_name} allowed root must not contain a symlink or junction.",
        )
    return resolved_root


def resolve_repo_artifact_path(
    path: str | Path,
    *,
    repo_root: Path,
    allowed_roots: Sequence[Path],
    field_name: str,
    outside_reason: str,
    allowed_suffixes: Sequence[str] | None = None,
    direct_child: bool = True,
) -> Path:
    """Resolve a local artifact path under explicit repository-owned roots."""

    raw_path = str(path or "").strip()
    if not raw_path:
        raise ArtifactPathError(f"{field_name}_path_missing", f"{field_name} path is required.")

    repo = repo_root.resolve()
    candidate = Path(raw_path)
    _reject_drive_relative_or_rooted(candidate, field_name=field_name)
    if not candidate.is_absolute():
        candidate = repo / candidate

    try:
        resolved = candidate.resolve()
    except (OSError, ValueError) as exc:
        raise ArtifactPathError(
            f"{field_name}_path_unresolvable",
            f"{field_name} path could not be resolved.",
        ) from exc

    if _has_reparse_component(candidate, repo_root=repo):
        raise ArtifactPathError(
            f"{field_name}_path_reparse_point",
            f"{field_name} path must not contain a symlink or junction.",
        )

    if allowed_suffixes is not None:
        suffixes = {suffix.lower() for suffix in allowed_suffixes}
        if resolved.suffix.lower() not in suffixes:
            raise ArtifactPathError(
                f"{field_name}_path_extension_refused",
                f"{field_name} must use one of these suffixes: {sorted(suffixes)}.",
            )

    safe_roots = tuple(
        _safe_allowed_root(root, repo_root=repo, field_name=field_name)
        for root in allowed_roots
    )
    for root in safe_roots:
        if direct_child:
            if _same_path(resolved.parent, root):
                return resolved
        elif _under_or_same(resolved, root) and not _same_path(resolved, root):
            return resolved

    raise ArtifactPathError(
        f"{field_name}_{outside_reason}",
        f"{field_name} must stay under an allowed repository artifact root.",
    )


def _reject_unsafe_write_target(target: Path, *, repo_root: Path | None = None) -> None:
    if repo_root is not None and _has_reparse_component(target.parent, repo_root=repo_root.resolve()):
        raise ArtifactPathError(
            "artifact_parent_reparse_point",
            "Artifact parent must not contain a symlink or junction.",
        )
    if _is_reparse_point(target):
        raise ArtifactPathError(
            "artifact_target_reparse_point",
            "Artifact target must not be a symlink or junction.",
        )


def atomic_write_text_artifact(
    path: str | Path,
    content: str,
    *,
    encoding: str = "utf-8",
    repo_root: Path | None = None,
) -> None:
    """Write text through a temp file in the already-validated target directory."""

    target = Path(path)
    _reject_unsafe_write_target(target, repo_root=repo_root)

    target.parent.mkdir(parents=True, exist_ok=True)
    _reject_unsafe_write_target(target, repo_root=repo_root)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding=encoding,
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(content)
        _reject_unsafe_write_target(target, repo_root=repo_root)
        os.replace(temp_name, target)
        temp_name = ""
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
