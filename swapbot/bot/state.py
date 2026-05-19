"""User state machine for WhatsApp swap flow."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from swapbot.engine.rates import RateInfo
from swapbot.engine.commission import FeeBreakdown


class UserStateType(Enum):
    IDLE = "idle"
    SELECTING_DIRECTION = "selecting_direction"
    ENTERING_AMOUNT = "entering_amount"
    ENTERING_INVOICE = "entering_invoice"
    ENTERING_ADDRESS = "entering_address"
    CONFIRMING = "confirming"
    AWAITING_PAYMENT = "awaiting_payment"
    COMPLETE = "complete"


@dataclass
class SwapSession:
    """Active swap session data for a user."""
    direction: str | None = None          # btc_ln, ln_btc, usdt_btc, btc_usdt
    source_amount: int | None = None      # in sats
    dest_amount: int | None = None
    invoice: str | None = None            # Lightning invoice
    dest_address: str | None = None       # BTC on-chain address
    rate_info: RateInfo | None = None
    fee_breakdown: FeeBreakdown | None = None
    swap_id: str | None = None
    boltz_address: str | None = None      # Boltz deposit address
    boltz_expected_amount: int | None = None


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
        return {
            "state": self.state.value,
            "direction": self.session.direction,
            "source_amount": self.session.source_amount,
            "invoice": self.session.invoice,
            "dest_address": self.session.dest_address,
            "swap_id": self.session.swap_id,
        }

    @classmethod
    def from_dict(cls, phone_hash: str, data: dict) -> "UserState":
        """Deserialize from stored dict."""
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
        return us
