"""Swap orchestrator: creates swaps via Boltz, monitors WebSocket, notifies users.
Ported from telegram-swap-bot/src/bot/commands/swap.ts
"""

import asyncio
import logging
import os
import hashlib
import secrets
from datetime import datetime, timezone

from swapbot.boltz.client import BoltzClient
from swapbot.boltz.types import (
    SubmarineSwapRequest,
    ReverseSwapRequest,
    ChainSwapRequest,
)
from swapbot.boltz.websocket import BoltzWebSocket
from swapbot.db.connection import Database
from swapbot.db.queries import (
    create_swap,
    update_swap,
    get_swap,
    increment_user_swaps,
)
from swapbot.openwa.client import OpenWAClient
from .commission import CommissionEngine, FeeBreakdown
from .rates import RateInfo
from .raffle import RaffleEngine

logger = logging.getLogger("engine.swap")


class SwapOrchestrator:
    """Coordinates swap creation, monitoring, and user notification."""

    def __init__(
        self,
        boltz_client: BoltzClient,
        db: Database,
        commission_engine: CommissionEngine,
        raffle_engine: "RaffleEngine",
    ):
        self.boltz = boltz_client
        self.db = db
        self.commission = commission_engine
        self.raffle = raffle_engine
        self._ws: BoltzWebSocket | None = None
        self._active_swaps: set[str] = set()
        self._openwa: OpenWAClient | None = None

    def set_ws(self, ws: BoltzWebSocket):
        self._ws = ws

    def set_openwa(self, client: OpenWAClient):
        self._openwa = client

    async def execute_submarine_swap(
        self,
        phone_hash: str,
        chat_id: str,
        invoice: str,
        source_amount: int,
        rate_info: RateInfo,
        fee_breakdown: FeeBreakdown,
    ) -> str | None:
        """Execute a submarine swap (BTC on-chain → Lightning).
        
        Returns the swap ID or None on failure.
        """
        swap_id = "SWAP-" + secrets.token_hex(6).upper()

        try:
            # Generate random keys for refund
            refund_key = secrets.token_hex(32)

            # Create Boltz swap
            response = await self.boltz.create_submarine_swap(
                SubmarineSwapRequest(
                    from_currency="BTC",
                    to_currency="BTC",
                    invoice=invoice,
                    refundPublicKey=refund_key,
                )
            )

            # Persist swap record
            raffle_contrib = int(source_amount * 0.001)
            await create_swap(
                self.db,
                swap_id=swap_id,
                phone_hash=phone_hash,
                direction="btc_ln",
                source_currency="BTC",
                dest_currency="BTC",
                source_amount=source_amount,
                dest_amount=response.expectedAmount,
                boltz_swap_id=response.id,
                boltz_address=response.address,
                boltz_expected_amount=response.expectedAmount,
                boltz_status="swap.created",
                status="pending",
                commission_rate=fee_breakdown.commission_rate,
                commission_amount=fee_breakdown.commission_amount,
                boltz_fee_amount=fee_breakdown.boltz_fee_amount,
                boltz_miner_fee=fee_breakdown.boltz_miner_fee,
                raffle_contribution=raffle_contrib,
                pair_hash=rate_info.pair_hash,
                user_invoice=invoice,
            )

            # Track raffle
            await self.raffle.add_entry(phone_hash, source_amount)

            # Subscribe to WebSocket updates
            if self._ws and self._openwa:
                self._active_swaps.add(swap_id)
                await self._ws.subscribe(
                    response.id,
                    lambda sid, status: self._on_swap_update(
                        swap_id, phone_hash, chat_id, response.address,
                        fee_breakdown, status
                    ),
                )

                # Auto-unsubscribe after 2 hours
                def _cleanup():
                    asyncio.ensure_future(self._unsubscribe_after_timeout(swap_id, response.id))

            logger.info(f"Swap executed: {swap_id} (Boltz: {response.id})")
            return swap_id

        except Exception as e:
            logger.error(f"Swap creation failed: {e}")
            # Record failed swap
            await create_swap(
                self.db,
                swap_id=swap_id,
                phone_hash=phone_hash,
                direction="btc_ln",
                source_currency="BTC",
                dest_currency="BTC",
                source_amount=source_amount,
                boltz_status=f"error: {str(e)[:100]}",
                status="failed",
                commission_rate=fee_breakdown.commission_rate,
            )
            return None

    async def execute_reverse_swap(
        self,
        phone_hash: str,
        chat_id: str,
        dest_address: str,
        invoice_amount: int,
        rate_info: RateInfo,
        fee_breakdown: FeeBreakdown,
    ) -> str | None:
        """Execute a reverse swap (Lightning → BTC on-chain).
        
        Returns the swap ID or None on failure.
        """
        swap_id = "SWAP-" + secrets.token_hex(6).upper()

        try:
            # Generate preimage and claim keys
            preimage = secrets.token_bytes(32)
            preimage_hash = hashlib.sha256(preimage).hexdigest()
            claim_key = secrets.token_hex(32)

            response = await self.boltz.create_reverse_swap(
                ReverseSwapRequest(
                    from_currency="BTC",
                    to_currency="BTC",
                    invoiceAmount=invoice_amount,
                    claimPublicKey=claim_key,
                    preimageHash=preimage_hash,
                    address=dest_address,
                )
            )

            raffle_contrib = int(invoice_amount * 0.001)
            await create_swap(
                self.db,
                swap_id=swap_id,
                phone_hash=phone_hash,
                direction="ln_btc",
                source_currency="BTC",
                dest_currency="BTC",
                source_amount=invoice_amount,
                dest_amount=response.expectedAmount,
                boltz_swap_id=response.id,
                boltz_invoice=response.invoice,
                boltz_status="swap.created",
                status="pending",
                commission_rate=fee_breakdown.commission_rate,
                commission_amount=fee_breakdown.commission_amount,
                boltz_fee_amount=fee_breakdown.boltz_fee_amount,
                boltz_miner_fee=fee_breakdown.boltz_miner_fee,
                raffle_contribution=raffle_contrib,
                pair_hash=rate_info.pair_hash,
                user_address=dest_address,
            )

            await self.raffle.add_entry(phone_hash, invoice_amount)

            if self._ws and self._openwa:
                self._active_swaps.add(swap_id)
                await self._ws.subscribe(
                    response.id,
                    lambda sid, status: self._on_reverse_update(
                        swap_id, phone_hash, chat_id, fee_breakdown, status
                    ),
                )

            return swap_id

        except Exception as e:
            logger.error(f"Reverse swap creation failed: {e}")
            await create_swap(
                self.db,
                swap_id=swap_id,
                phone_hash=phone_hash,
                direction="ln_btc",
                source_currency="BTC",
                dest_currency="BTC",
                source_amount=invoice_amount,
                boltz_status=f"error: {str(e)[:100]}",
                status="failed",
                commission_rate=fee_breakdown.commission_rate,
            )
            return None

    async def _on_swap_update(
        self,
        swap_id: str,
        phone_hash: str,
        chat_id: str,
        boltz_address: str,
        fee: FeeBreakdown,
        status: str,
    ):
        """Handle submarine swap status update from WebSocket."""
        logger.info(f"Swap {swap_id}: status → {status}")

        status_labels = {
            "swap.created": "⏳ Swap creado. Esperando tu transacción...",
            "invoice.set": "📋 Invoice validada. Envía tus BTC a la dirección indicada.",
            "transaction.mempool": "🔍 Transacción detectada (mempool). Esperando confirmación...",
            "transaction.confirmed": "✅ Transacción confirmada. Pagando tu invoice Lightning...",
            "invoice.pending": "⚡ Pagando invoice Lightning...",
            "invoice.paid": "💰 Invoice pagada. Completando swap...",
            "transaction.claim.pending": "🔐 Finalizando swap...",
            "transaction.claimed": "🎉 ¡Swap completado!",
            "invoice.settled": "🎉 ¡Swap completado! Tus fondos fueron enviados.",
        }

        failure_statuses = [
            "invoice.failedToPay",
            "swap.expired",
            "transaction.lockupFailed",
            "transaction.failed",
            "transaction.refunded",
        ]

        terminal_statuses = ["transaction.claimed", "invoice.settled"]

        if status in failure_statuses:
            await update_swap(self.db, swap_id, status="failed", boltz_status=status)
            if self._openwa:
                fail_msg = (
                    f"❌ *Swap no completado*\n\n"
                    f"ID: `{swap_id}`\n"
                    f"Estado: {status}\n\n"
                    f"Tus fondos serán reembolsados automáticamente.\n"
                    f"Contacta a soporte si necesitas ayuda."
                )
                await self._openwa.send_text(chat_id, fail_msg)
            return

        if status in terminal_statuses:
            await update_swap(
                self.db,
                swap_id,
                status="completed",
                boltz_status=status,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            await increment_user_swaps(self.db, phone_hash, fee.source_amount)

            if self._openwa:
                complete_msg = (
                    f"🎉 *¡Swap completado!*\n\n"
                    f"Enviaste: {fee.source_amount:,} sats\n"
                    f"Recibiste: {fee.estimated_receive:,} sats\n"
                    f"Swap: `{swap_id}`\n\n"
                    f"Envía *swap* para un nuevo intercambio."
                )
                await self._openwa.send_text(chat_id, complete_msg)
            return

        # Progress update
        label = status_labels.get(status, f"Estado: {status}")
        if self._openwa:
            progress_msg = (
                f"🔄 *{label}*\n\n"
                f"Swap: `{swap_id}`\n"
            )
            if boltz_address:
                progress_msg += f"📤 Envía a: `{boltz_address}`\n"
            await self._openwa.send_text(chat_id, progress_msg)

        await update_swap(self.db, swap_id, boltz_status=status)

    async def _on_reverse_update(
        self,
        swap_id: str,
        phone_hash: str,
        chat_id: str,
        fee: FeeBreakdown,
        status: str,
    ):
        """Handle reverse swap status update from WebSocket."""
        logger.info(f"Reverse swap {swap_id}: status → {status}")

        status_labels = {
            "swap.created": "⏳ Swap creado. Paga la invoice Lightning.",
            "minerfee.paid": "💰 Fee de minería pagado. Procesando...",
            "transaction.mempool": "🔍 Transacción detectada (mempool).",
            "transaction.confirmed": "✅ Transacción confirmada. Enviando BTC...",
        }

        failure_statuses = [
            "invoice.expired",
            "transaction.failed",
            "swap.expired",
            "transaction.refunded",
        ]

        terminal_statuses = ["invoice.settled"]

        if status in failure_statuses:
            await update_swap(self.db, swap_id, status="failed", boltz_status=status)
            if self._openwa:
                fail_msg = (
                    f"❌ *Swap no completado*\n\n"
                    f"ID: `{swap_id}`\n"
                    f"Estado: {status}\n\n"
                    f"Contacta a soporte si necesitas ayuda."
                )
                await self._openwa.send_text(chat_id, fail_msg)
            return

        if status in terminal_statuses:
            await update_swap(
                self.db,
                swap_id,
                status="completed",
                boltz_status=status,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            await increment_user_swaps(self.db, phone_hash, fee.source_amount)

            if self._openwa:
                complete_msg = (
                    f"🎉 *¡Swap completado!*\n\n"
                    f"Pagaste: {fee.source_amount:,} sats\n"
                    f"Swap: `{swap_id}`\n\n"
                    f"Los BTC se enviaron a tu dirección.\n"
                    f"Envía *swap* para un nuevo intercambio."
                )
                await self._openwa.send_text(chat_id, complete_msg)
            return

        label = status_labels.get(status, f"Estado: {status}")
        if self._openwa:
            await self._openwa.send_text(chat_id, f"🔄 *{label}*\n\nSwap: `{swap_id}`")

        await update_swap(self.db, swap_id, boltz_status=status)

    async def _unsubscribe_after_timeout(self, swap_id: str, boltz_id: str):
        """Clean up WebSocket subscription after 2 hours."""
        await asyncio.sleep(2 * 3600)
        if self._ws:
            await self._ws.unsubscribe(boltz_id)
        self._active_swaps.discard(swap_id)
