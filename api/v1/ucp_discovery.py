# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Public UCP business surfaces served on a merchant's verified serving host.

* ``GET /.well-known/ucp`` - the business profile for the newest configured
  UCP release; ``GET /.well-known/ucp/{version}`` - a leaf profile for a release
  listed in ``ucp.supported_versions``.
* ``POST /ucp/catalog/search`` and ``POST /ucp/catalog/lookup``
  (``dev.ucp.shopping.catalog.*``).
* ``POST /ucp/locations/search`` and ``POST /ucp/locations/lookup``
  (``dev.ucp.common.location.*``, 2026-08-25 only).

The merchant is chosen by the ``Host`` header alone: the host must be a
verified serving host (DNS attestation) of a merchant config that opted in
(published) and whose verified, fresh ``public_discovery_state`` artifact from
Grantex allows publication for that host; anything else is 404, as is
every route while ``AGENTICORG_UCP_PROFILE_ENABLED`` is off. No path or query
parameter selects a merchant, and no route redirects.
These routes are mounted at the application root (no ``/api/v1`` prefix) and
are exempt from tenant authentication: the profile and catalog are public
access level (UCP identity linking, Access levels).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.route_metadata import route_meta
from core.commerce.source_precedence import products_with_precedence
from core.commerce.ucp.authority import normalize_host
from core.commerce.ucp.catalog import (
    MAX_LOOKUP_IDS,
    catalog_unavailable_message,
    decode_cursor,
    lookup_catalog,
    not_found_message,
    paginate,
    price_filter_ignored_message,
    search_catalog,
    usable_artifact_ids,
    verified_catalog_source,
)
from core.commerce.ucp.locations import lookup_locations, parse_open_at, search_locations
from core.commerce.ucp.policies import map_oacp_policy_to_ucp_policies
from core.commerce.ucp.profile import (
    PROFILE_CACHE_CONTROL,
    RENDERERS,
    UcpMerchantServeState,
    build_profile,
    merchant_serve_state,
    profile_etag,
)
from core.commerce.ucp.registry import (
    CATALOG_LOOKUP,
    CATALOG_SEARCH,
    LOCATION_LOOKUP,
    LOCATION_SEARCH,
    ucp_registry,
)
from core.commerce.ucp.store import (
    find_serving_binding,
    load_catalog_inputs,
    load_merchant_config_row,
    load_shared_cache_records,
)
from core.commerce.ucp.versions import (
    UCP_2026_08_25,
    UcpAgentHeaderError,
    configured_ucp_versions,
    current_ucp_version,
    parse_ucp_agent,
)
from core.config import settings

well_known_router = APIRouter(tags=["UCP"])
ucp_router = APIRouter(prefix="/ucp", tags=["UCP"])

UCP_PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/.well-known/ucp",
        "/ucp/catalog/search",
        "/ucp/catalog/lookup",
        "/ucp/locations/search",
        "/ucp/locations/lookup",
    }
)
UCP_WELL_KNOWN_PREFIX = "/.well-known/ucp/"


class _UcpRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    context: dict[str, Any] | None = None
    signals: dict[str, Any] | None = None
    attribution: dict[str, Any] | None = None


class _PriceFilter(BaseModel):
    model_config = ConfigDict(extra="allow")

    min: int | None = Field(default=None, ge=0)
    max: int | None = Field(default=None, ge=0)


class _SearchFilters(BaseModel):
    model_config = ConfigDict(extra="allow")

    categories: list[str] | None = Field(default=None, max_length=20)
    price: _PriceFilter | None = None


class _Pagination(BaseModel):
    model_config = ConfigDict(extra="allow")

    cursor: str | None = Field(default=None, max_length=200)
    limit: int | None = Field(default=None, ge=1)


class CatalogSearchRequest(_UcpRequest):
    query: str | None = Field(default=None, max_length=512)
    filters: _SearchFilters | None = None
    pagination: _Pagination | None = None


class CatalogLookupRequest(_UcpRequest):
    ids: list[str] = Field(min_length=1, max_length=MAX_LOOKUP_IDS)
    filters: _SearchFilters | None = None


class _HoursFilter(BaseModel):
    model_config = ConfigDict(extra="allow")

    open_at: str = Field(max_length=64)


class _LocationFilters(BaseModel):
    model_config = ConfigDict(extra="allow")

    hours: _HoursFilter | None = None
    amenities: list[str] | None = Field(default=None, max_length=20)
    items: list[str] | None = Field(default=None, min_length=1, max_length=50)


class LocationSearchRequest(_UcpRequest):
    query: str | None = Field(default=None, max_length=512)
    distance: dict[str, Any] | None = None
    serves: dict[str, Any] | None = None
    filters: _LocationFilters | None = None
    pagination: _Pagination | None = None


class LocationLookupRequest(_UcpRequest):
    ids: list[str] = Field(min_length=1, max_length=MAX_LOOKUP_IDS)
    distance: dict[str, Any] | None = None
    serves: dict[str, Any] | None = None
    filters: _LocationFilters | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().isoformat().replace("+00:00", "Z")


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not Found")


async def _serving_merchant(request: Request) -> UcpMerchantServeState:
    """The published merchant on this request's ``Host``; 404 for anything else."""
    if not getattr(settings, "ucp_profile_enabled", False):
        raise _not_found()
    host = normalize_host(request.headers.get("host"))
    if host is None:
        raise _not_found()
    binding = await find_serving_binding(host)
    if binding is None:
        raise _not_found()
    row = await load_merchant_config_row(
        tenant_id=binding.tenant_id,
        merchant_id=binding.merchant_id,
        seller_agent_id=binding.seller_agent_id,
    )
    if row is None or str(row.tenant_id) != binding.tenant_id:
        raise _not_found()
    records = await load_shared_cache_records(
        tenant_id=binding.tenant_id, merchant_id=binding.merchant_id, seller_agent_id=binding.seller_agent_id
    )
    live = usable_artifact_ids(cache_records=records, now_iso=_now_iso(), artifact_type="public_discovery")
    serve = merchant_serve_state(row, host=host, now=_now(), live_public_discovery_ids=live)
    if serve is None:
        raise _not_found()
    return serve


def _error_body(version: str, code: str, content: str) -> dict[str, Any]:
    return {
        "ucp": {"version": version, "status": "error"},
        "messages": [{"type": "error", "code": code, "content": content, "severity": "unrecoverable"}],
    }


def _request_version(request: Request, versions: tuple[str, ...]) -> str | JSONResponse:
    """The UCP release the platform declares (``UCP-Agent`` ``version`` parameter), default the current one."""
    current = current_ucp_version(versions)
    try:
        agent = parse_ucp_agent(request.headers.get("ucp-agent"))
    except UcpAgentHeaderError:
        return JSONResponse(
            status_code=400,
            content=_error_body(current, "invalid_profile_url", "UCP-Agent is not a valid RFC 8941 dictionary."),
        )
    if agent is None or agent.version is None:
        return current
    if agent.version not in versions:
        return JSONResponse(
            status_code=422,
            content=_error_body(
                current,
                "version_unsupported",
                f"Version {agent.version} is not supported. This business implements {', '.join(versions)}.",
            ),
        )
    return agent.version


def _capabilities_incompatible(version: str, capability: str) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content=_error_body(version, "capabilities_incompatible", f"{capability} is not served at {version}."),
    )


def _served(serve: UcpMerchantServeState, version: str, capability: str) -> bool:
    return capability in ucp_registry.served_capability_names(serve.context(version))


def _profile_response(request: Request, document: Mapping[str, Any]) -> Response:
    etag = profile_etag(document)
    headers = {"Cache-Control": PROFILE_CACHE_CONTROL, "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=dict(document), headers=headers)


def _invalid_request(version: str, exc: ValidationError) -> JSONResponse:
    del exc  # validation detail is not echoed (it may contain request content)
    return JSONResponse(
        status_code=400, content=_error_body(version, "invalid_request", "The request body is invalid.")
    )


async def _json_body(request: Request) -> Any:
    try:
        return await request.json()
    except ValueError:
        return None


@well_known_router.get("/.well-known/ucp")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:ucp.profile.read",
    rate_limit="ucp-discovery",
    idempotency="read-only",
    audit_event="none-public-ucp-profile",
    public_reason="ucp-business-profile-public-by-spec-host-bound",
)
async def ucp_business_profile(request: Request) -> Response:
    serve = await _serving_merchant(request)
    versions = configured_ucp_versions()
    document = build_profile(serve, version=current_ucp_version(versions), versions=versions)
    return _profile_response(request, document)


@well_known_router.get("/.well-known/ucp/{version}")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:ucp.profile.read",
    rate_limit="ucp-discovery",
    idempotency="read-only",
    audit_event="none-public-ucp-profile",
    public_reason="ucp-leaf-profile-public-by-spec-host-bound",
)
async def ucp_leaf_profile(version: str, request: Request) -> Response:
    serve = await _serving_merchant(request)
    versions = configured_ucp_versions()
    if version not in versions or version == current_ucp_version(versions):
        raise _not_found()
    return _profile_response(request, build_profile(serve, version=version, versions=versions))


def _provider_policy(serve: Any) -> Mapping[str, Any] | None:
    """The merchant's ``provider_policy`` (source precedence and saved config-only facts)."""
    policy = (getattr(serve, "config", None) or {}).get("provider_policy")
    return policy if isinstance(policy, Mapping) else None


@ucp_router.post("/catalog/search")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:ucp.catalog.read",
    rate_limit="ucp-catalog-read",
    idempotency="read-only",
    audit_event="none-public-ucp-catalog-search",
    public_reason="ucp-catalog-public-access-level-verified-artifacts-only",
)
async def ucp_catalog_search(request: Request) -> JSONResponse:
    serve = await _serving_merchant(request)
    versions = configured_ucp_versions()
    version = _request_version(request, versions)
    if isinstance(version, JSONResponse):
        return version
    try:
        body = CatalogSearchRequest.model_validate(await _json_body(request) or {})
    except ValidationError as exc:
        return _invalid_request(version, exc)
    if not _served(serve, version, CATALOG_SEARCH):
        return _capabilities_incompatible(version, CATALOG_SEARCH)
    offset = decode_cursor(body.pagination.cursor if body.pagination else None)
    if offset is None:
        return JSONResponse(status_code=400, content=_error_body(version, "invalid_cursor", "The cursor is invalid."))
    records, products = await load_catalog_inputs(
        tenant_id=serve.tenant_id, merchant_id=serve.merchant_id, seller_agent_id=serve.seller_agent_id
    )
    now_iso = _now_iso()
    source = verified_catalog_source(cache_records=records, products=products, now_iso=now_iso)
    messages: list[dict[str, Any]] = []
    pagination: dict[str, Any] | None
    page: list[Any]
    if source.unavailable_reason is not None:
        page, pagination = [], {"has_next_page": False}
        messages.append(catalog_unavailable_message())
    else:
        filters = body.filters
        price = filters.price if filters else None
        context_currency = (body.context or {}).get("currency")
        currency = context_currency.upper() if isinstance(context_currency, str) else None
        if price is not None and (price.min is not None or price.max is not None) and currency is None:
            messages.append(price_filter_ignored_message())
        results = search_catalog(
            products_with_precedence(source.products, _provider_policy(serve)),
            query=body.query,
            categories=filters.categories if filters else None,
            price_min=price.min if price else None,
            price_max=price.max if price else None,
            price_currency=currency,
        )
        page, pagination = paginate(results, offset=offset, limit=body.pagination.limit if body.pagination else None)
    policies = _catalog_policies(serve, records, now_iso) if version == UCP_2026_08_25 and page else []
    return JSONResponse(
        content=RENDERERS[version].render_catalog_response(
            capability=CATALOG_SEARCH, products=page, messages=messages, pagination=pagination, policies=policies
        )
    )


@ucp_router.post("/catalog/lookup")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:ucp.catalog.read",
    rate_limit="ucp-catalog-read",
    idempotency="read-only",
    audit_event="none-public-ucp-catalog-lookup",
    public_reason="ucp-catalog-public-access-level-verified-artifacts-only",
)
async def ucp_catalog_lookup(request: Request) -> JSONResponse:
    serve = await _serving_merchant(request)
    versions = configured_ucp_versions()
    version = _request_version(request, versions)
    if isinstance(version, JSONResponse):
        return version
    try:
        body = CatalogLookupRequest.model_validate(await _json_body(request) or {})
    except ValidationError as exc:
        return _invalid_request(version, exc)
    if not _served(serve, version, CATALOG_LOOKUP):
        return _capabilities_incompatible(version, CATALOG_LOOKUP)
    records, products = await load_catalog_inputs(
        tenant_id=serve.tenant_id, merchant_id=serve.merchant_id, seller_agent_id=serve.seller_agent_id
    )
    now_iso = _now_iso()
    source = verified_catalog_source(cache_records=records, products=products, now_iso=now_iso)
    messages: list[dict[str, Any]] = []
    if source.unavailable_reason is not None:
        results: list[dict[str, Any]] = []
        messages.append(catalog_unavailable_message())
    else:
        results, missing = lookup_catalog(products_with_precedence(source.products, _provider_policy(serve)), body.ids)
        messages.extend(not_found_message(index) for index in missing)
    policies = _catalog_policies(serve, records, now_iso) if version == UCP_2026_08_25 and results else []
    return JSONResponse(
        content=RENDERERS[version].render_catalog_response(
            capability=CATALOG_LOOKUP, products=results, messages=messages, pagination=None, policies=policies
        )
    )


def _catalog_policies(serve: UcpMerchantServeState, records: list[Any], now_iso: str) -> list[dict[str, Any]]:
    """``policies[]`` from the policy snapshot taken at verification, while its artifact stays usable."""
    snapshot = serve.ucp.get("verified_policy")
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("policy"), Mapping):
        return []
    usable = usable_artifact_ids(cache_records=records, now_iso=now_iso, artifact_type="policy")
    if snapshot.get("artifact_id") not in usable:
        return []
    return map_oacp_policy_to_ucp_policies(snapshot["policy"], vendor_namespace=serve.namespace)


def _unsupported_location_predicate(body: LocationSearchRequest | LocationLookupRequest) -> str | None:
    if body.distance is not None:
        return "distance"
    if body.serves is not None:
        return "serves"
    if body.filters is not None and body.filters.items is not None:
        return "filters.items"
    return None


@ucp_router.post("/locations/search")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:ucp.location.read",
    rate_limit="ucp-catalog-read",
    idempotency="read-only",
    audit_event="none-public-ucp-location-search",
    public_reason="ucp-location-public-store-hours-and-address-only",
)
async def ucp_location_search(request: Request) -> JSONResponse:
    serve = await _serving_merchant(request)
    versions = configured_ucp_versions()
    version = _request_version(request, versions)
    if isinstance(version, JSONResponse):
        return version
    try:
        body = LocationSearchRequest.model_validate(await _json_body(request) or {})
    except ValidationError as exc:
        return _invalid_request(version, exc)
    if version != UCP_2026_08_25 or not _served(serve, version, LOCATION_SEARCH):
        return _capabilities_incompatible(version, LOCATION_SEARCH)
    unsupported = _unsupported_location_predicate(body)
    if unsupported is not None:
        return JSONResponse(
            content=_error_body(version, "unsupported_filter", f"{unsupported} cannot be evaluated by this business.")
        )
    open_at = None
    if body.filters is not None and body.filters.hours is not None:
        try:
            open_at = parse_open_at(body.filters.hours.open_at)
        except ValueError:
            return JSONResponse(status_code=400, content=_error_body(version, "invalid_request", "open_at is invalid."))
    offset = decode_cursor(body.pagination.cursor if body.pagination else None)
    if offset is None:
        return JSONResponse(status_code=400, content=_error_body(version, "invalid_cursor", "The cursor is invalid."))
    matches = search_locations(
        serve.locations,
        query=body.query,
        open_at=open_at,
        amenities=body.filters.amenities if body.filters else None,
    )
    page, pagination = paginate(list(matches), offset=offset, limit=body.pagination.limit if body.pagination else None)
    return JSONResponse(
        content=RENDERERS[version].render_location_response(
            capability=LOCATION_SEARCH, locations=[dict(item) for item in page], messages=[], pagination=pagination
        )
    )


@ucp_router.post("/locations/lookup")
@route_meta(
    auth_required=False,
    tenant_required=False,
    scope="public:ucp.location.read",
    rate_limit="ucp-catalog-read",
    idempotency="read-only",
    audit_event="none-public-ucp-location-lookup",
    public_reason="ucp-location-public-store-hours-and-address-only",
)
async def ucp_location_lookup(request: Request) -> JSONResponse:
    serve = await _serving_merchant(request)
    versions = configured_ucp_versions()
    version = _request_version(request, versions)
    if isinstance(version, JSONResponse):
        return version
    try:
        body = LocationLookupRequest.model_validate(await _json_body(request) or {})
    except ValidationError as exc:
        return _invalid_request(version, exc)
    if version != UCP_2026_08_25 or not _served(serve, version, LOCATION_LOOKUP):
        return _capabilities_incompatible(version, LOCATION_LOOKUP)
    unsupported = _unsupported_location_predicate(body)
    if unsupported is not None:
        return JSONResponse(
            content=_error_body(version, "unsupported_filter", f"{unsupported} cannot be evaluated by this business.")
        )
    candidates = list(serve.locations)
    if body.filters is not None and (body.filters.hours is not None or body.filters.amenities):
        try:
            open_at = parse_open_at(body.filters.hours.open_at) if body.filters.hours is not None else None
        except ValueError:
            return JSONResponse(status_code=400, content=_error_body(version, "invalid_request", "open_at is invalid."))
        candidates = [
            dict(item)
            for item in search_locations(candidates, query=None, open_at=open_at, amenities=body.filters.amenities)
        ]
    found, missing = lookup_locations(candidates, body.ids)
    messages = [
        {"type": "info", "code": "not_found", "content": f"No location matches requested identifier {index + 1}."}
        for index in missing
    ]
    return JSONResponse(
        content=RENDERERS[version].render_location_response(
            capability=LOCATION_LOOKUP, locations=found, messages=messages, pagination=None
        )
    )
