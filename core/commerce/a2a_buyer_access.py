# SPDX-License-Identifier: Apache-2.0
"""Credentials for merchant-approved, read-only A2A buyer conversations."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from core.database import get_tenant_session
from core.models.commerce_a2a_buyer_access import CommerceA2ABuyerAccess
from core.models.commerce_c6z_runtime import C6ZSellerOnboardingPacketRow

_TOKEN_RE = re.compile(r"^ao_buyer_([0-9a-f]{32})_([A-Za-z0-9_-]{40,64})$")


@dataclass(frozen=True, slots=True)
class BuyerAccessIdentity:
    access_id: str
    tenant_id: str
    merchant_id: str
    seller_agent_id: str
    buyer_agent_id: str


def mint_buyer_token(tenant_id: str) -> tuple[str, str]:
    token = f"ao_buyer_{UUID(tenant_id).hex}_{secrets.token_urlsafe(32)}"
    return token, hashlib.sha256(token.encode("ascii")).hexdigest()


async def resolve_buyer_token(token: str) -> BuyerAccessIdentity | None:
    match = _TOKEN_RE.fullmatch(token)
    if match is None:
        return None
    tenant_id = str(UUID(hex=match.group(1)))
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    async with get_tenant_session(UUID(tenant_id)) as session:
        row = await session.scalar(
            select(CommerceA2ABuyerAccess).where(
                CommerceA2ABuyerAccess.tenant_id == tenant_id,
                CommerceA2ABuyerAccess.token_hash == digest,
                CommerceA2ABuyerAccess.status == "active",
                CommerceA2ABuyerAccess.expires_at > datetime.now(UTC),
            )
        )
        if row is None:
            return None
        seller = await session.scalar(select(C6ZSellerOnboardingPacketRow).where(
            C6ZSellerOnboardingPacketRow.tenant_id == tenant_id,
            C6ZSellerOnboardingPacketRow.merchant_id == row.merchant_id,
            C6ZSellerOnboardingPacketRow.seller_agent_id == row.seller_agent_id,
        ))
        if seller is None or seller.status in {
            "draft", "rejected", "blocked_missing_credentials", "blocked_grantex_unavailable",
        }:
            return None
        return BuyerAccessIdentity(
            access_id=str(row.id),
            tenant_id=row.tenant_id,
            merchant_id=row.merchant_id,
            seller_agent_id=row.seller_agent_id,
            buyer_agent_id=row.buyer_agent_id,
        )
