"""User state machine for WhatsApp swap flow."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from swapbot.engine.rates import RateInfo
from swapbot.engine.commission import FeeBreakdown


class UserStateType(Enum):
    IDLE = "idle"
    SELECTING_DIRECTION = "selecting_direction"
    SELECTING_STABLECOIN = "selecting_stablecoin"
    SELECTING_NETWORK = "selecting_network"
    SELECTING_DEST_NETWORK = "selecting_dest_network"
    ENTERING_AMOUNT = "entering_amount"
    ENTERING_INVOICE = "entering_invoice"
    ENTERING_ADDRESS = "entering_address"
    ENTERING_ADDRESS_STABLE = "entering_address_stable"
    CONFIRMING = "confirming"
    AWAITING_PAYMENT = "awaiting_payment"
    COMPLETE = "complete"


@dataclass
class SwapSession:
    """Active swap session data for a user."""
    direction: str | None = None          # btc_ln, ln_btc, stable_to_btc, btc_to_stable
    source_amount: int | None = None      # in sats or cents
    dest_amount: int | None = None
    invoice: str | None = None            # Lightning invoice
    dest_address: str | None = None       # BTC on-chain address
    rate_info: RateInfo | None = None
    fee_breakdown: FeeBreakdown | None = None
    swap_id: str | None = None
    boltz_address: str | None = None      # Boltz deposit address
    boltz_expected_amount: int | None = None
    # Stablecoin fields
    stable_currency: str | None = None    # "USDT" or "USDC"
    stable_network: str | None = None     # e.g. "TRC-20", "ERC-20"
    stable_dest_network: str | None = None
    stable_source_amount: float | None = None  # in USDT/USDC units
    stable_dest_amount: float | None = None
    stable_rate_id: str | None = None     # ChangeNOW rateId
    stable_payin_address: str | None = None
    stable_memo: str | None = None        # Memo/tag for some chains


@dataclass
class UserState:
    """Current state for a WhatsApp user."""
    phone_hash: str
    state: UserStateType = UserStateType.IDLE
    session: SwapSession = field(default_factory=SwapSession)

    def start_direction_selection(self):
        """Transition to direction selection."""
        self.state = UserStateType.SELECTING_DIRECTION
        self.session = SwapSession()

    def select_stablecoin_direction(self, direction: str):
        """User selected stablecoin direction."""
        self.session.direction = direction
        self.state = UserStateType.SELECTING_STABLECOIN

    def select_direction(self, direction: str):
        """User selected a swap direction."""
        self.session.direction = direction
        if direction == "btc_ln":
            self.state = UserStateType.ENTERING_INVOICE
        elif direction == "ln_btc":
            self.state = UserStateType.ENTERING_ADDRESS
        else:
            self.state = UserStateType.ENTERING_AMOUNT

    def set_amount(self, amount: int, rate_info: RateInfo, fee: FeeBreakdown):
        """User entered an amount, ready for confirmation."""
        self.session.source_amount = amount
        self.session.rate_info = rate_info
        self.session.fee_breakdown = fee
        self.state = UserStateType.CONFIRMING

    def confirm(self):
        """User confirmed the swap."""
        self.state = UserStateType.AWAITING_PAYMENT

    def complete(self):
        """Swap completed."""
        self.state = UserStateType.COMPLETE

    def reset(self):
        """Reset to idle."""
        self.state = UserStateType.IDLE
        self.session = SwapSession()

    def to_dict(self) -> dict:
        """Serialize to JSON-friendly dict for storage."""
        d = {
            "state": self.state.value,
            "direction": self.session.direction,
            "source_amount": self.session.source_amount,
            "invoice": self.session.invoice,
            "dest_address": self.session.dest_address,
            "swap_id": self.session.swap_id,
            "stable_currency": self.session.stable_currency,
            "stable_network": self.session.stable_network,
            "stable_dest_network": self.session.stable_dest_network,
            "stable_source_amount": self.session.stable_source_amount,
            "stable_dest_amount": self.session.stable_dest_amount,
            "stable_rate_id": self.session.stable_rate_id,
        }
        if self.session.rate_info:
            d["rate_info"] = {
                "boltz_rate": self.session.rate_info.boltz_rate,
                "user_rate": self.session.rate_info.user_rate,
                "boltz_fee_pct": self.session.rate_info.boltz_fee_pct,
                "boltz_miner_fee": self.session.rate_info.boltz_miner_fee,
                "bot_commission_pct": self.session.rate_info.bot_commission_pct,
                "min_amount": self.session.rate_info.min_amount,
                "max_amount": self.session.rate_info.max_amount,
                "pair_hash": self.session.rate_info.pair_hash,
            }
        if self.session.fee_breakdown:
            d["fee_breakdown"] = {
                "source_amount": self.session.fee_breakdown.source_amount,
                "commission_rate": self.session.fee_breakdown.commission_rate,
                "commission_amount": self.session.fee_breakdown.commission_amount,
                "boltz_fee_rate": self.session.fee_breakdown.boltz_fee_rate,
                "boltz_fee_amount": self.session.fee_breakdown.boltz_fee_amount,
                "boltz_miner_fee": self.session.fee_breakdown.boltz_miner_fee,
                "total_fees": self.session.fee_breakdown.total_fees,
                "net_swap_amount": self.session.fee_breakdown.net_swap_amount,
                "estimated_receive": self.session.fee_breakdown.estimated_receive,
                "bot_profit": self.session.fee_breakdown.bot_profit,
            }
        return d

    @classmethod
    def from_dict(cls, phone_hash: str, data: dict) -> "UserState":
        """Deserialize from stored dict."""
        from swapbot.engine.rates import RateInfo
        us = cls(phone_hash=phone_hash)
        state_str = data.get("state", "idle")
        try:
            us.state = UserStateType(state_str)
        except ValueError:
            us.state = UserStateType.IDLE

        us.session.direction = data.get("direction")
        us.session.source_amount = data.get("source_amount")
        us.session.invoice = data.get("invoice")
        us.session.dest_address = data.get("dest_address")
        us.session.swap_id = data.get("swap_id")
        us.session.stable_currency = data.get("stable_currency")
        us.session.stable_network = data.get("stable_network")
        us.session.stable_dest_network = data.get("stable_dest_network")
        us.session.stable_source_amount = data.get("stable_source_amount")
        us.session.stable_dest_amount = data.get("stable_dest_amount")
        us.session.stable_rate_id = data.get("stable_rate_id")

        # Restore rate_info
        if data.get("rate_info"):
            ri = data["rate_info"]
            us.session.rate_info = RateInfo(
                boltz_rate=ri.get("boltz_rate", 0),
                user_rate=ri.get("user_rate", 0),
                boltz_fee_pct=ri.get("boltz_fee_pct", 0),
                boltz_miner_fee=ri.get("boltz_miner_fee", 0),
                bot_commission_pct=ri.get("bot_commission_pct", 0),
                min_amount=ri.get("min_amount", 0),
                max_amount=ri.get("max_amount", 0),
                pair_hash=ri.get("pair_hash", ""),
            )
        # Restore fee_breakdown
        if data.get("fee_breakdown"):
            from swapbot.engine.commission import FeeBreakdown
            fb = data["fee_breakdown"]
            us.session.fee_breakdown = FeeBreakdown(
                source_amount=fb.get("source_amount", 0),
                commission_rate=fb.get("commission_rate", 0),
                commission_amount=fb.get("commission_amount", 0),
                boltz_fee_rate=fb.get("boltz_fee_rate", 0),
                boltz_fee_amount=fb.get("boltz_fee_amount", 0),
                boltz_miner_fee=fb.get("boltz_miner_fee", 0),
                total_fees=fb.get("total_fees", 0),
                net_swap_amount=fb.get("net_swap_amount", 0),
                estimated_receive=fb.get("estimated_receive", 0),
                bot_profit=fb.get("bot_profit", 0),
            )
        return us
