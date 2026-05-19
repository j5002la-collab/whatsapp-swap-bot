"""Data types for Boltz API v2."""

from dataclasses import dataclass, field
from typing import Literal


# Currency identifiers
BoltzCurrency = Literal["BTC", "L-BTC", "RBTC", "TBTC", "USDT", "USDC", "ARK"]

# Swap directions
SwapDirection = Literal["btc_ln", "ln_btc", "usdt_btc", "btc_usdt"]


@dataclass
class BoltzMinerFees:
    server: int = 0
    user: dict | None = None  # {"claim": int, "lockup": int}
    minerFees: int = 0


@dataclass
class BoltzPairFees:
    percentage: float = 0.0
    minerFees: BoltzMinerFees | int = 0


@dataclass
class BoltzPairLimits:
    maximal: int = 0
    minimal: int = 0
    maximalZeroConf: int = 0
    minimalBatched: int = 0


@dataclass
class BoltzPair:
    hash: str = ""
    rate: float = 0.0
    limits: BoltzPairLimits = field(default_factory=BoltzPairLimits)
    fees: BoltzPairFees = field(default_factory=BoltzPairFees)


@dataclass
class SubmarineSwapRequest:
    from_currency: str  # "BTC"
    to_currency: str  # "BTC"
    invoice: str  # Lightning invoice (lnbc...)
    refundPublicKey: str  # Refund path public key hex


@dataclass
class SubmarineSwapResponse:
    id: str = ""
    address: str = ""  # On-chain address to send funds to
    expectedAmount: int = 0  # In sats
    bip21: str = ""
    rate: float = 0.0
    timeoutBlockHeight: int = 0
    claimPublicKey: str = ""
    swapTree: dict | None = None


@dataclass
class ReverseSwapRequest:
    from_currency: str
    to_currency: str
    invoiceAmount: int  # in sats
    claimPublicKey: str
    preimageHash: str
    address: str = ""


@dataclass
class ReverseSwapResponse:
    id: str = ""
    invoice: str = ""  # Lightning invoice to pay
    lockupAddress: str = ""
    expectedAmount: int = 0
    rate: float = 0.0
    timeoutBlockHeight: int = 0
    refundPublicKey: str = ""
    swapTree: dict | None = None


@dataclass
class ChainSwapRequest:
    from_currency: str
    to_currency: str
    userLockAmount: int


@dataclass
class ChainSwapResponse:
    id: str = ""
    lockupAddress: str = ""
    serverLockupAddress: str = ""
    expectedAmount: int = 0
    rate: float = 0.0
    timeoutBlockHeight: int = 0


# WebSocket status types
SubmarineSwapStatus = Literal[
    "swap.created",
    "invoice.set",
    "transaction.mempool",
    "transaction.confirmed",
    "invoice.pending",
    "invoice.paid",
    "invoice.failedToPay",
    "transaction.claim.pending",
    "transaction.claimed",
    "transaction.lockupFailed",
    "swap.expired",
]

ReverseSwapStatus = Literal[
    "swap.created",
    "minerfee.paid",
    "transaction.mempool",
    "transaction.confirmed",
    "invoice.expired",
    "invoice.settled",
    "transaction.failed",
    "swap.expired",
    "transaction.refunded",
]

ChainSwapStatus = Literal[
    "swap.created",
    "transaction.mempool",
    "transaction.confirmed",
    "transaction.server.mempool",
    "transaction.server.confirmed",
    "transaction.claim.pending",
    "transaction.claimed",
    "transaction.lockupFailed",
    "swap.expired",
]

BoltzSwapStatus = SubmarineSwapStatus | ReverseSwapStatus | ChainSwapStatus

# API response types
SubmarinePairs = dict[str, dict[str, BoltzPair]]
ReversePairs = dict[str, dict[str, BoltzPair]]
ChainPairs = dict[str, dict[str, BoltzPair]]
