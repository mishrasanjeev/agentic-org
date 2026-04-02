"""Tally Bridge Agent — runs on the CA's local machine.

Establishes a WebSocket connection to the AgenticOrg cloud platform and
tunnels XML/TDL requests to the local Tally ERP instance.

Usage (standalone):
    python -m bridge.tally_bridge \\
        --cloud-url wss://app.agenticorg.ai/api/v1/ws/bridge \\
        --bridge-id <id> --bridge-token <token>
"""

from __future__ import annotations

import asyncio
import json
import signal
from typing import Any

import httpx
import structlog
import websockets
from websockets.asyncio.client import ClientConnection

logger = structlog.get_logger()

# Minimal TDL envelope for health-checking Tally
_HEALTH_CHECK_XML = (
    '<?xml version="1.0" encoding="utf-8"?>'
    "<ENVELOPE><HEADER><VERSION>1</VERSION>"
    "<TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE>"
    "<ID>List of Companies</ID></HEADER><BODY/></ENVELOPE>"
)


class TallyBridge:
    """WebSocket bridge between AgenticOrg cloud and local Tally."""

    def __init__(
        self,
        cloud_url: str,
        bridge_id: str,
        bridge_token: str,
        tally_host: str = "localhost",
        tally_port: int = 9000,
        heartbeat_interval: int = 30,
        health_check_interval: int = 60,
    ):
        self.cloud_url = cloud_url.rstrip("/")
        self.bridge_id = bridge_id
        self.bridge_token = bridge_token
        self.tally_url = f"http://{tally_host}:{tally_port}"
        self.heartbeat_interval = heartbeat_interval
        self.health_check_interval = health_check_interval

        self._ws: ClientConnection | None = None
        self._running = False
        self._tally_healthy = False
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0

    async def start(self) -> None:
        """Main loop — connect to cloud, process messages, auto-reconnect."""
        self._running = True
        logger.info(
            "bridge_starting",
            cloud_url=self.cloud_url,
            bridge_id=self.bridge_id,
            tally_url=self.tally_url,
        )

        # Handle graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        while self._running:
            try:
                await self._connect()
                self._reconnect_delay = 1.0  # Reset on successful connect
                await asyncio.gather(
                    self._message_loop(),
                    self._heartbeat_loop(),
                    self._health_check_loop(),
                )
            except (
                websockets.ConnectionClosed,
                websockets.InvalidStatus,
                OSError,
            ) as exc:
                logger.warning(
                    "bridge_disconnected",
                    error=str(exc),
                    reconnect_in=self._reconnect_delay,
                )
                if self._running:
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(
                        self._reconnect_delay * 2,
                        self._max_reconnect_delay,
                    )

    async def _connect(self) -> None:
        """Establish WebSocket connection to the cloud platform."""
        ws_url = f"{self.cloud_url}/{self.bridge_id}"
        logger.info("bridge_connecting", url=ws_url)

        self._ws = await websockets.connect(
            ws_url,
            additional_headers={"Authorization": f"Bearer {self.bridge_token}"},
            ping_interval=20,
            ping_timeout=10,
        )

        # Send auth handshake
        await self._ws.send(json.dumps({
            "type": "auth",
            "bridge_id": self.bridge_id,
            "token": self.bridge_token,
        }))

        resp = json.loads(await self._ws.recv())
        if resp.get("type") != "auth_ok":
            raise RuntimeError(f"Bridge auth failed: {resp}")

        logger.info("bridge_connected", bridge_id=self.bridge_id)

    async def _message_loop(self) -> None:
        """Listen for incoming requests from the cloud and process them."""
        assert self._ws is not None
        async for raw in self._ws:
            msg = json.loads(raw)
            msg_type = msg.get("type", msg.get("method", ""))

            if msg_type == "post_xml":
                asyncio.create_task(self._handle_xml_request(msg))
            elif msg_type == "heartbeat_ack":
                pass  # Expected response to our heartbeats
            elif msg_type == "ping":
                await self._ws.send(json.dumps({"type": "pong"}))
            else:
                logger.warning("bridge_unknown_message", msg_type=msg_type)

    async def _handle_xml_request(self, msg: dict[str, Any]) -> None:
        """Process an XML request: forward to Tally, return response."""
        request_id = msg.get("request_id", "unknown")
        xml_body = msg.get("xml_body", "")

        logger.info("bridge_request", request_id=request_id)

        try:
            xml_response = await self._forward_to_tally(xml_body)
            response = {
                "type": "response",
                "request_id": request_id,
                "status": "ok",
                "xml_response": xml_response,
            }
        except Exception as exc:
            logger.error("bridge_tally_error", request_id=request_id, error=str(exc))
            response = {
                "type": "response",
                "request_id": request_id,
                "status": "error",
                "error": str(exc),
            }

        if self._ws:
            await self._ws.send(json.dumps(response))

    async def _forward_to_tally(self, xml_body: str) -> str:
        """HTTP POST XML to the local Tally instance."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                self.tally_url,
                content=xml_body.encode("utf-8"),
                headers={"Content-Type": "application/xml"},
            )
            resp.raise_for_status()
            return resp.text

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to the cloud."""
        while self._running and self._ws:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                if self._ws:
                    await self._ws.send(json.dumps({
                        "type": "heartbeat",
                        "bridge_id": self.bridge_id,
                        "tally_healthy": self._tally_healthy,
                    }))
            except websockets.ConnectionClosed:
                break

    async def _health_check_loop(self) -> None:
        """Periodically check if local Tally is reachable."""
        while self._running:
            await asyncio.sleep(self.health_check_interval)
            self._tally_healthy = await self._tally_health_check()

    async def _tally_health_check(self) -> bool:
        """POST a minimal TDL request to verify Tally is responding."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    self.tally_url,
                    content=_HEALTH_CHECK_XML.encode("utf-8"),
                    headers={"Content-Type": "application/xml"},
                )
                healthy = resp.status_code == 200
                if healthy:
                    logger.debug("tally_health_ok")
                else:
                    logger.warning("tally_health_bad_status", status=resp.status_code)
                return healthy
        except Exception as exc:
            logger.warning("tally_health_unreachable", error=str(exc))
            return False

    async def stop(self) -> None:
        """Graceful shutdown."""
        logger.info("bridge_stopping")
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._ws.state.name == "OPEN"

    @property
    def tally_healthy(self) -> bool:
        return self._tally_healthy
