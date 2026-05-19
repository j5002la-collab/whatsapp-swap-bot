"""Async HTTP client for Boltz Exchange API v2.
Ported from telegram-swap-bot/src/boltz/client.ts
"""

import logging
import time
import httpx
from typing import Any

from .types import (
    BoltzPair,
    BoltzPairLimits,
    BoltzPairFees,
    BoltzMinerFees,
    SubmarinePairs,
    ReversePairs,
    ChainPairs,
)

# Re-export for convenience
from .types import (
    SubmarineSwapRequest,
    SubmarineSwapResponse,
    ReverseSwapRequest,
    ReverseSwapResponse,
    ChainSwapRequest,
    ChainSwapResponse,
)

logger = logging.getLogger("boltz.client")


class BoltzClient:
    """Async HTTP client for Boltz Exchange API v2."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.http = httpx.AsyncClient(
            base_url=f"{self.base_url}/v2",
            headers={"Content-Type": "application/json"},
            timeout=20.0,
        )

    async def close(self):
        await self.http.aclose()

    # --- Pairs ---

    async def get_submarine_pairs(self) -> SubmarinePairs:
        """Fetch BTC→LN (submarine) pairs and limits."""
        response = await self.http.get("/swap/submarine")
        response.raise_for_status()
        data = response.json()
        return self._parse_pairs(data)

    async def get_reverse_pairs(self) -> ReversePairs:
        """Fetch LN→BTC (reverse) pairs and limits."""
        response = await self.http.get("/swap/reverse")
        response.raise_for_status()
        data = response.json()
        return self._parse_pairs(data)

    async def get_chain_pairs(self) -> ChainPairs:
        """Fetch on-chain swap pairs."""
        response = await self.http.get("/swap/chain")
        response.raise_for_status()
        data = response.json()
        return self._parse_pairs(data)

    def _parse_pairs(self, data: dict) -> dict:
        """Parse Boltz pair response into typed objects."""
        result = {}
        for from_cur, to_pairs in data.items():
            result[from_cur] = {}
            for to_cur, pair_data in to_pairs.items():
                fees_data = pair_data.get("fees", {})
                miner_fees_data = fees_data.get("minerFees", 0)
                if isinstance(miner_fees_data, dict):
                    miner_fees = BoltzMinerFees(
                        server=miner_fees_data.get("server", 0),
                        user=miner_fees_data.get("user"),
                        minerFees=miner_fees_data.get("minerFees", 0),
                    )
                else:
                    miner_fees = miner_fees_data

                limits_data = pair_data.get("limits", {})

                result[from_cur][to_cur] = BoltzPair(
                    hash=pair_data.get("hash", ""),
                    rate=pair_data.get("rate", 0),
                    limits=BoltzPairLimits(
                        maximal=limits_data.get("maximal", 0),
                        minimal=limits_data.get("minimal", 0),
                    ),
                    fees=BoltzPairFees(
                        percentage=fees_data.get("percentage", 0),
                        minerFees=miner_fees,
                    ),
                )
        return result

    # --- Swap Creation ---

    async def create_submarine_swap(
        self, params: SubmarineSwapRequest
    ) -> SubmarineSwapResponse:
        """Create a submarine swap (BTC on-chain → Lightning)."""
        logger.info(
            f"Creating submarine swap: {params.from_currency} → {params.to_currency}"
        )
        body = {
            "from": params.from_currency,
            "to": params.to_currency,
            "invoice": params.invoice,
            "refundPublicKey": params.refundPublicKey,
        }
        response = await self.http.post("/swap/submarine", json=body)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Submarine swap created: {data.get('id')}")
        return SubmarineSwapResponse(
            id=data.get("id", ""),
            address=data.get("address", ""),
            expectedAmount=data.get("expectedAmount", 0),
            bip21=data.get("bip21", ""),
            rate=data.get("rate", 0),
            timeoutBlockHeight=data.get("timeoutBlockHeight", 0),
            claimPublicKey=data.get("claimPublicKey", ""),
            swapTree=data.get("swapTree"),
        )

    async def create_reverse_swap(
        self, params: ReverseSwapRequest
    ) -> ReverseSwapResponse:
        """Create a reverse swap (Lightning → BTC on-chain)."""
        logger.info(
            f"Creating reverse swap: {params.from_currency} → {params.to_currency}"
        )
        body = {
            "from": params.from_currency,
            "to": params.to_currency,
            "invoiceAmount": params.invoiceAmount,
            "claimPublicKey": params.claimPublicKey,
            "preimageHash": params.preimageHash,
        }
        if params.address:
            body["address"] = params.address
        response = await self.http.post("/swap/reverse", json=body)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Reverse swap created: {data.get('id')}")
        return ReverseSwapResponse(
            id=data.get("id", ""),
            invoice=data.get("invoice", ""),
            lockupAddress=data.get("lockupAddress", ""),
            expectedAmount=data.get("expectedAmount", 0),
            rate=data.get("rate", 0),
            timeoutBlockHeight=data.get("timeoutBlockHeight", 0),
            refundPublicKey=data.get("refundPublicKey", ""),
            swapTree=data.get("swapTree"),
        )

    async def create_chain_swap(
        self, params: ChainSwapRequest
    ) -> ChainSwapResponse:
        """Create a chain swap (on-chain → on-chain)."""
        logger.info(
            f"Creating chain swap: {params.from_currency} → {params.to_currency}"
        )
        body = {
            "from": params.from_currency,
            "to": params.to_currency,
            "userLockAmount": params.userLockAmount,
        }
        response = await self.http.post("/swap/chain", json=body)
        response.raise_for_status()
        data = response.json()
        return ChainSwapResponse(
            id=data.get("id", ""),
            lockupAddress=data.get("lockupAddress", ""),
            serverLockupAddress=data.get("serverLockupAddress", ""),
            expectedAmount=data.get("expectedAmount", 0),
            rate=data.get("rate", 0),
            timeoutBlockHeight=data.get("timeoutBlockHeight", 0),
        )

    # --- Swap Status ---

    async def get_swap_status(self, swap_id: str) -> str:
        """Get swap status by ID via REST."""
        try:
            response = await self.http.get(f"/swap/{swap_id}")
            data = response.json()
            return data.get("status", "unknown")
        except Exception:
            return "unknown"

    def get_websocket_url(self) -> str:
        """Get WebSocket URL for swap status updates."""
        ws_url = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{ws_url}/v2/ws"
