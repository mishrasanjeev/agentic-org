"""Route metadata annotations for enterprise stability gates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TypeVar

F = TypeVar("F", bound=Callable[..., object])

ROUTE_METADATA_ATTR = "__enterprise_route_metadata__"


@dataclass(frozen=True, slots=True)
class RouteMetadata:
    auth_required: bool
    tenant_required: bool
    scope: str | None = None
    rate_limit: str | None = None
    idempotency: str | None = None
    audit_event: str | None = None
    public_reason: str | None = None


def route_meta(
    *,
    auth_required: bool,
    tenant_required: bool,
    scope: str | None = None,
    rate_limit: str | None = None,
    idempotency: str | None = None,
    audit_event: str | None = None,
    public_reason: str | None = None,
) -> Callable[[F], F]:
    """Attach enterprise route metadata without changing handler behavior."""

    metadata = RouteMetadata(
        auth_required=auth_required,
        tenant_required=tenant_required,
        scope=scope,
        rate_limit=rate_limit,
        idempotency=idempotency,
        audit_event=audit_event,
        public_reason=public_reason,
    )

    def decorator(func: F) -> F:
        setattr(func, ROUTE_METADATA_ATTR, asdict(metadata))
        return func

    return decorator
