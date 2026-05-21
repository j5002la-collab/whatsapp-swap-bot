"""Blink.sv GraphQL API client for LN invoice generation and balance checking."""

import logging
import httpx

logger = logging.getLogger("blink.client")

BLINK_GRAPHQL_URL = "https://api.blink.sv/graphql"


class BlinkClient:
    """Async client for Blink.sv GraphQL API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.http = httpx.AsyncClient(
            base_url=BLINK_GRAPHQL_URL,
            headers={
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        self._btc_wallet_id: str | None = None

    async def close(self):
        await self.http.aclose()

    async def _graphql(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query."""
        body = {"query": query}
        if variables:
            body["variables"] = variables
        try:
            resp = await self.http.post("", json=body)
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data:
                logger.error(f"GraphQL errors: {data['errors']}")
            return data.get("data", {})
        except Exception as e:
            logger.error(f"Blink API error: {e}")
            return {}

    async def get_btc_wallet_id(self) -> str | None:
        """Get the BTC wallet ID."""
        if self._btc_wallet_id:
            return self._btc_wallet_id

        query = """
        query Me {
            me {
                defaultAccount {
                    wallets {
                        id
                        walletCurrency
                        balance
                    }
                }
            }
        }
        """
        data = await self._graphql(query)
        wallets = (
            data.get("me", {})
            .get("defaultAccount", {})
            .get("wallets", [])
        )
        for w in wallets:
            if w.get("walletCurrency") == "BTC":
                self._btc_wallet_id = w["id"]
                logger.info(f"Blink BTC wallet: {w['id']}  balance: {w['balance']} sats")
                return w["id"]
        logger.error("No BTC wallet found in Blink account")
        return None

    async def get_btc_balance(self) -> int:
        """Get BTC balance in sats."""
        wallet_id = await self.get_btc_wallet_id()
        if not wallet_id:
            return 0

        query = """
        query Me {
            me {
                defaultAccount {
                    wallets {
                        walletCurrency
                        balance
                    }
                }
            }
        }
        """
        data = await self._graphql(query)
        wallets = (
            data.get("me", {})
            .get("defaultAccount", {})
            .get("wallets", [])
        )
        for w in wallets:
            if w.get("walletCurrency") == "BTC":
                return w.get("balance", 0)
        return 0

    async def create_invoice(
        self, amount_sats: int, memo: str = ""
    ) -> dict | None:
        """Create a Lightning invoice. Returns {paymentRequest, paymentHash, ...}."""
        wallet_id = await self.get_btc_wallet_id()
        if not wallet_id:
            return None

        query = """
        mutation LnInvoiceCreate($input: LnInvoiceCreateInput!) {
            lnInvoiceCreate(input: $input) {
                invoice {
                    paymentRequest
                    paymentHash
                    paymentSecret
                    satoshis
                }
                errors {
                    message
                }
            }
        }
        """
        variables = {
            "input": {
                "walletId": wallet_id,
                "amount": amount_sats,
                "memo": memo or "SwapBot",
            }
        }
        data = await self._graphql(query, variables)
        invoice_data = (
            data.get("lnInvoiceCreate", {})
            .get("invoice", {})
        )
        if invoice_data.get("paymentRequest"):
            logger.info(
                f"Blink invoice: {amount_sats} sats  "
                f"hash={invoice_data['paymentHash'][:16]}..."
            )
            return invoice_data
        return None


# Singleton
_blink: BlinkClient | None = None


def get_blink() -> BlinkClient | None:
    return _blink


def init_blink(api_key: str) -> BlinkClient:
    global _blink
    _blink = BlinkClient(api_key)
    return _blink
