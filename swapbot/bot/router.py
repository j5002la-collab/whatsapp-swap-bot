"""Message router: parse webhook payload, lookup user state, dispatch to handler."""

import json
import logging
import re
from datetime import datetime, timezone

from swapbot.db.connection import Database
from swapbot.db.queries import (
    get_or_create_user,
    get_user_state,
    update_user_state,
    check_rate_limit,
)
from swapbot.openwa.client import OpenWAClient
from swapbot.boltz.client import BoltzClient
from swapbot.engine.rates import RateEngine
from swapbot.engine.commission import CommissionEngine
from swapbot.engine.swap import SwapOrchestrator
from swapbot.engine.raffle import RaffleEngine
from swapbot.bot.state import UserState, UserStateType

logger = logging.getLogger("bot.router")


class MessageRouter:
    """Routes incoming WhatsApp messages to the appropriate handler based on user state."""

    def __init__(
        self,
        db: Database,
        openwa_client: OpenWAClient,
        boltz_client: BoltzClient,
        rate_engine: RateEngine,
        commission_engine: CommissionEngine,
        swap_orchestrator: SwapOrchestrator,
        raffle_engine: RaffleEngine,
        admin_phone: str = "",
    ):
        self.db = db
        self.openwa = openwa_client
        self.boltz = boltz_client
        self.rates = rate_engine
        self.commission = commission_engine
        self.swap = swap_orchestrator
        self.raffle = raffle_engine
        self.admin_hash = admin_phone

        # Set OpenWA client on swap orchestrator
        self.swap.set_openwa(openwa_client)

    async def handle_message(self, payload: dict):
        """Main entry point for message.received webhook."""
        try:
            data = payload.get("data", {})
            body = (data.get("body", "") or "").strip().lower()
            from_phone = data.get("from", "")
            chat_id = from_phone  # WhatsApp chatId is the from field
            contact_name = data.get("contact", {}).get("pushName", "")

            if not body or not from_phone:
                return

            # Skip group messages
            if data.get("isGroup"):
                return

            logger.info(f"📩 {contact_name or from_phone}: {body[:80]}")

            # Hash phone for privacy
            import hashlib
            phone_hash = hashlib.sha256(from_phone.encode()).hexdigest()[:16]

            # Get or create user
            user = await get_or_create_user(self.db, phone_hash)

            # Load current state
            state_data = await get_user_state(self.db, phone_hash)
            user_state = (
                UserState.from_dict(phone_hash, state_data)
                if state_data
                else UserState(phone_hash=phone_hash)
            )

            # Dispatch
            await self._dispatch(phone_hash, chat_id, body, user_state, contact_name)

        except Exception as e:
            logger.error(f"Message handler error: {e}", exc_info=True)

    async def _dispatch(
        self,
        phone_hash: str,
        chat_id: str,
        body: str,
        state: UserState,
        contact_name: str,
    ):
        """Route message to appropriate handler based on user state and content."""
        from swapbot.bot.handlers import (
            handle_swap_start,
            handle_direction_selection,
            handle_amount_entry,
            handle_invoice_entry,
            handle_address_entry,
            handle_confirmation,
            handle_rates,
            handle_calc,
            handle_help,
            handle_cancel,
            handle_admin,
            handle_default,
        )

        # Check if admin command
        if phone_hash == self.admin_hash and body.startswith("admin"):
            await handle_admin(
                self, phone_hash, chat_id, body, state, contact_name
            )
            return

        # Cancel command always available
        if body in ("cancelar", "cancel"):
            await handle_cancel(self, phone_hash, chat_id, body, state)
            return

        # State-based handling
        if state.state == UserStateType.SELECTING_DIRECTION:
            await handle_direction_selection(
                self, phone_hash, chat_id, body, state
            )
        elif state.state == UserStateType.ENTERING_INVOICE:
            await handle_invoice_entry(
                self, phone_hash, chat_id, body, state
            )
        elif state.state == UserStateType.ENTERING_ADDRESS:
            await handle_address_entry(
                self, phone_hash, chat_id, body, state
            )
        elif state.state == UserStateType.ENTERING_AMOUNT:
            await handle_amount_entry(
                self, phone_hash, chat_id, body, state
            )
        elif state.state == UserStateType.CONFIRMING:
            await handle_confirmation(
                self, phone_hash, chat_id, body, state
            )
        elif state.state == UserStateType.AWAITING_PAYMENT:
            # User is in an active swap - don't start a new one
            await self.openwa.send_text(
                chat_id,
                "⏳ Tienes un intercambio en curso. Espera la confirmación.",
            )
        else:
            # IDLE state: check for command triggers
            if body in ("swap", "cambiar"):
                if await check_rate_limit(self.db, phone_hash):
                    await self.openwa.send_text(
                        chat_id, "⏳ Límite de 3 swaps/hora. Espera un momento."
                    )
                    return
                await handle_swap_start(self, phone_hash, chat_id, body, state)
            elif body in ("rates", "tasas"):
                await handle_rates(self, phone_hash, chat_id, body, state)
            elif body in ("help", "ayuda"):
                await handle_help(self, phone_hash, chat_id, body, state)
            elif body.startswith(("calc", "calcular")):
                await handle_calc(self, phone_hash, chat_id, body, state)
            else:
                await handle_default(self, phone_hash, chat_id, body, state)
