"""WebSocket client for Boltz swap status updates.
Ported from telegram-swap-bot/src/boltz/websocket.ts
"""

import asyncio
import json
import logging
from typing import Callable, Awaitable

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger("boltz.websocket")

# Type alias for swap status callback
SwapStatusCallback = Callable[[str, str], Awaitable[None]]


class BoltzWebSocket:
    """WebSocket client for real-time Boltz swap status updates."""

    def __init__(self, boltz_api_url: str):
        ws_url = boltz_api_url.replace("https://", "wss://").replace("http://", "ws://")
        self.url = f"{ws_url}/v2/ws"
        self._ws: ClientConnection | None = None
        self._subscriptions: dict[str, SwapStatusCallback] = {}
        self._running = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._lock = asyncio.Lock()

    async def connect(self):
        """Connect to Boltz WebSocket and maintain connection with reconnection."""
        self._running = True
        await self._create_connection()

    async def _create_connection(self):
        """Create websocket connection with retry logic."""
        while self._running:
            try:
                logger.info(f"Connecting to Boltz WebSocket: {self.url}")
                async with websockets.connect(
                    self.url,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._reconnect_attempts = 0
                    logger.info("Boltz WebSocket connected")

                    # Re-subscribe to all existing subscriptions
                    async with self._lock:
                        for swap_id in list(self._subscriptions.keys()):
                            await self._send_subscribe(swap_id)

                    # Listen for messages
                    async for message in ws:
                        await self._handle_message(message)

            except (websockets.ConnectionClosed, OSError) as e:
                logger.warning(f"Boltz WebSocket disconnected: {e}")
            except Exception as e:
                logger.error(f"Boltz WebSocket error: {e}")

            if not self._running:
                break

            self._reconnect_attempts += 1
            if self._reconnect_attempts > self._max_reconnect_attempts:
                logger.error("Max reconnect attempts reached for Boltz WebSocket")
                break

            delay = min(1.5 ** self._reconnect_attempts, 30)
            logger.info(f"Reconnecting Boltz WebSocket in {delay:.1f}s (attempt {self._reconnect_attempts})")
            await asyncio.sleep(delay)

    async def _handle_message(self, raw: str):
        """Parse incoming WebSocket message."""
        try:
            msg = json.loads(raw)

            if msg.get("event") == "update" and msg.get("args"):
                update = msg["args"][0]
                swap_id = update.get("id", "")
                status = update.get("status", "")

                async with self._lock:
                    callback = self._subscriptions.get(swap_id)

                if callback:
                    logger.debug(f"WS swap update: {swap_id} → {status}")
                    try:
                        await callback(swap_id, status)
                    except Exception as e:
                        logger.error(f"WS callback error for {swap_id}: {e}")

        except json.JSONDecodeError:
            logger.warning("Invalid JSON from Boltz WS")
        except Exception as e:
            logger.error(f"WS message handler error: {e}")

    async def subscribe(self, swap_id: str, callback: SwapStatusCallback):
        """Subscribe to status updates for a swap."""
        async with self._lock:
            self._subscriptions[swap_id] = callback

        if self._ws:
            await self._send_subscribe(swap_id)

    async def unsubscribe(self, swap_id: str):
        """Unsubscribe from swap status updates."""
        async with self._lock:
            self._subscriptions.pop(swap_id, None)

    async def _send_subscribe(self, swap_id: str):
        """Send subscribe message over WebSocket."""
        if self._ws:
            try:
                msg = json.dumps({
                    "op": "subscribe",
                    "channel": "swap.update",
                    "args": [swap_id],
                })
                await self._ws.send(msg)
                logger.debug(f"Subscribed to WS updates for {swap_id}")
            except Exception as e:
                logger.error(f"Failed to subscribe to {swap_id}: {e}")

    async def disconnect(self):
        """Clean shutdown."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("Boltz WebSocket disconnected")

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._ws.close_sent
