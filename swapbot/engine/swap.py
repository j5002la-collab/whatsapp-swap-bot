"""Swap orchestrator — ChangeNOW-based swap creation and monitoring.
Handles: estimate, create, poll status, notify user.
"""

import asyncio
import logging
import secrets
from datetime import datetime, timezone

from swapbot.changenow.client import ChangeNowClient, EstimatedAmount
from swapbot.db.connection import Database
from swapbot.db.queries import (
    create_swap,
    update_swap,
    get_swap,
    increment_user_swaps,
)
from swapbot.openwa.client import OpenWAClient
from swapbot.i18n import t as _t

logger = logging.getLogger("engine.swap")

# Status polling config
POLL_INTERVAL = 30  # seconds
MAX_POLLS = 60      # max 30 minutes


class SwapOrchestrator:
    """Coordinates swap creation via ChangeNOW and status monitoring."""

    def __init__(
        self,
        cn_client: ChangeNowClient,
        db: Database,
    ):
        self.cn = cn_client
        self.db = db
        self._openwa: OpenWAClient | None = None
        self._active_polls: dict[str, asyncio.Task] = {}

    def set_openwa(self, client: OpenWAClient):
        self._openwa = client

    async def estimate_swap(
        self,
        from_ticker: str,
        to_ticker: str,
        from_amount: str,
        from_network: str,
        to_network: str,
    ) -> EstimatedAmount | None:
        """Get swap estimate from ChangeNOW."""
        return await self.cn.estimate(
            from_ticker, to_ticker, from_amount, from_network, to_network
        )

    async def execute_swap(
        self,
        phone_hash: str,
        chat_id: str,
        lang: str,
        from_ticker: str,
        to_ticker: str,
        from_amount: str,
        from_network: str,
        to_network: str,
        dest_address: str,
        extra_id: str | None = None,
        rate_id: str | None = None,
    ) -> str | None:
        """Execute a ChangeNOW swap end-to-end.
        
        Returns swap_id on success, None on failure.
        """
        swap_id = "SWAP-" + secrets.token_hex(6).upper()

        try:
            # 1. Create exchange
            result = await self.cn.create_exchange(
                from_ticker=from_ticker,
                to_ticker=to_ticker,
                from_amount=from_amount,
                from_network=from_network,
                to_network=to_network,
                address=dest_address,
                extra_id=extra_id,
                rate_id=rate_id,
            )

            if not result:
                if self._openwa:
                    await self._openwa.send_text(
                        chat_id, _t("swap.error", lang)
                    )
                return None

            # 2. Show deposit instructions
            if self._openwa:
                memo_text = ""
                if result.extra_id or extra_id:
                    memo_text = _t("swap.memo_required", lang, memo=result.extra_id or extra_id or "")

                cn_info = self.cn.get_currency_info(from_ticker, from_network)
                network_display = cn_info.network_display if cn_info else from_network.upper()

                msg = _t("swap.created", lang,
                    from_amount=from_amount,
                    from_currency=from_ticker.upper(),
                    from_network=network_display,
                    payin_address=result.payin_address,
                    exchange_id=result.id,
                    memo=memo_text)
                await self._openwa.send_text(chat_id, msg)

                # Send the waiting message
                from swapbot.i18n import t as translate
                await self._openwa.send_text(
                    chat_id,
                    translate("swap.status.waiting", lang, network=network_display)
                )

            # 3. Persist swap
            from_amt_num = self._safe_float(from_amount)
            to_amt_num = self._safe_float(result.to_amount)

            await create_swap(
                self.db,
                swap_id=swap_id,
                phone_hash=phone_hash,
                direction=f"{from_ticker}_to_{to_ticker}",
                source_currency=from_ticker,
                dest_currency=to_ticker,
                source_amount=int(from_amt_num * 1e8) if from_amt_num > 0 else 0,
                dest_amount=int(to_amt_num * 1e8) if to_amt_num > 0 else 0,
                changenow_exchange_id=result.id,
                changenow_payin_address=result.payin_address,
                changenow_payout_address=result.payout_address or dest_address,
                user_address=dest_address,
                status="pending",
                boltz_status="waiting",  # Reusing boltz_status for CN status
            )

            # 4. Start polling
            task = asyncio.create_task(
                self._poll_status(
                    swap_id=swap_id,
                    exchange_id=result.id,
                    phone_hash=phone_hash,
                    chat_id=chat_id,
                    lang=lang,
                    from_ticker=from_ticker,
                    to_ticker=to_ticker,
                    from_network=from_network,
                    to_network=to_network,
                )
            )
            self._active_polls[result.id] = task

            return swap_id

        except Exception as e:
            logger.error(f"Swap {swap_id} creation error: {e}", exc_info=True)
            await create_swap(
                self.db,
                swap_id=swap_id,
                phone_hash=phone_hash,
                direction=f"{from_ticker}_to_{to_ticker}",
                source_currency=from_ticker,
                dest_currency=to_ticker,
                status="failed",
                boltz_status=f"error: {str(e)[:100]}",
            )
            if self._openwa:
                await self._openwa.send_text(chat_id, _t("swap.error", lang))
            return None

    async def _poll_status(
        self,
        swap_id: str,
        exchange_id: str,
        phone_hash: str,
        chat_id: str,
        lang: str,
        from_ticker: str,
        to_ticker: str,
        from_network: str,
        to_network: str,
    ):
        """Poll ChangeNOW for exchange status and notify user of progress."""
        last_status = "waiting"
        notified_once = False

        for i in range(MAX_POLLS):
            await asyncio.sleep(POLL_INTERVAL)

            try:
                status_data = await self.cn.get_status(exchange_id)
                if not status_data:
                    continue

                status = status_data.get("status", last_status)

                # Get network display names
                from_info = self.cn.get_currency_info(from_ticker, from_network)
                to_info = self.cn.get_currency_info(to_ticker, to_network)
                from_net = from_info.network_display if from_info else from_network.upper()
                to_net = to_info.network_display if to_info else to_network.upper()

                # Notify on status change for progress states
                if status != last_status and status in ("confirming", "exchanging", "sending"):
                    if self._openwa:
                        msg = _t(f"swap.status.{status}", lang,
                            from_currency=from_ticker.upper(),
                            to_currency=to_ticker.upper(),
                            network=from_net if status != "sending" else to_net)
                        await self._openwa.send_text(chat_id, msg)
                    last_status = status
                    notified_once = True

                # Terminal: success
                if status == "finished":
                    to_amount = status_data.get("toAmount", status_data.get("amount", {}).get("to", "0"))
                    if self._openwa:
                        msg = _t("swap.status.finished", lang,
                            to_amount=to_amount,
                            to_currency=to_ticker.upper(),
                            exchange_id=exchange_id)
                        await self._openwa.send_text(chat_id, msg)
                    await update_swap(self.db, swap_id, status="completed",
                        boltz_status=status,
                        completed_at=datetime.now(timezone.utc).isoformat())
                    await increment_user_swaps(self.db, phone_hash,
                        self._safe_int(status_data.get("fromAmount", "0")))
                    self._active_polls.pop(exchange_id, None)
                    return

                # Terminal: failure
                if status in ("failed", "refunded", "expired"):
                    reason = status_data.get("error", {}).get("message", status_data.get("message", status))
                    if self._openwa:
                        msg = _t(f"swap.status.{status}", lang,
                            exchange_id=exchange_id,
                            reason=reason)
                        await self._openwa.send_text(chat_id, msg)
                    await update_swap(self.db, swap_id, status=status,
                        boltz_status=status)
                    self._active_polls.pop(exchange_id, None)
                    return

                # Update DB with current status
                await update_swap(self.db, swap_id, boltz_status=status)

            except Exception as e:
                logger.error(f"Poll error for {exchange_id}: {e}")
                if i >= MAX_POLLS - 1:
                    if self._openwa:
                        await self._openwa.send_text(
                            chat_id,
                            _t("swap.status.overdue", lang,
                                exchange_id=exchange_id,
                                status=last_status)
                        )
                    return

        # Timeout reached
        if self._openwa and not notified_once:
            await self._openwa.send_text(
                chat_id,
                _t("swap.status.overdue", lang,
                    exchange_id=exchange_id,
                    status=last_status)
            )
        self._active_polls.pop(exchange_id, None)

    async def check_status(self, exchange_id: str) -> dict | None:
        """Check status of a specific exchange (for user status command)."""
        return await self.cn.get_status(exchange_id)

    @staticmethod
    def _safe_float(val: str | None) -> float:
        try:
            return float(val or "0")
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _safe_int(val: str | None) -> int:
        try:
            return int(float(val or "0"))
        except (ValueError, TypeError):
            return 0
