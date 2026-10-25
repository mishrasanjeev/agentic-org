# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The AgenticOrg shopping network ("Sell everywhere").

A merchant turns the network on once. AgenticOrg then lists the shop in a public directory that
the hosted MCP server (ChatGPT, Claude, any MCP app) and the public Telegram bot read, and opens
checkouts there for the AgenticOrg network platform through the network checkout gateway
(``api.v1.network``). A shop with no host of its own gets a platform subdomain
``<slug>.<platform apex>`` served through the Firebase Hosting front, at most
``platform_subdomain_cap`` of them (Firebase's per-apex limit).

A listing never makes a shop public by itself: the directory and the gateway only serve a shop
whose UCP profile is published on an attested host (``core.commerce.ucp.state``), so the
Grantex authority and publication gates stay in force.
"""

from __future__ import annotations

import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select

from core.commerce import custom_domains as cd
from core.commerce.ucp.state import attested_host, is_published, ucp_state
from core.commerce.ucp.store import find_serving_binding
from core.config import settings
from core.database import get_platform_session, get_tenant_session
from core.models.commerce_c6z_runtime import C6ZMerchantCommerceConfigRow
from core.models.commerce_network import CommerceNetworkListingRow
from core.models.merchant_custom_domain import INACTIVE_STATUSES, MerchantCustomDomainRow

logger = structlog.get_logger()

SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38})[a-z0-9]$")
# Names that would collide with AgenticOrg's own hosts or read as official pages.
RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        "admin",
        "api",
        "app",
        "assets",
        "auth",
        "blog",
        "cdn",
        "demo",
        "dev",
        "docs",
        "ftp",
        "grantex",
        "help",
        "login",
        "mail",
        "mcp",
        "network",
        "ns1",
        "ns2",
        "shop",
        "shops",
        "smtp",
        "staging",
        "static",
        "status",
        "support",
        "test",
        "www",
    }
)
DIRECTORY_TTL_SECONDS = 60.0


class NetworkError(Exception):
    """A refusal with a stable code for the API layer."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class ShopRef:
    slug: str
    tenant_id: str
    merchant_id: str
    seller_agent_id: str
    host: str
    display_name: str


def network_enabled() -> bool:
    return bool(settings.network_enabled)


def _now() -> datetime:
    return datetime.now(UTC)


def slugify(name: str) -> str:
    """A slug proposal from a display name: ASCII, lowercase, words joined by hyphens, 3-40 characters."""
    ascii_name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:40].strip("-")
    return slug if len(slug) >= 3 else (slug + "-shop").strip("-")


def slug_problem(slug: str) -> str | None:
    """The refusal code for an unusable slug, or None."""
    if not SLUG_PATTERN.match(slug) or "--" in slug:
        return "invalid_slug"
    if slug in RESERVED_SLUGS:
        return "reserved_slug"
    return None


def published_host(config: C6ZMerchantCommerceConfigRow) -> str | None:
    """The host the shop's UCP profile is published on, or None while it is not published."""
    state = ucp_state(config.public_publishing)
    return attested_host(state) if is_published(state) else None


def telegram_link(slug: str) -> str | None:
    bot = (settings.network_telegram_bot_username or "").strip().lstrip("@")
    return f"https://t.me/{bot}?start={slug}" if bot else None


def platform_apex() -> str:
    return (settings.platform_subdomain_apex or "").strip().lower().rstrip(".")


def platform_subdomain_for(slug: str) -> str | None:
    apex = platform_apex()
    return f"{slug}.{apex}" if apex else None


# --- listings (tenant session) ----------------------------------------------------------------


async def get_listing(
    session: Any, *, tenant_id: str, merchant_id: str, seller_agent_id: str
) -> CommerceNetworkListingRow | None:
    return (
        await session.scalars(
            select(CommerceNetworkListingRow).where(
                CommerceNetworkListingRow.tenant_id == uuid.UUID(tenant_id),
                CommerceNetworkListingRow.merchant_id == merchant_id,
                CommerceNetworkListingRow.seller_agent_id == seller_agent_id,
            )
        )
    ).first()


async def slug_taken(slug: str, *, tenant_id: str, merchant_id: str, seller_agent_id: str) -> bool:
    """Whether another shop, in any tenant, already holds ``slug`` (platform context read)."""
    async with get_platform_session() as session:
        row = (
            await session.execute(
                select(
                    CommerceNetworkListingRow.tenant_id,
                    CommerceNetworkListingRow.merchant_id,
                    CommerceNetworkListingRow.seller_agent_id,
                ).where(CommerceNetworkListingRow.slug == slug)
            )
        ).first()
    if row is None:
        return False
    return (str(row[0]), row[1], row[2]) != (tenant_id, merchant_id, seller_agent_id)


async def save_listing(
    *,
    tenant_id: str,
    config: C6ZMerchantCommerceConfigRow,
    listed: bool,
    slug: str | None,
    actor_label: str,
) -> CommerceNetworkListingRow:
    """Turn the network on (accepting the current terms) or pause it; the slug is fixed once chosen."""
    seller = config.seller_agent_id or ""
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await get_listing(session, tenant_id=tenant_id, merchant_id=config.merchant_id, seller_agent_id=seller)
    if row is not None and slug and slug != row.slug:
        raise NetworkError("slug_fixed", "The shop's network name cannot change once chosen.")
    chosen = row.slug if row is not None else (slug or slugify(config.merchant_display_name or config.merchant_id))
    problem = slug_problem(chosen)
    if problem:
        raise NetworkError(problem, "Choose 3-40 lowercase letters, digits or hyphens that are not reserved.", 422)
    if row is None and await slug_taken(
        chosen, tenant_id=tenant_id, merchant_id=config.merchant_id, seller_agent_id=seller
    ):
        raise NetworkError("slug_taken", "Another shop already uses this name; choose another.")
    now = _now()
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        row = await get_listing(session, tenant_id=tenant_id, merchant_id=config.merchant_id, seller_agent_id=seller)
        if row is None:
            row = CommerceNetworkListingRow(
                id=uuid.uuid4(),
                tenant_id=uuid.UUID(tenant_id),
                merchant_id=config.merchant_id,
                seller_agent_id=seller,
                slug=chosen,
                created_at=now,
            )
            session.add(row)
        row.display_name = (config.merchant_display_name or config.merchant_id)[:200]
        row.serving_host = published_host(config) or row.serving_host
        row.status = "listed" if listed else "paused"
        if listed:
            row.terms_version = settings.network_terms_version
            row.opted_in_by = actor_label[:255]
            row.opted_in_at = now
        elif row.opted_in_at is None:
            raise NetworkError("not_listed", "The shop is not in the network.", 404)
        row.updated_at = now
        await session.flush()
    reset_directory_cache()
    return row


# --- platform subdomains --------------------------------------------------------------------


async def platform_subdomains_in_use() -> int:
    """Claimed Firebase hosts directly under the platform apex, across every tenant."""
    apex = platform_apex()
    async with get_platform_session() as session:
        count = await session.scalar(
            select(func.count(MerchantCustomDomainRow.id)).where(
                MerchantCustomDomainRow.provider == "firebase_hosting",
                MerchantCustomDomainRow.host.like(f"%.{apex}"),
                MerchantCustomDomainRow.status.not_in(INACTIVE_STATUSES),
            )
        )
    return int(count or 0)


async def claim_platform_subdomain(
    *,
    tenant_id: str,
    config: C6ZMerchantCommerceConfigRow,
    listing: CommerceNetworkListingRow,
    actor: dict[str, str],
    actor_label: str,
) -> str:
    """Give a listed shop with no host of its own ``<slug>.<apex>``; returns the host.

    The host is attested as the shop's serving host (AgenticOrg owns the apex) and its domain row
    goes straight to ``pending_mapping``: the custom-domain status task then creates the Firebase
    Hosting domain and follows the certificate to live. Refused when the platform front is off,
    the shop already has a domain row, or the cap is reached.
    """
    from api.v1.commerce_ucp_publishing import attest_platform_subdomain  # noqa: PLC0415 - avoids a cycle

    if not (cd.custom_domains_enabled() and cd.firebase_enabled() and platform_apex()):
        raise NetworkError("platform_subdomains_unavailable", "Platform subdomains are not available yet.", 503)
    host = platform_subdomain_for(listing.slug)
    assert host is not None
    seller = config.seller_agent_id or ""
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        existing = (
            await session.scalars(
                select(MerchantCustomDomainRow).where(
                    MerchantCustomDomainRow.tenant_id == uuid.UUID(tenant_id),
                    MerchantCustomDomainRow.merchant_id == config.merchant_id,
                    MerchantCustomDomainRow.seller_agent_id == seller,
                    MerchantCustomDomainRow.status.not_in(INACTIVE_STATUSES),
                )
            )
        ).first()
    if existing is not None:
        if existing.host == host:
            return host
        raise NetworkError("domain_exists", f"The shop already uses {existing.host}.")
    if await platform_subdomains_in_use() >= int(settings.platform_subdomain_cap):
        raise NetworkError(
            "platform_subdomain_cap_reached",
            "All platform addresses are taken for now; connect your own domain instead.",
        )
    await attest_platform_subdomain(
        tenant_id=tenant_id,
        merchant_id=config.merchant_id,
        seller_agent_id=config.seller_agent_id or None,
        host=host,
        platform_apex=platform_apex(),
        actor=actor,
    )
    now = _now()
    async with get_tenant_session(uuid.UUID(tenant_id)) as session:
        session.add(
            MerchantCustomDomainRow(
                id=uuid.uuid4(),
                tenant_id=uuid.UUID(tenant_id),
                merchant_id=config.merchant_id,
                seller_agent_id=seller,
                host=host,
                status="pending_mapping",
                provider="firebase_hosting",
                cname_target=cd.cname_target(),
                created_by=actor_label[:255],
                check_count=0,
                verified_at=now,
                mapping_requested_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        stored = await session.get(CommerceNetworkListingRow, listing.id)
        if stored is not None:
            stored.subdomain_host = host
            stored.updated_at = now
    logger.info("network_platform_subdomain_claimed", slug=listing.slug)
    return host


# --- the directory and shop resolution (platform context) ------------------------------------

# enterprise-gate: process-local-ok reason=directory cache expires on its ttl; the database is the source of truth
_DIRECTORY_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def reset_directory_cache() -> None:
    _DIRECTORY_CACHE.clear()


async def _config_for(listing: CommerceNetworkListingRow) -> C6ZMerchantCommerceConfigRow | None:
    async with get_tenant_session(listing.tenant_id) as session:
        return (
            await session.scalars(
                select(C6ZMerchantCommerceConfigRow)
                .where(
                    C6ZMerchantCommerceConfigRow.tenant_id == str(listing.tenant_id),
                    C6ZMerchantCommerceConfigRow.merchant_id == listing.merchant_id,
                    C6ZMerchantCommerceConfigRow.seller_agent_id == listing.seller_agent_id,
                )
                .order_by(C6ZMerchantCommerceConfigRow.created_at.desc())
            )
        ).first()


async def _servable_host(listing: CommerceNetworkListingRow, config: C6ZMerchantCommerceConfigRow | None) -> str | None:
    """The published host that the public host index still maps to this exact shop, else None."""
    if config is None or config.status == "disabled":
        return None
    host = published_host(config)
    if host is None:
        return None
    binding = await find_serving_binding(host)
    if binding is None or binding.tenant_id != str(listing.tenant_id) or binding.merchant_id != listing.merchant_id:
        return None
    return host


async def _listed_rows(slug: str | None = None) -> list[CommerceNetworkListingRow]:
    query = select(CommerceNetworkListingRow).where(CommerceNetworkListingRow.status == "listed")
    if slug is not None:
        query = query.where(CommerceNetworkListingRow.slug == slug)
    async with get_platform_session() as session:
        return list((await session.scalars(query.order_by(CommerceNetworkListingRow.opted_in_at))).all())


async def directory() -> list[dict[str, Any]]:
    """Every listed shop that is servable right now (cached for a minute)."""
    cached = _DIRECTORY_CACHE.get("all")
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return cached[1]
    entries: list[dict[str, Any]] = []
    for listing in await _listed_rows():
        config = await _config_for(listing)
        host = await _servable_host(listing, config)
        if host is None:
            continue
        categories = (
            config.commerce_categories if config is not None and isinstance(config.commerce_categories, list) else []
        )
        entries.append(
            {
                "slug": listing.slug,
                "name": listing.display_name,
                "categories": [str(c) for c in categories][:10],
                "host": host,
                "profile_url": f"https://{host}/.well-known/ucp",
                "telegram_link": telegram_link(listing.slug),
            }
        )
    _DIRECTORY_CACHE["all"] = (now + DIRECTORY_TTL_SECONDS, entries)
    return entries


async def resolve_shop(slug: str) -> ShopRef:
    """The listed, servable shop behind ``slug``; ``NetworkError(unknown_shop)`` otherwise."""
    if slug_problem(slug) == "invalid_slug":
        raise NetworkError("unknown_shop", "No such shop in the network.", 404)
    rows = await _listed_rows(slug)
    if not rows:
        raise NetworkError("unknown_shop", "No such shop in the network.", 404)
    listing = rows[0]
    host = await _servable_host(listing, await _config_for(listing))
    if host is None:
        raise NetworkError("shop_unavailable", "This shop is not taking orders through the network right now.", 404)
    return ShopRef(
        slug=listing.slug,
        tenant_id=str(listing.tenant_id),
        merchant_id=listing.merchant_id,
        seller_agent_id=listing.seller_agent_id,
        host=host,
        display_name=listing.display_name,
    )


# --- the merchant's view ----------------------------------------------------------------------


def surfaces_view(
    listing: CommerceNetworkListingRow | None,
    config: C6ZMerchantCommerceConfigRow,
    domain: MerchantCustomDomainRow | None,
) -> dict[str, Any]:
    """Where the shop stands on each surface, for the Sell everywhere page."""
    host = published_host(config)
    listed = listing is not None and listing.status == "listed"
    if domain is not None and domain.status not in INACTIVE_STATUSES:
        web = {"status": "live" if domain.status == "live" else domain.status, "host": domain.host}
    elif host:
        web = {"status": "live", "host": host}
    else:
        web = {"status": "not_started", "host": None}
    reachable = listed and host is not None
    return {
        "web": web,
        "mcp": {
            "status": "live" if reachable else ("waiting_for_publication" if listed else "off"),
            "server_url": "https://mcp.agenticorg.ai/mcp",
        },
        "telegram": {
            "status": "live" if reachable and telegram_link(listing.slug) else ("off" if not listed else "coming_soon"),
            "link": telegram_link(listing.slug) if listing is not None else None,
        },
        "google": {"status": "registration_pending"},
        "microsoft": {"status": "registration_pending"},
        "perplexity": {"status": "registration_pending"},
        "payments": {"status": "off"},
    }


def listing_view(listing: CommerceNetworkListingRow | None) -> dict[str, Any] | None:
    if listing is None:
        return None
    return {
        "slug": listing.slug,
        "status": listing.status,
        "display_name": listing.display_name,
        "serving_host": listing.serving_host,
        "subdomain_host": listing.subdomain_host,
        "terms_version": listing.terms_version,
        "opted_in_at": listing.opted_in_at.isoformat().replace("+00:00", "Z") if listing.opted_in_at else None,
    }
