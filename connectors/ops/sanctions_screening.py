# SPDX-License-Identifier: Apache-2.0
"""Sanctions screening connector — ops / compliance.

Screens people, businesses and the parties to a transaction against sanctions, PEP and watch
lists through the verification provider seam (``connectors/framework/verification_provider.py``),
so no screening service's API or address is built in. ``provider`` in the connector config names
a provider in ``connectors.providers.registry``: ``mock`` by default, which runs only in local,
development, test and CI environments, or one that a separately installed package registers
through the ``agenticorg.providers`` entry point (``docs/providers/plugin-packages.md``). The
provider holds its own credentials.

Fails closed. An unknown provider, one this environment refuses or one without screening stops
``connect()``; a screening the provider does not offer raises ``CapabilityNotSupported`` and any
other provider error propagates, so a call never returns an empty result that reads as clear.
Every candidate the provider returns is reported: deciding whether a hit is the subject is a
human disposition.

Each tool call has one deadline, the connector's ``timeout_ms`` from when the call starts, and
every screening the call makes gets that same deadline. When it passes, the screening in progress
fails the call with ``ProviderTimeout`` and no further screening starts: a batch of 50 names
screened as people and businesses is 100 screenings within one timeout, not 100 timeouts.

It replaces the deprecated ``sanctions_api`` connector (``connectors/ops/sanctions_api.py``), which
stays registered with its own tools for tenants that use it; an agent moves here by linking this
connector. A grant held under either id covers the tools the two share
(``auth.grant_enforcement.enforce_connector_grant``).
"""

from __future__ import annotations

import re
import uuid
from functools import partial
from typing import Any

from connectors.framework.base_connector import BaseConnector
from connectors.framework.verification_provider import (
    BusinessSubject,
    Capability,
    CapabilityNotSupported,
    Deadline,
    ListType,
    NotAvailable,
    PersonSubject,
    ScreeningResult,
    ScreenOptions,
    VerificationProvider,
    call_capability,
)

DEFAULT_PROVIDER = "mock"
MAX_BATCH = 50
_PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SCREENING = frozenset({Capability.SCREEN_PERSON, Capability.SCREEN_BUSINESS})
# ``type`` values: the old connector's ``individual`` / ``entity`` and the seam's own names.
_KINDS: dict[str, tuple[Capability, ...]] = {
    "individual": (Capability.SCREEN_PERSON,),
    "person": (Capability.SCREEN_PERSON,),
    "entity": (Capability.SCREEN_BUSINESS,),
    "business": (Capability.SCREEN_BUSINESS,),
}
# Without a type a subject is screened as both: screened as the wrong kind, a listed person or
# business can be missed.
_BOTH_KINDS = (Capability.SCREEN_PERSON, Capability.SCREEN_BUSINESS)

Subject = PersonSubject | BusinessSubject


class ScreeningUnavailableError(RuntimeError):
    """No provider can screen for this connector, so the call is refused rather than answered."""


def _text(params: dict[str, Any], key: str, *, required: bool = False) -> str | None:
    value = params.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f"{key} is required")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value.strip()


def _name(params: dict[str, Any], key: str = "name") -> str:
    return _text(params, key, required=True) or ""


def _kinds(value: Any) -> tuple[Capability, ...]:
    if value is None or value == "":
        return _BOTH_KINDS
    kinds = _KINDS.get(str(value).strip().lower())
    if kinds is None:
        raise ValueError(f"type must be one of: {', '.join(_KINDS)}")
    return kinds


def _list_types(params: dict[str, Any]) -> frozenset[ListType]:
    value = params.get("list_types")
    if value is None:
        return frozenset(ListType)
    if not isinstance(value, list | tuple) or not value:
        raise ValueError("list_types must be a non-empty list")
    try:
        return frozenset(ListType(str(item).strip().lower()) for item in value)
    except ValueError as exc:
        raise ValueError(f"list_types may contain: {', '.join(ListType)}") from exc


def _subjects(
    name: str,
    kinds: tuple[Capability, ...],
    *,
    date_of_birth: str | None = None,
    nationality: str | None = None,
    jurisdiction: str | None = None,
) -> list[Subject]:
    """One validated subject per kind to screen; a malformed field raises ``ValueError`` before any call."""
    subjects: list[Subject] = []
    if Capability.SCREEN_PERSON in kinds:
        nationalities = (nationality.upper(),) if nationality else ()
        subjects.append(PersonSubject(full_name=name, date_of_birth=date_of_birth, nationalities=nationalities))
    if Capability.SCREEN_BUSINESS in kinds:
        subjects.append(BusinessSubject(legal_name=name, jurisdiction=jurisdiction.upper() if jurisdiction else None))
    return subjects


def _summary(results: list[ScreeningResult]) -> dict[str, Any]:
    return {
        "hit_count": sum(len(result.hits) for result in results),
        "screenings": [result.model_dump(mode="json") for result in results],
    }


class SanctionsScreeningConnector(BaseConnector):
    name = "sanctions_screening"
    category = "ops"
    auth_type = "none"
    base_url = ""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._provider: VerificationProvider | None = None

    def _register_tools(self) -> None:
        self._tool_registry["screen_entity"] = self.screen_entity
        self._tool_registry["screen_person"] = self.screen_person
        self._tool_registry["screen_business"] = self.screen_business
        self._tool_registry["screen_transaction"] = self.screen_transaction
        self._tool_registry["batch_screen"] = self.batch_screen

    async def _authenticate(self) -> None:
        """Nothing to do: the provider authenticates with its own configuration."""

    async def connect(self) -> None:
        # No HTTP client: the provider owns the transport.
        self._provider = self._create_provider()

    async def health_check(self) -> dict[str, Any]:
        try:
            provider = self._provider or self._create_provider()
        except ScreeningUnavailableError as exc:
            # A status answer, not a pass: the connector reports that it cannot screen.
            return {"status": "not_configured", "reason": str(exc)}
        # Never ``healthy``: the provider seam has no probe, and a provider built with wrong
        # credentials looks the same as a working one until it screens (UI-HEALTH-404).
        return {
            "status": "configured",
            "provider": provider.name,
            "reason": "the provider was created but not contacted; the first screening checks its credentials",
        }

    def _create_provider(self) -> VerificationProvider:
        # Imported here so that importing the connector package does not load the providers.
        from connectors.providers.registry import ProviderRegistry, ProviderRegistryError  # noqa: PLC0415

        name = str(self.config.get("provider") or DEFAULT_PROVIDER).strip()
        if not _PROVIDER_NAME.fullmatch(name):
            raise ScreeningUnavailableError("the configured screening provider is not a valid provider name")
        try:
            provider = ProviderRegistry.create(name)
        except ProviderRegistryError as exc:
            # Fail closed: without a provider there is no screening, and nothing to report as clear.
            raise ScreeningUnavailableError(
                f"screening provider {name!r} cannot be used ({exc.reason}); set `provider` in the connector "
                "config to a provider installed through the agenticorg.providers entry point"
            ) from exc
        if not provider.capabilities & _SCREENING:
            raise ScreeningUnavailableError(f"provider {name!r} offers no screening")
        return provider

    def _connected(self) -> VerificationProvider:
        if self._provider is None:
            raise ScreeningUnavailableError(f"{self.name} is not connected")
        return self._provider

    def _deadline(self) -> Deadline:
        """The deadline of one tool call, created when the call starts and shared by all its screenings."""
        return Deadline.after(self.timeout_ms / 1000)

    async def _screen(
        self, subjects: list[Subject], list_types: frozenset[ListType], deadline: Deadline
    ) -> list[ScreeningResult]:
        """Screen ``subjects`` in turn within ``deadline``, the tool call's, never a fresh one per screening."""
        provider = self._connected()
        results: list[ScreeningResult] = []
        for subject in subjects:
            options = ScreenOptions(idempotency_key=f"sanctions-screening:{uuid.uuid4().hex}", list_types=list_types)
            if isinstance(subject, PersonSubject):
                capability = Capability.SCREEN_PERSON
                call = partial(provider.screen_person, subject, options, deadline=deadline)
            else:
                capability = Capability.SCREEN_BUSINESS
                call = partial(provider.screen_business, subject, options, deadline=deadline)
            # The provider must keep the deadline it is given. Enforcing it here as well bounds the
            # call when a provider does not, and starts no screening once the time is up.
            async with deadline.enforce(provider.name, capability):
                outcome = await call_capability(provider, capability, call)
            if isinstance(outcome, NotAvailable):
                # Fail closed: a screening that did not run is not a clear result.
                raise CapabilityNotSupported(provider.name, capability, "the provider does not offer this screening")
            results.append(outcome)
        return results

    async def screen_entity(self, **params: Any) -> dict[str, Any]:
        """Screen a person or business name against sanctions, PEP and watch lists.

        Params: name (required), type (individual/entity; omitted screens both),
                date_of_birth (optional YYYY[-MM[-DD]], individuals), nationality (optional
                2-letter, individuals), jurisdiction (optional 2-letter, entities),
                list_types (optional: sanctions, pep, watchlist, enforcement, adverse_media).
        Every candidate the provider returns is reported; min_score is not applied.
        """
        deadline = self._deadline()
        subjects = _subjects(
            _name(params),
            _kinds(params.get("type")),
            date_of_birth=_text(params, "date_of_birth"),
            nationality=_text(params, "nationality"),
            jurisdiction=_text(params, "jurisdiction"),
        )
        results = await self._screen(subjects, _list_types(params), deadline)
        return {"provider": self._connected().name, **_summary(results)}

    async def screen_person(self, **params: Any) -> dict[str, Any]:
        """Screen a person against sanctions, PEP and watch lists.

        Params: name (required), date_of_birth (optional YYYY[-MM[-DD]]),
                nationality (optional 2-letter), list_types (optional).
        """
        deadline = self._deadline()
        subjects = _subjects(
            _name(params),
            (Capability.SCREEN_PERSON,),
            date_of_birth=_text(params, "date_of_birth"),
            nationality=_text(params, "nationality"),
        )
        results = await self._screen(subjects, _list_types(params), deadline)
        return {"provider": self._connected().name, **_summary(results)}

    async def screen_business(self, **params: Any) -> dict[str, Any]:
        """Screen a business against sanctions, PEP and watch lists.

        Params: name (required), jurisdiction (optional 2-letter), list_types (optional).
        """
        deadline = self._deadline()
        subjects = _subjects(_name(params), (Capability.SCREEN_BUSINESS,), jurisdiction=_text(params, "jurisdiction"))
        results = await self._screen(subjects, _list_types(params), deadline)
        return {"provider": self._connected().name, **_summary(results)}

    async def screen_transaction(self, **params: Any) -> dict[str, Any]:
        """Screen both parties to a transaction.

        Params: sender_name (required), receiver_name (required),
                sender_type / receiver_type (individual/entity; omitted screens both),
                sender_country / receiver_country (optional 2-letter, the jurisdiction of a
                party screened as an entity), list_types (optional).
        """
        deadline = self._deadline()
        sender_subjects, receiver_subjects = (
            _subjects(
                _name(params, f"{role}_name"),
                _kinds(params.get(f"{role}_type")),
                jurisdiction=_text(params, f"{role}_country"),
            )
            for role in ("sender", "receiver")
        )
        list_types = _list_types(params)
        sender = _summary(await self._screen(sender_subjects, list_types, deadline))
        receiver = _summary(await self._screen(receiver_subjects, list_types, deadline))
        return {
            "provider": self._connected().name,
            "hit_count": sender["hit_count"] + receiver["hit_count"],
            "sender": sender,
            "receiver": receiver,
        }

    async def batch_screen(self, **params: Any) -> dict[str, Any]:
        """Screen up to 50 entities.

        Params: entities (list of {name, type, date_of_birth, nationality, jurisdiction}),
                list_types (optional, applies to every entity).
        """
        deadline = self._deadline()
        entities = params.get("entities")
        if not isinstance(entities, list) or not entities:
            raise ValueError("entities must be a non-empty list")
        if len(entities) > MAX_BATCH:
            raise ValueError(f"at most {MAX_BATCH} entities per batch")
        batch: list[tuple[str, list[Subject]]] = []
        for entity in entities:
            if not isinstance(entity, dict):
                raise ValueError("each entity must be an object with a name")
            name = _name(entity)
            subjects = _subjects(
                name,
                _kinds(entity.get("type")),
                date_of_birth=_text(entity, "date_of_birth"),
                nationality=_text(entity, "nationality"),
                jurisdiction=_text(entity, "jurisdiction"),
            )
            batch.append((name, subjects))
        list_types = _list_types(params)
        results = [
            {"name": name, **_summary(await self._screen(subjects, list_types, deadline))} for name, subjects in batch
        ]
        return {
            "provider": self._connected().name,
            "hit_count": sum(result["hit_count"] for result in results),
            "results": results,
        }
