"""Commission engine: calculates fees and formats breakdown.
Ported from telegram-swap-bot/src/engine/commission.ts
"""

import logging
from dataclasses import dataclass

from .rates import RateInfo

logger = logging.getLogger("engine.commission")


@dataclass
class FeeBreakdown:
    """Full fee breakdown for a swap."""
    source_amount: int = 0         # Source amount in smallest unit
    commission_rate: float = 0.0    # Bot commission rate (%)
    commission_amount: int = 0     # Bot commission in smallest unit
    boltz_fee_rate: float = 0.0    # Boltz fee rate (%)
    boltz_fee_amount: int = 0     # Boltz fee in smallest unit
    boltz_miner_fee: int = 0      # Boltz miner fee in sats
    total_fees: int = 0           # Total fees deducted
    net_swap_amount: int = 0      # Net amount after fees
    estimated_receive: int = 0    # Estimated receive in dest currency
    bot_profit: int = 0           # Bot profit


class CommissionEngine:
    """Handles commission calculation and formatting."""

    def __init__(self, commission_rate: float = 2.5):
        self.commission_rate = commission_rate

    def calculate_commission(self, source_amount: int) -> int:
        """Calculate commission on a source amount."""
        return int(source_amount * (self.commission_rate / 100))

    def get_net_after_commission(self, source_amount: int) -> int:
        """Get net amount after deducting commission."""
        return source_amount - self.calculate_commission(source_amount)

    def calculate_fee_breakdown(
        self, source_amount: int, rate_info: RateInfo
    ) -> FeeBreakdown:
        """Calculate full fee breakdown for a swap."""
        commission_amount = self.calculate_commission(source_amount)

        # Boltz fee on net amount after commission
        amount_for_boltz = source_amount - commission_amount
        boltz_fee_amount = int(amount_for_boltz * (rate_info.boltz_fee_pct / 100))
        boltz_miner_fee = rate_info.boltz_miner_fee

        total_fees = commission_amount + boltz_fee_amount + boltz_miner_fee
        net_swap_amount = source_amount - total_fees
        estimated_receive = max(0, int(net_swap_amount * rate_info.user_rate))

        return FeeBreakdown(
            source_amount=source_amount,
            commission_rate=self.commission_rate,
            commission_amount=commission_amount,
            boltz_fee_rate=rate_info.boltz_fee_pct,
            boltz_fee_amount=boltz_fee_amount,
            boltz_miner_fee=boltz_miner_fee,
            total_fees=total_fees,
            net_swap_amount=net_swap_amount,
            estimated_receive=estimated_receive,
            bot_profit=commission_amount,
        )

    def format_breakdown(
        self, fee: FeeBreakdown, source_currency: str = "sats", dest_currency: str = "sats"
    ) -> str:
        """Format fee breakdown as a WhatsApp message."""
        raffle = int(fee.source_amount * 0.001)

        # Format amounts
        def fmt_sats(amount: int) -> str:
            if abs(amount) < 1_000_000:
                return f"{amount:,} sats"
            btc = amount / 100_000_000
            return f"{btc:.8f} BTC ({amount:,} sats)"

        lines = [
            "📋 *Resumen de tu swap*",
            "",
            f"Envías: {fmt_sats(fee.source_amount)}",
            f"Recibes: {fmt_sats(fee.estimated_receive)}",
            "",
            "*Comisiones incluidas:*",
            f"  ├ SwapBot ({fee.commission_rate}%): {fee.commission_amount:,} sats",
            f"  ├ Minería: {fee.boltz_miner_fee:,} sats",
            f"  └ Sorteo (0.1%): {raffle:,} sats",
            "",
            "⏱ Tiempo estimado: 10-30 minutos",
        ]
        return "\n".join(lines)

    def format_amount(self, amount: int, currency: str = "sats") -> str:
        """Format an amount for display."""
        if currency in ("sats", "BTC"):
            if amount < 1_000_000:
                return f"{amount:,} sats"
            btc = amount / 100_000_000
            return f"{amount:,} sats ({btc:.8f} BTC)"
        if currency in ("USDT", "USDC", "cents"):
            usd = amount / 100
            return f"${usd:.2f} {currency}"
        return f"{amount:,} {currency}"

    def is_profitable(self, source_amount: int, min_commission_sats: int = 200) -> bool:
        """Check if a swap is profitable (commission >= min threshold)."""
        commission = source_amount * (self.commission_rate / 100)
        return commission >= min_commission_sats

    def get_commission_rate(self) -> float:
        return self.commission_rate

    def set_commission_rate(self, rate: float):
        """Set commission rate at runtime (admin command). Range: 1.5% - 2.5%."""
        if rate < 1.5 or rate > 2.5:
            raise ValueError("Commission rate must be between 1.5% and 2.5%")
        self.commission_rate = rate
        logger.info(f"Commission rate changed to {rate}%")
