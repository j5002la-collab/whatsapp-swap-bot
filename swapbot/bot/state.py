"""User state machine for WhatsApp swap flow.
Updated for ChangeNOW universal swap flow with i18n.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class UserStateType(Enum):
    IDLE = "idle"
    SELECTING_SOURCE_CATEGORY = "selecting_source_category"
    SELECTING_SOURCE_CURRENCY = "selecting_source_currency"
    SELECTING_SOURCE_NETWORK = "selecting_source_network"
    SELECTING_DEST_CATEGORY = "selecting_dest_category"
    SELECTING_DEST_CURRENCY = "selecting_dest_currency"
    SELECTING_DEST_NETWORK = "selecting_dest_network"
    ENTERING_AMOUNT = "entering_amount"
    ENTERING_DEST_ADDRESS = "entering_dest_address"
    CONFIRMING = "confirming"
    AWAITING_PAYMENT = "awaiting_payment"
    COMPLETE = "complete"


@dataclass
class SwapSession:
    """Active swap session data for a user."""
    # Source
    from_ticker: str | None = None
    from_network: str | None = None
    # Destination
    to_ticker: str | None = None
    to_network: str | None = None
    # Amounts
    from_amount: str | None = None
    to_amount: str | None = None
    rate: float = 0.0
    rate_id: str | None = None
    # Address
    dest_address: str | None = None
    extra_id: str | None = None
    # Result
    swap_id: str | None = None
    exchange_id: str | None = None
    payin_address: str | None = None
    # UI state
    source_category_page: int = 0
    dest_category_page: int = 0
    current_category: str = "popular"


@dataclass
class UserState:
    """Current state for a WhatsApp user."""
    phone_hash: str
    state: UserStateType = UserStateType.IDLE
    session: SwapSession = field(default_factory=SwapSession)

    def start_swap(self):
        """Start a new swap flow."""
        self.state = UserStateType.SELECTING_SOURCE_CATEGORY
        self.session = SwapSession()

    def select_source_category(self, category: str):
        self.session.current_category = category
        self.state = UserStateType.SELECTING_SOURCE_CURRENCY

    def select_source_currency(self, ticker: str):
        self.session.from_ticker = ticker
        self.state = UserStateType.SELECTING_SOURCE_NETWORK

    def select_source_network(self, network: str):
        self.session.from_network = network
        self.state = UserStateType.SELECTING_DEST_CATEGORY

    def select_dest_category(self, category: str):
        self.session.current_category = category
        self.state = UserStateType.SELECTING_DEST_CURRENCY

    def select_dest_currency(self, ticker: str):
        self.session.to_ticker = ticker
        self.state = UserStateType.SELECTING_DEST_NETWORK

    def select_dest_network(self, network: str):
        self.session.to_network = network
        self.state = UserStateType.ENTERING_AMOUNT

    def set_amount_estimation(self, from_amount: str, to_amount: str, rate: float, rate_id: str):
        self.session.from_amount = from_amount
        self.session.to_amount = to_amount
        self.session.rate = rate
        self.session.rate_id = rate_id
        self.state = UserStateType.ENTERING_DEST_ADDRESS

    def set_dest_address(self, address: str, extra_id: str | None = None):
        self.session.dest_address = address
        self.session.extra_id = extra_id
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

    def quick_select_pair(self, from_ticker: str, to_ticker: str, from_network: str):
        """Quick select a popular pair, skipping category selection."""
        self.session.from_ticker = from_ticker
        self.session.to_ticker = to_ticker
        self.session.from_network = from_network
        self.state = UserStateType.SELECTING_DEST_NETWORK

    def to_dict(self) -> dict:
        """Serialize to JSON-friendly dict for storage."""
        return {
            "state": self.state.value,
            "from_ticker": self.session.from_ticker,
            "from_network": self.session.from_network,
            "to_ticker": self.session.to_ticker,
            "to_network": self.session.to_network,
            "from_amount": self.session.from_amount,
            "to_amount": self.session.to_amount,
            "rate": self.session.rate,
            "rate_id": self.session.rate_id,
            "dest_address": self.session.dest_address,
            "extra_id": self.session.extra_id,
            "swap_id": self.session.swap_id,
            "exchange_id": self.session.exchange_id,
            "payin_address": self.session.payin_address,
            "source_category_page": self.session.source_category_page,
            "dest_category_page": self.session.dest_category_page,
            "current_category": self.session.current_category,
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

        us.session.from_ticker = data.get("from_ticker")
        us.session.from_network = data.get("from_network")
        us.session.to_ticker = data.get("to_ticker")
        us.session.to_network = data.get("to_network")
        us.session.from_amount = data.get("from_amount")
        us.session.to_amount = data.get("to_amount")
        us.session.rate = data.get("rate", 0)
        us.session.rate_id = data.get("rate_id")
        us.session.dest_address = data.get("dest_address")
        us.session.extra_id = data.get("extra_id")
        us.session.swap_id = data.get("swap_id")
        us.session.exchange_id = data.get("exchange_id")
        us.session.payin_address = data.get("payin_address")
        us.session.source_category_page = data.get("source_category_page", 0)
        us.session.dest_category_page = data.get("dest_category_page", 0)
        us.session.current_category = data.get("current_category", "popular")
        return us
