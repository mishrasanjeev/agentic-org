# SPDX-License-Identifier: Apache-2.0
"""Walk an application's HTTP routes the same way on every supported FastAPI release.

FastAPI 0.141 and later keep included routers lazy: ``app.routes`` holds
``_IncludedRouter`` entries whose ``effective_candidates`` carry each route's
full path, methods and endpoint. Older releases list ``APIRoute`` objects
directly. A flat ``for route in app.routes`` therefore finds no API route on the
pinned release, and a route-table assertion written that way passes or fails
depending on the installed FastAPI.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from fastapi.routing import APIRoute


@dataclass(frozen=True)
class HttpRoute:
    path: str
    methods: frozenset[str]
    endpoint: Any
    dependency_calls: frozenset[Any]


def _walk(routes: Iterable[Any]) -> Iterator[Any]:
    for route in routes:
        if hasattr(route, "effective_candidates"):
            candidates = route.effective_candidates
            yield from _walk(candidates() if callable(candidates) else candidates)
        else:
            yield route


def _flatten(dependencies: Iterable[Any]) -> Iterator[Any]:
    for dependency in dependencies:
        yield dependency
        yield from _flatten(getattr(dependency, "dependencies", ()))


def http_routes(app: Any) -> list[HttpRoute]:
    """Every HTTP API route of ``app`` with its full path, methods and endpoint."""
    found: list[HttpRoute] = []
    for route in _walk(app.routes):
        original = getattr(route, "original_route", route)
        if not isinstance(original, APIRoute):
            continue
        endpoint = getattr(route, "endpoint", None) or original.endpoint
        methods = getattr(route, "methods", None) or original.methods
        dependant = getattr(route, "dependant", None) or original.dependant
        calls = frozenset(dependency.call for dependency in _flatten(dependant.dependencies))
        found.append(
            HttpRoute(path=route.path, methods=frozenset(methods), endpoint=endpoint, dependency_calls=calls)
        )
    return found
