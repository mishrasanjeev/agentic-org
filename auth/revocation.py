# SPDX-License-Identifier: Apache-2.0
"""Grant revocation on the platform side: the pool, the authority service's feed and the middleware.

A grant token is unexpired for as long as its ``exp`` says, so withdrawing
authority has to happen somewhere other than the token. Three things do it:

- Pausing or retiring an agent revokes the run grants the pool minted for it
  at the authority service (``/oauth2/revoke``), evicts them from the shared
  cache and from every node's in-process cache, and reports what happened,
  so the response of pause and retire is the real result rather than a
  constant.
- The authentication middleware refuses a grant token whose grant the
  authority service has revoked (the SDK's revocation feed, kept current in
  a background thread) and any token of an agent that is retired, or paused
  while paused agents are refused (``core.governance.agent_status``).
- When the revocation state cannot be known (no feed, feed stale or failing)
  a strict runtime refuses the token and a relaxed runtime allows it with a
  warning, so an outage of the feed never widens authority in production.

``AGENTICORG_GRANTEX_REVOCATION_CHECK`` is ``feed`` (default) or ``off``; off
skips the feed and keeps only the agent-status refusal. The feed needs the
Grantex SDK API key (``GRANTEX_API_KEY``); without one no feed is built and
the state is unknown.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import structlog

from auth.token_pool import RevocationOutcome
from core.config import external_keys, grantex_base_url_for_env, is_strict_runtime_env, settings
from core.governance.agent_status import refusal_for

logger = structlog.get_logger()

MODES: tuple[str, ...] = ("feed", "off")
REASON_AGENT_INACTIVE = "agent_inactive"
REASON_REVOKED = "grant_revoked"
REASON_UNKNOWN = "revocation_unknown"
_FEED_RETRY_SECONDS = 60.0

_lock = threading.Lock()
# enterprise-gate: process-local-ok reason=one-revocation-feed-thread-per-process-shared-by-every-request
_feed: Any = None
_feed_failed_at: float | None = None
_override_feed: Any = None


def mode() -> str:
    """The configured check mode; an unknown value reads as ``feed``."""
    value = str(getattr(settings, "grantex_revocation_check", "feed") or "feed").strip().lower()
    return value if value in MODES else "feed"


def set_feed_for_tests(feed: Any) -> None:
    """Replace the feed (tests); ``None`` restores the real one."""
    global _override_feed
    _override_feed = feed


def reset_for_tests() -> None:
    global _feed, _feed_failed_at, _override_feed
    with _lock:
        current, _feed = _feed, None
        _feed_failed_at = None
    _override_feed = None
    if current is not None:
        try:
            current.stop()
        # enterprise-gate: broad-except-ok reason=stopping-a-test-feed-never-fails-the-reset-logged
        except Exception as exc:
            logger.debug("grant_revocation_feed_stop_failed", error_type=type(exc).__name__)


def _build_feed() -> Any:
    from grantex import Grantex

    client = Grantex(
        api_key=external_keys.grantex_api_key,
        base_url=grantex_base_url_for_env(),
        revocation_check="feed",
    )
    candidate = client.revocation_feed()
    candidate.start()
    return candidate


def feed() -> Any:
    """The revocation feed, started on first use; None when it cannot be built (no API key, SDK error)."""
    global _feed, _feed_failed_at
    if _override_feed is not None:
        return _override_feed
    if not str(external_keys.grantex_api_key or "").strip():
        return None
    with _lock:
        if _feed is not None:
            return _feed
        if _feed_failed_at is not None and time.monotonic() - _feed_failed_at < _FEED_RETRY_SECONDS:
            return None
        try:
            _feed = _build_feed()
        # enterprise-gate: broad-except-ok reason=unbuildable-feed-degrades-to-unknown-state-refused-in-strict-runtimes
        except Exception as exc:
            _feed_failed_at = time.monotonic()
            logger.warning("grant_revocation_feed_unavailable", error_type=type(exc).__name__)
            return None
        logger.info("grant_revocation_feed_started")
        return _feed


@dataclass(frozen=True)
class RevocationState:
    """``revoked`` is True, False, or None when the state cannot be known; ``detail`` says why."""

    revoked: bool | None
    detail: str


def check(grant_id: str | None, token_id: str | None, parent_grant_id: str | None = None) -> RevocationState:
    """Whether the authority service has revoked the grant, the token, or the grant it was delegated from."""
    if mode() == "off":
        return RevocationState(False, "check_off")
    current = feed()
    if current is None:
        return RevocationState(None, "feed_unavailable")
    try:
        if not current.is_fresh():
            return RevocationState(None, "feed_stale")
        match = current.match(
            grant_id=grant_id or None, token_id=token_id or None, parent_grant_id=parent_grant_id or None
        )
    # enterprise-gate: broad-except-ok reason=failing-feed-degrades-to-unknown-state-refused-in-strict-runtimes
    except Exception as exc:
        return RevocationState(None, f"feed_error:{type(exc).__name__}")
    return RevocationState(bool(match), "revoked" if match else "current")


def refusal_reason(
    *,
    grant_id: str | None,
    token_id: str | None,
    agent_status: str | None,
    parent_grant_id: str | None = None,
) -> str | None:
    """Why a verified grant token must still be refused, or None.

    The agent's status comes first (retired, or paused while paused agents are
    refused), then the authority service's revocation state. An unknown state
    refuses in a strict runtime and is allowed with a warning elsewhere.
    """
    if refusal_for(agent_status):
        return REASON_AGENT_INACTIVE
    state = check(grant_id, token_id, parent_grant_id)
    if state.revoked:
        return REASON_REVOKED
    if state.revoked is None:
        if is_strict_runtime_env(settings.env):
            logger.warning("grant_revocation_state_unknown_refused", detail=state.detail)
            return REASON_UNKNOWN
        logger.warning("grant_revocation_state_unknown_allowed", detail=state.detail)
    return None


async def revoke_agent_authority(tenant_id: str, agent_id: str) -> RevocationOutcome:
    """Revoke every grant the pool holds for an agent at the authority service and evict it everywhere.

    Never raises: a failed revocation is counted and logged, and the outcome
    says so, because the caller's pause or retire must still land. The
    middleware refuses the paused or retired agent's tokens regardless.
    """
    from auth.grantex import grantex_client
    from auth.token_pool import token_pool

    async def _revoke(token: str) -> bool:
        try:
            await grantex_client.revoke_token(token)
        # enterprise-gate: broad-except-ok reason=a-failed-remote-revocation-is-counted-in-the-outcome-and-logged
        except Exception as exc:
            logger.warning("grant_revocation_failed", agent_id=agent_id, error_type=type(exc).__name__)
            return False
        return True

    outcome = await token_pool.revoke_agent_grants(tenant_id, agent_id, _revoke)
    logger.info(
        "agent_authority_revoked",
        agent_id=agent_id,
        evicted=outcome.evicted,
        revoked=outcome.revoked,
        failed=outcome.failed,
    )
    return outcome
