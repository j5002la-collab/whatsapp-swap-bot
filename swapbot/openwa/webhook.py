"""Webhook receiver for OpenWA Gateway events.
Handles HMAC-SHA256 signature verification and event parsing.
"""

import logging

from fastapi import APIRouter, Request, HTTPException, status

logger = logging.getLogger("openwa.webhook")

router = APIRouter()


def create_webhook_router() -> APIRouter:
    """Create the webhook router (used in main.py)."""
    return router


class WebhookPayload:
    """Parsed webhook payload from OpenWA."""

    def __init__(self, raw: dict):
        self.raw = raw
        self.event = raw.get("event", "")
        self.timestamp = raw.get("timestamp", "")
        self.session_id = raw.get("sessionId", "")
        self.data = raw.get("data", {})

    @property
    def message_body(self) -> str:
        """Get message text body."""
        return self.data.get("body", "").strip()

    @property
    def from_phone(self) -> str:
        """Get sender's WhatsApp ID e.g. '628123456789@c.us'."""
        return self.data.get("from", "")

    @property
    def chat_id(self) -> str:
        """Get chat ID where message was received."""
        return self.data.get("from", self.data.get("chatId", ""))

    @property
    def message_id(self) -> str:
        """Get message ID."""
        return self.data.get("id", "")

    @property
    def is_group(self) -> bool:
        """Check if message is from a group."""
        return self.data.get("isGroup", False)

    @property
    def contact_name(self) -> str | None:
        """Get contact name if available."""
        contact = self.data.get("contact", {})
        return contact.get("name") or contact.get("pushName")
