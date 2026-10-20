# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Universal Commerce Protocol (UCP) business surfaces.

Profile, catalog and locations are rendered from verified OACP artifacts
(``profile``, ``catalog``, ``locations``). Transactions (checkout, cart and
order) run through the transport-neutral handlers in
``core.commerce.ucp.handlers``: the REST binding (``api/v1/commerce_ucp.py``)
calls them and an MCP binding can call the same functions (UCP has no separate
JSON-RPC transport). Transaction shapes are drawn by one module per release in
``core.commerce.ucp.renderers``.
"""
