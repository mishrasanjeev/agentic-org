# SPDX-License-Identifier: Apache-2.0
"""Universal Commerce Protocol (UCP) business-side transactions: checkout, cart and order.

The handlers in ``core.commerce.ucp.handlers`` are transport-neutral: the REST
binding (``api/v1/commerce_ucp.py``) calls them, and an MCP binding can call
the same functions (UCP has no separate JSON-RPC transport; MCP and Embedded
are its JSON-RPC bindings). Version-specific shapes are rendered by one module
per UCP release in ``core.commerce.ucp.renderers``; the stored state is
version-neutral.
"""
