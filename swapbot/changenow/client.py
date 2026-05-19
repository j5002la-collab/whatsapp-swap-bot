"""ChangeNOW API client for stablecoin swaps (USDT/USDC).
Ported from telegram-swap-bot/src/changenow/client.ts

Supports: USDT (TRC-20, ERC-20, BEP-20, ARBITRUM, SOLANA, POLYGON, OPTIMISM, AVALANCHE, BASE)
          USDC (ERC-20, ARBITRUM, BASE, SOLANA, POLYGON, OPTIMISM, AVALANCHE)
"""

import logging
import httpx

logger = logging.getLogger("changenow.client")

# Network → { ticker, network } mapping for ChangeNOW v2
USDT_NETWORKS: dict[str, dict[str, str]] = {
    "TRC-20":     {"ticker": "usdt", "network": "trc20"},
    "ERC-20":     {"ticker": "usdt", "network": "eth"},
    "BEP-20":     {"ticker": "usdt", "network": "bsc"},
    "ARBITRUM":   {"ticker": "usdt", "network": "arbitrum"},
    "SOLANA":     {"ticker": "usdt", "network": "sol"},
    "POLYGON":    {"ticker": "usdt", "network": "matic"},
    "OPTIMISM":   {"ticker": "usdt", "network": "op"},
    "AVALANCHE":  {"ticker": "usdt", "network": "avaxc"},
    "BASE":       {"ticker": "usdt", "network": "base"},
}

USDC_NETWORKS: dict[str, dict[str, str]] = {
    "ERC-20":     {"ticker": "usdc", "network": "eth"},
    "ARBITRUM":   {"ticker": "usdc", "network": "arbitrum"},
    "BASE":       {"ticker": "usdc", "network": "base"},
    "SOLANA":     {"ticker": "usdc", "network": "sol"},
    "POLYGON":    {"ticker": "usdc", "network": "matic"},
    "OPTIMISM":   {"ticker": "usdc", "network": "op"},
    "AVALANCHE":  {"ticker": "usdc", "network": "avaxc"},
    "BEP-20":     {"ticker": "usdc", "network": "bsc"},
}


class ChangeNowClient:
    """Async client for ChangeNOW v2 API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.changenow.io/v2"
        self.http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "x-changenow-api-key": api_key,
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )

    async def close(self):
        await self.http.aclose()

    # --- Estimate ---

    async def estimate(
        self,
        from_currency: str,
        to_currency: str,
        from_amount: str,
        from_network: str | None = None,
        to_network: str | None = None,
    ) -> dict:
        """Get estimated exchange amount."""
        params: dict = {
            "fromCurrency": from_currency,
            "toCurrency": to_currency,
            "fromAmount": from_amount,
            "flow": "fixed-rate",
        }
        if from_network:
            params["fromNetwork"] = from_network
        if to_network:
            params["toNetwork"] = to_network

        try:
            resp = await self.http.get("/exchange/estimated-amount", params=params)
            return resp.json()
        except Exception as e:
            logger.error(f"ChangeNOW estimate error: {e}")
            raise

    # --- Min amount ---

    async def get_min_amount(
        self,
        from_currency: str,
        to_currency: str,
        from_network: str | None = None,
        to_network: str | None = None,
    ) -> str:
        """Get minimum exchange amount."""
        params: dict = {
            "fromCurrency": from_currency,
            "toCurrency": to_currency,
            "flow": "fixed-rate",
        }
        if from_network:
            params["fromNetwork"] = from_network
        if to_network:
            params["toNetwork"] = to_network

        try:
            resp = await self.http.get("/exchange/min-amount", params=params)
            data = resp.json()
            return data.get("minAmount", "0")
        except Exception as e:
            logger.error(f"ChangeNOW min-amount error: {e}")
            return "0"

    # --- Create Exchange ---

    async def create_exchange(self, params: dict) -> dict:
        """Create a fixed-rate exchange."""
        try:
            resp = await self.http.post("/exchange", json=params)
            data = resp.json()
            logger.info(f"ChangeNOW exchange created: {data.get('id')}")
            return data
        except Exception as e:
            logger.error(f"ChangeNOW create error: {e}")
            raise

    # --- Status ---

    async def get_status(self, exchange_id: str) -> dict:
        """Get exchange status by ID."""
        try:
            resp = await self.http.get("/exchange/by-id", params={"id": exchange_id})
            return resp.json()
        except Exception as e:
            logger.error(f"ChangeNOW status error: {e}")
            raise

    # --- Network mapping ---

    def get_ticker(self, currency: str, network: str) -> dict | None:
        """Map currency+network to ChangeNOW {ticker, network} pair."""
        if currency.upper() == "USDT":
            return USDT_NETWORKS.get(network.upper())
        if currency.upper() == "USDC":
            return USDC_NETWORKS.get(network.upper())
        return None

    def get_all_networks(self, currency: str) -> list[str]:
        """Get all supported networks for a currency."""
        if currency.upper() == "USDT":
            return list(USDT_NETWORKS.keys())
        if currency.upper() == "USDC":
            return list(USDC_NETWORKS.keys())
        return []


# Singleton
_cn_client: ChangeNowClient | None = None


def get_cn_client() -> ChangeNowClient | None:
    return _cn_client


def init_cn_client(api_key: str) -> ChangeNowClient:
    global _cn_client
    _cn_client = ChangeNowClient(api_key)
    logger.info("ChangeNOW client initialized")
    return _cn_client
