"""Swap orchestrator: creates swaps via Boltz, monitors WebSocket, notifies users.
Ported from telegram-swap-bot/src/bot/commands/swap.ts
"""

import asyncio
import logging
import os
import hashlib
import secrets
import httpx
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
        self._btc_wallet = None
        self._blink = None

    def set_ws(self, ws: BoltzWebSocket):
        self._ws = ws

    def set_openwa(self, client: OpenWAClient):
        self._openwa = client

    def set_btc_wallet(self, wallet):
        self._btc_wallet = wallet

    def set_blink(self, blink):
        self._blink = blink

    async def execute_ln_to_btc_blink(
        self,
        phone_hash: str,
        chat_id: str,
        dest_address: str,
        amount: int,
        fee_breakdown: FeeBreakdown,
    ) -> str | None:
        """LN→BTC via Blink: generate LN invoice, user pays, bot sends BTC.
        
        No Boltz reverse swap — direct LN receive via Blink.sv.
        """
        swap_id = "SWAP-" + secrets.token_hex(6).upper()
        blink = self._blink
        btc_wallet = self._btc_wallet

        if not blink or not btc_wallet:
            logger.error("Blink or BTC wallet not configured")
            return None

        try:
            # 1. Generate Blink LN invoice for the amount
            invoice_data = await blink.create_invoice(amount, memo=f"SwapBot {swap_id}")
            if not invoice_data:
                if self._openwa:
                    await self._openwa.send_text(
                        chat_id, "⚠️ Error generando invoice LN. Intenta de nuevo."
                    )
                return None

            payment_request = invoice_data["paymentRequest"]
            payment_hash = invoice_data.get("paymentHash", "")

            # 2. Show user the invoice to pay
            if self._openwa:
                invoice_msg = (
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "⚡ *Paga esta invoice Lightning*\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"`{payment_request}`\n\n"
                    f"Monto: {amount:,} sats\n"
                    f"Recibirás BTC en tu dirección.\n\n"
                    f"Swap: `{swap_id}`\n"
                    "⏳ _Esperando tu pago..._"
                )
                await self._openwa.send_text(chat_id, invoice_msg)

            # 3. Poll Blink balance until payment arrives
            initial_balance = await blink.get_btc_balance()
            if self._openwa:
                await self._openwa.send_text(
                    chat_id, "🔍 Esperando confirmación del pago LN..."
                )

            deadline = asyncio.get_event_loop().time() + 600  # 10 min timeout
            paid = False
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(5)
                current = await blink.get_btc_balance()
                if current >= initial_balance + amount:
                    paid = True
                    break

            if not paid:
                if self._openwa:
                    await self._openwa.send_text(
                        chat_id, "⏰ No se detectó tu pago en 10 min. Cancelado."
                    )
                return None

            if self._openwa:
                await self._openwa.send_text(
                    chat_id, "✅ Pago LN recibido. Enviando BTC a tu dirección..."
                )

            # 4. Save swap record
            await create_swap(
                self.db,
                swap_id=swap_id,
                phone_hash=phone_hash,
                direction="ln_btc",
                source_currency="BTC",
                dest_currency="BTC",
                source_amount=amount,
                dest_amount=amount - fee_breakdown.commission_amount,
                status="pending",
                commission_rate=fee_breakdown.commission_rate,
                commission_amount=fee_breakdown.commission_amount,
                boltz_fee_amount=0,
                boltz_miner_fee=0,
                raffle_contribution=int(amount * 0.001),
                user_address=dest_address,
                user_invoice=payment_request,
                boltz_status="blink:paid",
            )

            await self.raffle.add_contribution(self.db, phone_hash, amount)

            # 5. Send BTC from bot wallet to user (minus commission + network fee)
            forward_fee = 500
            user_gets = max(
                amount - fee_breakdown.commission_amount - forward_fee, 5000
            )

            txid = await btc_wallet.send_btc(dest_address, user_gets)
            if txid:
                await update_swap(
                    self.db,
                    swap_id,
                    status="completed",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    completion_tx=txid,
                )
                await increment_user_swaps(self.db, phone_hash, amount)
                if self._openwa:
                    await self._openwa.send_text(
                        chat_id,
                        f"🎉 *¡Swap completado!*\n\n"
                        f"Recibiste: {user_gets:,} sats\n"
                        f"TX: `{txid[:16]}...`\n"
                        f"Comisión: {fee_breakdown.commission_amount:,} sats\n"
                        f"Swap: `{swap_id}`\n\n"
                        f"Envía *swap* para un nuevo intercambio."
                    )
                logger.info(f"Blink LN→BTC swap completed: {swap_id}")
                return swap_id
            else:
                if self._openwa:
                    await self._openwa.send_text(
                        chat_id, "⚠️ Error al enviar BTC. Contactá a soporte."
                    )
                return None

        except Exception as e:
            logger.error(f"Blink LN→BTC error: {e}", exc_info=True)
            await create_swap(
                self.db,
                swap_id=swap_id,
                phone_hash=phone_hash,
                direction="ln_btc",
                source_currency="BTC",
                dest_currency="BTC",
                source_amount=amount,
                boltz_status=f"error: {str(e)[:100]}",
                status="failed",
                commission_rate=fee_breakdown.commission_rate if fee_breakdown else 2.5,
            )
            return None

    async def execute_submarine_swap(
        self,
        phone_hash: str,
        chat_id: str,
        invoice: str,
        source_amount: int,
        rate_info: RateInfo,
        fee_breakdown: FeeBreakdown,
    ) -> str | None:
        """Custodial BTC→LN swap.
        
        Flow:
        1. Show bot's BTC address → user sends BTC
        2. Wait for 1 confirmation
        3. Create Boltz submarine swap → forward BTC to Boltz
        4. Boltz pays user's Lightning invoice
        5. Commission stays in bot's wallet
        """
        swap_id = "SWAP-" + secrets.token_hex(6).upper()
        btc_wallet = self._btc_wallet

        if not btc_wallet:
            logger.error("BTC wallet not configured")
            return None

        try:
            # Total user must send = invoice amount + fees + commission
            total_user_sends = (
                fee_breakdown.source_amount
                + fee_breakdown.commission_amount
                + fee_breakdown.boltz_miner_fee
            )

            bot_address = btc_wallet.derive_address()

            # 1. Tell user to send BTC to bot
            if self._openwa:
                deposit_msg = (
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "📤 *Envía BTC para iniciar*\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Envía exactamente *{total_user_sends:,} sats* a:\n\n"
                    f"`{bot_address}`\n\n"
                    f"Recibirás: ~{fee_breakdown.estimated_receive:,} sats ⚡\n"
                    f"Comisión SwapBot: {fee_breakdown.commission_amount:,} sats\n\n"
                    "⏳ _Esperando tu transacción (1 confirmación)..._"
                )
                await self._openwa.send_text(chat_id, deposit_msg)

            # 2. Wait for deposit
            deposit = await btc_wallet.wait_for_deposit(
                total_user_sends, tolerance_pct=3.0, timeout_s=1800
            )
            if not deposit:
                if self._openwa:
                    await self._openwa.send_text(
                        chat_id,
                        "⏰ No se detectó tu depósito en 30 min. Cancelado."
                    )
                return None

            # 3. Create Boltz submarine swap (send invoice amount to Boltz)
            from bitcoinlib.keys import Key as BtcKey
            refund_keypair = BtcKey()
            response = await self.boltz.create_submarine_swap(
                SubmarineSwapRequest(
                    from_currency="BTC",
                    to_currency="BTC",
                    invoice=invoice,
                    refundPublicKey=refund_keypair.public_hex,
                )
            )

            # 4. Forward BTC to Boltz's deposit address
            boltz_amount = response.expectedAmount
            txid = await btc_wallet.send_btc(response.address, boltz_amount)
            if not txid:
                if self._openwa:
                    await self._openwa.send_text(
                        chat_id,
                        "⚠️ Error al enviar a Boltz. Contactá a soporte."
                    )
                return None

            # 5. Persist swap
            raffle_contrib = int(source_amount * 0.001)
            actual_commission = deposit["value"] - boltz_amount
            await create_swap(
                self.db,
                swap_id=swap_id,
                phone_hash=phone_hash,
                direction="btc_ln",
                source_currency="BTC",
                dest_currency="BTC",
                source_amount=total_user_sends,
                dest_amount=response.expectedAmount,
                boltz_swap_id=response.id,
                boltz_address=response.address,
                boltz_expected_amount=response.expectedAmount,
                boltz_status="swap.created",
                status="pending",
                commission_rate=fee_breakdown.commission_rate,
                commission_amount=actual_commission,
                boltz_fee_amount=fee_breakdown.boltz_fee_amount,
                boltz_miner_fee=fee_breakdown.boltz_miner_fee,
                raffle_contribution=raffle_contrib,
                pair_hash=rate_info.pair_hash,
                user_invoice=invoice,
                user_address=deposit["txid"],  # store user's deposit txid
            )

            await self.raffle.add_contribution(self.db, phone_hash, source_amount)

            # 6. Subscribe to Boltz WS for updates
            if self._ws and self._openwa:
                self._active_swaps.add(swap_id)
                await self._ws.subscribe(
                    response.id,
                    lambda sid, status: self._on_swap_update(
                        swap_id, phone_hash, chat_id, response.address,
                        fee_breakdown, status
                    ),
                )
                asyncio.ensure_future(
                    self._unsubscribe_after_timeout(swap_id, response.id)
                )

            if self._openwa:
                await self._openwa.send_text(
                    chat_id,
                    f"✅ BTC enviado a Boltz ({boltz_amount:,} sats)\n"
                    f"🔍 TX: `{txid[:16]}...`\n\n"
                    f"Swap: `{swap_id}`\n"
                    f"⏳ Procesando pago Lightning..."
                )

            logger.info(f"Swap executed: {swap_id} (Boltz: {response.id})")
            return swap_id

        except Exception as e:
            logger.error(f"Swap creation failed: {e}", exc_info=True)
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
        dest_address: str,  # user's final BTC address
        invoice_amount: int,
        rate_info: RateInfo,
        fee_breakdown: FeeBreakdown,
    ) -> str | None:
        """Custodial LN→BTC swap via Boltz reverse.
        
        Flow:
        1. Create Boltz reverse swap with destination = BOT's BTC address
        2. User pays the Boltz Lightning invoice
        3. Boltz sends BTC to bot's address
        4. Bot waits 1 confirmation
        5. Bot forwards BTC to user (minus commission)
        """
        swap_id = "SWAP-" + secrets.token_hex(6).upper()
        btc_wallet = self._btc_wallet

        if not btc_wallet:
            logger.error("BTC wallet not configured")
            return None

        try:
            bot_address = btc_wallet.derive_address()

            # 1. Create Boltz reverse swap with bot as destination
            # Generate proper secp256k1 keypair for Boltz
            from bitcoinlib.keys import Key as BtcKey
            claim_keypair = BtcKey()
            preimage = secrets.token_bytes(32)
            preimage_hash = hashlib.sha256(preimage).hexdigest()

            response = await self.boltz.create_reverse_swap(
                ReverseSwapRequest(
                    from_currency="BTC",
                    to_currency="BTC",
                    invoiceAmount=invoice_amount,
                    claimPublicKey=claim_keypair.public_hex,
                    preimageHash=preimage_hash,
                    address=bot_address,  # Boltz sends BTC to bot's address on settlement
                )
            )

            # 2. Show user the Lightning invoice to pay
            if self._openwa:
                invoice_msg = (
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "⚡ *Paga esta invoice Lightning*\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"`{response.invoice}`\n\n"
                    f"Monto: {invoice_amount:,} sats\n"
                    f"Recibirás BTC en tu dirección después de confirmación.\n\n"
                    f"Swap: `{swap_id}`\n"
                    "⏳ _Esperando tu pago..._"
                )
                await self._openwa.send_text(chat_id, invoice_msg)

            # 3. Wait for Boltz to send BTC to bot (monitor via WS + mempool)
            # Store swap so WS updates can find it
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

            await self.raffle.add_contribution(self.db, phone_hash, invoice_amount)

            # 4. Subscribe to Boltz WS for status + auto-claim on completion
            if self._ws and self._openwa:
                self._active_swaps.add(swap_id)
                await self._ws.subscribe(
                    response.id,
                    lambda sid, status: self._on_reverse_update_custodial(
                        swap_id, phone_hash, chat_id, dest_address,
                        fee_breakdown, response.expectedAmount, status
                    ),
                )
                asyncio.ensure_future(
                    self._unsubscribe_after_timeout(swap_id, response.id)
                )

            return swap_id

        except httpx.HTTPStatusError as e:
            resp_body = e.response.text[:300]
            logger.error(f"Reverse swap Boltz error ({e.response.status_code}): {resp_body}")
            await create_swap(
                self.db,
                swap_id=swap_id,
                phone_hash=phone_hash,
                direction="ln_btc",
                source_currency="BTC",
                dest_currency="BTC",
                source_amount=invoice_amount,
                boltz_status=f"error: {resp_body[:100]}",
                status="failed",
                commission_rate=fee_breakdown.commission_rate if fee_breakdown else 2.5,
            )
            return None

        except Exception as e:
            logger.error(f"Reverse swap creation failed: {e}", exc_info=True)
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
        """Handle reverse swap status update (legacy non-custodial)."""
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

    async def _on_reverse_update_custodial(
        self,
        swap_id: str,
        phone_hash: str,
        chat_id: str,
        user_address: str,
        fee_breakdown: FeeBreakdown,
        boltz_expected_amount: int,
        status: str,
        claim_key: str = "",
        preimage_hash: str = "",
        boltz_id: str = "",
    ):
        """Handle custodial reverse swap: auto-settles via Boltz → forward BTC.
        
        Reverse swaps auto-settle (no cooperative claim needed).
        On invoice.settled, Boltz sends BTC to the configured address automatically.
        """
        logger.info(f"Custodial reverse {swap_id}: status → {status}")
        btc_wallet = self._btc_wallet

        if status == "invoice.settled":
            # Boltz auto-settled and sent BTC to bot's address → wait → forward
            if not btc_wallet:
                logger.error("BTC wallet gone, can't forward")
                if self._openwa:
                    await self._openwa.send_text(
                        chat_id, "⚠️ Error técnico. Contactá a soporte."
                    )
                return

            if self._openwa:
                await self._openwa.send_text(
                    chat_id, "✅ Pago LN detectado. Esperando 1 confirmación..."
                )

            deposit = await btc_wallet.wait_for_deposit(
                boltz_expected_amount, tolerance_pct=5.0, timeout_s=900
            )
            if not deposit:
                if self._openwa:
                    await self._openwa.send_text(
                        chat_id, "⏰ Timeout esperando BTC de Boltz. Monitoreando..."
                    )
                return

            # Forward BTC to user (minus commission + network fee)
            commission = fee_breakdown.commission_amount
            forward_fee = 500
            user_gets = max(boltz_expected_amount - commission - forward_fee, 5000)

            if self._openwa:
                await self._openwa.send_text(
                    chat_id,
                    f"📤 Enviando *{user_gets:,} sats* a tu dirección...\n"
                    f"Comisión: {commission:,} sats | Fee red: {forward_fee} sats"
                )

            txid = await btc_wallet.send_btc(user_address, user_gets)
            if txid:
                await update_swap(
                    self.db,
                    swap_id,
                    status="completed",
                    boltz_status=status,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    completion_tx=txid,
                )
                await increment_user_swaps(self.db, phone_hash, fee_breakdown.source_amount)
                if self._openwa:
                    await self._openwa.send_text(
                        chat_id,
                        f"🎉 *¡Swap completado!*\n\n"
                        f"Recibiste: {user_gets:,} sats\n"
                        f"TX: `{txid[:16]}...`\n"
                        f"Swap: `{swap_id}`\n\n"
                        f"Envía *swap* para un nuevo intercambio."
                    )
            else:
                logger.error(f"Failed to send BTC to user {user_address}")
                if self._openwa:
                    await self._openwa.send_text(
                        chat_id, "⚠️ Error al enviar BTC. Contactá a soporte."
                    )
            return

        # Non-terminal updates
        await update_swap(self.db, swap_id, boltz_status=status)

    async def _unsubscribe_after_timeout(self, swap_id: str, boltz_id: str):
        """Clean up WebSocket subscription after 2 hours."""
        await asyncio.sleep(2 * 3600)
        if self._ws:
            await self._ws.unsubscribe(boltz_id)
        self._active_swaps.discard(swap_id)
