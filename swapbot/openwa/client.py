"""Async HTTP client for OpenWA Gateway REST API."""

import logging
import time
import httpx

logger = logging.getLogger("openwa.client")


class OpenWAClient:
    """Async client for OpenWA API to send WhatsApp messages and manage sessions."""

    def __init__(self, base_url: str, api_key: str, session_id: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session_id = session_id
        self.http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=15.0,
        )

    async def close(self):
        await self.http.aclose()

    # --- Messaging ---

    async def send_text(self, chat_id: str, text: str) -> dict | None:
        """Send a text message to a WhatsApp chat.
        
        Args:
            chat_id: Phone number with suffix, e.g. "628123456789@c.us"
            text: Message body (max ~65k chars)
        
        Returns:
            API response data or None on failure
        """
        request_id = f"req_{int(time.time() * 1000)}"
        try:
            response = await self.http.post(
                f"/sessions/{self.session_id}/messages/send-text",
                json={
                    "chatId": chat_id,
                    "text": text,
                },
                headers={"X-Request-ID": request_id},
            )
            data = response.json()
            if data.get("success"):
                logger.debug(f"Message sent to {chat_id}: {text[:50]}...")
                return data.get("data")
            logger.error(f"Send text failed: {data.get('error', {}).get('message', 'unknown')}")
            return None
        except Exception as e:
            logger.error(f"Send text error: {e}")
            return None

    async def session_status(self) -> dict | None:
        """Get current session status."""
        try:
            response = await self.http.get(f"/sessions/{self.session_id}")
            data = response.json()
            if data.get("success"):
                return data.get("data")
            return None
        except Exception as e:
            logger.error(f"Session status error: {e}")
            return None

    # --- Webhook Management ---

    async def register_webhook(self, webhook_url: str, secret: str) -> bool:
        """Register or update a webhook for the session.
        
        Args:
            webhook_url: URL OpenWA will POST webhooks to (e.g. http://swapbot:2889/webhook)
            secret: HMAC-SHA256 secret for signature verification
        """
        try:
            # List existing webhooks first
            response = await self.http.get(f"/sessions/{self.session_id}/webhooks")
            data = response.json()
            existing = data.get("data", []) if data.get("success") else []

            # Check if webhook already exists
            webhook_id = None
            for wh in existing:
                if wh.get("url") == webhook_url:
                    webhook_id = wh.get("id")
                    break

            if webhook_id:
                # Delete existing, then re-create
                await self.http.delete(
                    f"/sessions/{self.session_id}/webhooks/{webhook_id}"
                )
                logger.info(f"Removed existing webhook: {webhook_id}")

            # Create new webhook
            create_resp = await self.http.post(
                f"/sessions/{self.session_id}/webhooks",
                json={
                    "url": webhook_url,
                    "events": [
                        "message.received",
                        "message.sent",
                        "message.ack",
                        "session.status",
                    ],
                    "secret": secret,
                },
            )
            create_data = create_resp.json()
            if create_data.get("success"):
                logger.info(f"Webhook registered: {webhook_url}")
                return True
            else:
                logger.error(f"Webhook registration failed: {create_data}")
                return False

        except Exception as e:
            logger.error(f"Webhook registration error: {e}")
            return False

    async def get_chat_id(self, phone: str) -> str:
        """Convert a plain phone number to WhatsApp chat ID format."""
        phone = phone.replace("+", "").replace(" ", "").replace("-", "")
        return f"{phone}@c.us"
