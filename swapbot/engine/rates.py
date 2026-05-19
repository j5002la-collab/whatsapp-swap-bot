"""Rate engine: fetch pairs from Boltz, cache, calculate user rate with commission.
Ported from telegram-swap-bot/src/engine/rates.ts
"""

import logging
import time
from dataclasses import dataclass, field

from swapbot.boltz.client import BoltzClient
from swapbot.boltz.types import BoltzPair, SubmarinePairs, ReversePairs, ChainPairs

logger = logging.getLogger("engine.rates")


@dataclass
class RateInfo:
    """Calculated rate information for a swap direction."""
    boltz_rate: float = 0.0       # Raw Boltz rate
    user_rate: float = 0.0        # Rate after commission
    boltz_fee_pct: float = 0.0     # Boltz fee percentage
    boltz_miner_fee: int = 0       # Boltz miner fees in sats
    bot_commission_pct: float = 0.0  # Bot commission percentage
    bot_commission_amount: int = 0   # Calculated when amount is known
    min_amount: int = 0
    max_amount: int = 0
    pair_hash: str = ""


@dataclass
class RateCache:
    data: SubmarinePairs | ReversePairs | ChainPairs
    fetched_at: float


class RateEngine:
    """Fetches and caches Boltz pair data, calculates user-facing rates."""

    def __init__(self, boltz_client: BoltzClient):
        self.client = boltz_client
        self._submarine_cache: RateCache | None = None
        self._reverse_cache: RateCache | None = None
        self._chain_cache: RateCache | None = None
        self._ttl_s = 30  # 30 second cache

    async def get_rate(
        self,
        direction: str,
        from_currency: str,
        to_currency: str,
        commission_pct: float = 2.5,
    ) -> RateInfo | None:
        """Get the rate info for a specific direction and currency pair."""
        try:
            if direction == "submarine":
                pairs = await self._get_cached_submarine()
            elif direction == "reverse":
                pairs = await self._get_cached_reverse()
            else:
                pairs = await self._get_cached_chain()
        except Exception as e:
            logger.error(f"Failed to fetch {direction} pairs: {e}")
            return None

        from_pairs = pairs.get(from_currency, {})
        pair = from_pairs.get(to_currency)
        if not pair:
            logger.warning(f"No pair for {from_currency}→{to_currency} in {direction}")
            return None

        return self._calculate_rate_info(pair, commission_pct)

    def _calculate_rate_info(self, pair: BoltzPair, commission_pct: float) -> RateInfo:
        """Calculate full rate info including bot commission."""
        boltz_fee_pct = pair.fees.percentage

        # Calculate miner fees
        miner_fees = pair.fees.minerFees
        if isinstance(miner_fees, int):
            boltz_miner_fee = miner_fees
        else:
            user_fees = miner_fees.user or {}
            boltz_miner_fee = (miner_fees.server or 0) + user_fees.get("claim", 0) + user_fees.get("lockup", 0)

        # User rate = boltz_rate * (1 - (boltz_fee% + commission%) / 100)
        fee_multiplier = 1 - (boltz_fee_pct + commission_pct) / 100
        user_rate = pair.rate * fee_multiplier

        return RateInfo(
            boltz_rate=pair.rate,
            user_rate=max(user_rate, 0),
            boltz_fee_pct=boltz_fee_pct,
            boltz_miner_fee=boltz_miner_fee,
            bot_commission_pct=commission_pct,
            min_amount=pair.limits.minimal,
            max_amount=pair.limits.maximal,
            pair_hash=pair.hash,
        )

    def calculate_amounts(self, source_amount: int, rate_info: RateInfo) -> dict:
        """Calculate receive amount and fee breakdown for a given source amount."""
        commission_amount = int(source_amount * (rate_info.bot_commission_pct / 100))
        boltz_fee_amount = int(source_amount * (rate_info.boltz_fee_pct / 100))
        net_amount = source_amount - commission_amount - boltz_fee_amount - rate_info.boltz_miner_fee
        receive_amount = max(0, int(net_amount * rate_info.boltz_rate))

        # For submarine (BTC→LN), source is what user sends, invoice is what they receive
        # We need source = (invoice + miner_fee) / (1 - total_fee_pct)
        total_fee_pct = rate_info.bot_commission_pct + rate_info.boltz_fee_pct
        source_from_invoice = int(
            (source_amount + rate_info.boltz_miner_fee) / (1 - total_fee_pct / 100)
        ) if total_fee_pct < 100 else 0

        return {
            "receive_amount": receive_amount,
            "commission_amount": commission_amount,
            "boltz_fee_amount": boltz_fee_amount,
            "net_amount": max(net_amount, 0),
            "source_from_invoice": max(source_from_invoice, 0),
        }

    def format_rate_display(self, rate_info: RateInfo, direction_label: str) -> str:
        """Format rate information for WhatsApp display."""
        lines = [
            f"📊 *{direction_label}*",
            "",
            f"💱 Tasa Boltz: 1 = {rate_info.boltz_rate:.8f}",
            f"💰 Tu tasa (con comisión): 1 = {rate_info.user_rate:.8f}",
            "",
            "📋 *Comisiones:*",
            f"  ├ Boltz fee: {rate_info.boltz_fee_pct}% + {rate_info.boltz_miner_fee} sats",
            f"  └ SwapBot fee: {rate_info.bot_commission_pct}%",
            "",
            f"📏 Mín: {rate_info.min_amount:,} | Máx: {rate_info.max_amount:,} sats",
        ]
        return "\n".join(lines)

    # --- Cached pair fetching ---

    async def _get_cached_submarine(self) -> SubmarinePairs:
        now = time.time()
        if self._submarine_cache and (now - self._submarine_cache.fetched_at) < self._ttl_s:
            return self._submarine_cache.data
        data = await self.client.get_submarine_pairs()
        self._submarine_cache = RateCache(data=data, fetched_at=now)
        return data

    async def _get_cached_reverse(self) -> ReversePairs:
        now = time.time()
        if self._reverse_cache and (now - self._reverse_cache.fetched_at) < self._ttl_s:
            return self._reverse_cache.data
        data = await self.client.get_reverse_pairs()
        self._reverse_cache = RateCache(data=data, fetched_at=now)
        return data

    async def _get_cached_chain(self) -> ChainPairs:
        now = time.time()
        if self._chain_cache and (now - self._chain_cache.fetched_at) < self._ttl_s:
            return self._chain_cache.data
        data = await self.client.get_chain_pairs()
        self._chain_cache = RateCache(data=data, fetched_at=now)
        return data
