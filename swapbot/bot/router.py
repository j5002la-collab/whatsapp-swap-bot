"""Message router: parse webhook payload, detect language, lookup user state, dispatch to handler.
"""

import json
import logging
import hashlib
from datetime import datetime, timezone

from swapbot.db.connection import Database
from swapbot.db.queries import (
    get_or_create_user,
    get_user_state,
    get_user_language,
    update_user_state,
    check_rate_limit,
)
from swapbot.openwa.client import OpenWAClient
from swapbot.changenow.client import ChangeNowClient
from swapbot.engine.swap import SwapOrchestrator
from swapbot.bot.state import UserState, UserStateType
from swapbot.i18n import detect_language, SUPPORTED_LANGS

logger = logging.getLogger("bot.router")


class MessageRouter:
    """Routes incoming WhatsApp messages to the appropriate handler based on user state and content."""

    def __init__(
        self,
        db: Database,
        openwa_client: OpenWAClient,
        cn_client: ChangeNowClient,
        swap_orchestrator: SwapOrchestrator,
        admin_phone: str = "",
    ):
        self.db = db
        self.openwa = openwa_client
        self.cn = cn_client
        self.swap = swap_orchestrator
        self.admin_hash = admin_phone

        self.swap.set_openwa(openwa_client)

    async def handle_message(self, payload: dict):
        """Main entry point for message.received webhook."""
        try:
            data = payload.get("data", {})
            body = (data.get("body", "") or "").strip()
            from_phone = data.get("from", "")
            chat_id = from_phone
            contact_name = data.get("contact", {}).get("pushName", "")

            if not body or not from_phone:
                return

            # Skip group, broadcast, and status messages
            if data.get("isGroup"):
                return
            if "@broadcast" in from_phone or "@status" in from_phone:
                return

            logger.info(f"📩 {contact_name or from_phone}: {body[:80]}")

            # Hash phone for privacy
            phone_hash = hashlib.sha256(from_phone.encode()).hexdigest()[:16]

            # Detect language from phone number
            detected_lang = detect_language(from_phone)

            # Get or create user
            user = await get_or_create_user(self.db, phone_hash, detected_lang)

            # Load language preference (DB overrides auto-detect)
            db_lang = user.get("language", detected_lang)
            lang = db_lang if db_lang in SUPPORTED_LANGS else detected_lang

            # Load current state
            state_data = await get_user_state(self.db, phone_hash)
            user_state = (
                UserState.from_dict(phone_hash, state_data)
                if state_data
                else UserState(phone_hash=phone_hash)
            )

            # Dispatch
            await self._dispatch(phone_hash, chat_id, body.lower(), user_state, lang, from_phone, contact_name)

        except Exception as e:
            logger.error(f"Message handler error: {e}", exc_info=True)

    async def _dispatch(
        self,
        phone_hash: str,
        chat_id: str,
        body: str,
        state: UserState,
        lang: str,
        from_phone: str,
        contact_name: str,
    ):
        """Route message based on user state and content."""
        from swapbot.bot.handlers import (
            handle_swap_start,
            handle_source_category,
            handle_source_currency,
            handle_source_network,
            handle_dest_category,
            handle_dest_currency,
            handle_dest_network,
            handle_amount_entry,
            handle_dest_address,
            handle_extra_id,
            handle_confirmation,
            handle_help,
            handle_language,
            handle_status,
            handle_cancel,
            handle_admin,
            handle_default,
        )

        # Admin commands (only from admin phone)
        if phone_hash == self.admin_hash and body.startswith("admin"):
            await handle_admin(
                self, phone_hash, chat_id, body, state, lang, contact_name
            )
            return

        # Cancel always available
        if body in ("cancelar", "cancel"):
            await handle_cancel(self, phone_hash, chat_id, body, state, lang)
            return

        # Language commands
        if body.startswith(("lang ", "idioma ", "language ")):
            await handle_language(self, phone_hash, chat_id, body, state, lang)
            return

        # AWAITING_PAYMENT: user has active swap, show status
        if state.state == UserStateType.AWAITING_PAYMENT:
            # Check if it's a status command
            if body in ("status", "estado", "statut", "estado"):
                await handle_status(self, phone_hash, chat_id, body, state, lang)
                return
            await self.openwa.send_text(
                chat_id,
                "⏳ Tienes un intercambio en curso. Envía *status* para ver el progreso."
            )
            return

        # State-based dispatch
        state_handlers = {
            UserStateType.SELECTING_SOURCE_CATEGORY: handle_source_category,
            UserStateType.SELECTING_SOURCE_CURRENCY: handle_source_currency,
            UserStateType.SELECTING_SOURCE_NETWORK: handle_source_network,
            UserStateType.SELECTING_DEST_CATEGORY: handle_dest_category,
            UserStateType.SELECTING_DEST_CURRENCY: handle_dest_currency,
            UserStateType.SELECTING_DEST_NETWORK: handle_dest_network,
            UserStateType.ENTERING_AMOUNT: handle_amount_entry,
            UserStateType.ENTERING_DEST_ADDRESS: handle_dest_address,
            UserStateType.CONFIRMING: handle_confirmation,
        }

        handler = state_handlers.get(state.state)
        if handler:
            # Special case: memo/extra_id entry is ENTERING_DEST_ADDRESS with extra_id marker
            if state.state == UserStateType.ENTERING_DEST_ADDRESS and state.session.extra_id == "required":
                await handle_extra_id(self, phone_hash, chat_id, body, state, lang)
                return
            await handler(self, phone_hash, chat_id, body, state, lang)
            return

        # IDLE: check for command triggers
        if body in ("swap", "cambiar", "trocar", "échanger"):
            if await check_rate_limit(self.db, phone_hash):
                from swapbot.bot.messages import rate_limited
                await self.openwa.send_text(chat_id, rate_limited(lang))
                return
            await handle_swap_start(self, phone_hash, chat_id, body, state, lang)

        elif body in ("help", "ayuda", "ajuda", "aide", "menu"):
            await handle_help(self, phone_hash, chat_id, body, state, lang)

        elif body in ("status", "estado", "estado", "statut") or body.startswith(("status ", "estado ", "statut ")):
            await handle_status(self, phone_hash, chat_id, body, state, lang)

        else:
            await handle_default(self, phone_hash, chat_id, body, state, lang)
