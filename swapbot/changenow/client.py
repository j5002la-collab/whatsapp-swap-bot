"""ChangeNOW API v2 client — universal crypto swap provider.
Supports ALL currencies available through ChangeNOW (900+ assets across 100+ networks).

API docs: https://documenter.getpostman.com/view/8180765/SVfTPnMc
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("changenow.client")

CHANGENOW_API = "https://api.changenow.io/v2"

# Currency categories for UX menus (ticker → category)
_CURRENCY_CATEGORIES: dict[str, str] = {}

# Default popular tickers
_POPULAR_TICKERS = {"btc", "eth", "usdt", "usdc", "sol", "xrp", "doge", "ltc", "ada", "dot", "matic", "bnb", "avax", "link", "uni"}

# Category ticker sets (lowercase)
_CAT_STABLECOINS = {"usdt", "usdc", "dai", "busd", "usdp", "tusd", "frax", "usdd", "usde", "fdusd", "usdx", "crvusd", "pyusd", "usdt.e", "usdc.e"}
_CAT_BTC = {"btc", "wbtc", "renbtc", "tbtc", "sbtc", "bbtc", "cbbtc", "btcb", "btc.b", "btc.bsc"}
_CAT_L1L2 = {"eth", "sol", "avax", "matic", "bnb", "ada", "dot", "atom", "near", "apt", "sui", "sei", "op", "arb", "zksync", "base", "linea", "scroll", "maticpol"}
_CAT_DEFI = {"uni", "aave", "link", "crv", "sushi", "1inch", "comp", "mkr", "snx", "yfi", "bal", "gns", "pendle", "ldo", "rpl", "ankr", "gmx", "jup", "ray"}
_CAT_MEME = {"doge", "shib", "pepe", "floki", "bonk", "wif", "meme", "bome", "popcat", "moodeng", "brett", "turbo", "dogeverse"}
_CAT_PRIVACY = {"xmr", "zec", "dash", "scrt", "rose", "xvg", "arrr"}


@dataclass
class CurrencyInfo:
    """Normalized currency info from ChangeNOW."""
    ticker: str
    name: str
    network: str
    network_display: str  # User-friendly network name
    image: str = ""
    has_extra_id: bool = False
    contract: str = ""


@dataclass
class EstimatedAmount:
    """Result of an exchange estimate."""
    from_amount: str
    to_amount: str
    rate: float
    rate_id: str = ""
    valid_until: str = ""
    min_amount: str = ""
    max_amount: str = ""


@dataclass
class ExchangeResult:
    """Result of a created exchange."""
    id: str
    payin_address: str
    payout_address: str
    from_amount: str
    to_amount: str
    from_currency: str
    to_currency: str
    from_network: str
    to_network: str
    extra_id: str | None = None
    status: str = "waiting"


class ChangeNowClient:
    """Async client for ChangeNOW v2 API — full currency support."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.http = httpx.AsyncClient(
            base_url=CHANGENOW_API,
            headers={
                "x-changenow-api-key": api_key,
                "Content-Type": "application/json",
            },
            timeout=20.0,
        )
        # Currency cache
        self._currencies: list[CurrencyInfo] = []
        self._currencies_by_ticker: dict[str, list[CurrencyInfo]] = {}
        self._categories: dict[str, list[str]] = {}
        self._cache_ts: float = 0
        self._cache_ttl: int = 3600  # 1 hour

    async def close(self):
        await self.http.aclose()

    # ── Currency Discovery ──

    async def fetch_currencies(self, force: bool = False) -> list[CurrencyInfo]:
        """Fetch all available currencies from ChangeNOW.
        Cached for 1 hour. Set force=True to bypass cache.
        """
        now = time.time()
        if not force and self._currencies and (now - self._cache_ts) < self._cache_ttl:
            return self._currencies

        try:
            resp = await self.http.get("/exchange/currencies", params={
                "active": "true",
                "flow": "fixed-rate",
            })
            data = resp.json()
            currencies = self._parse_currencies(data)
            self._currencies = currencies
            self._build_indexes(currencies)
            self._cache_ts = time.time()
            logger.info(f"Fetched {len(currencies)} currencies from ChangeNOW")
            return currencies
        except Exception as e:
            logger.error(f"Failed to fetch currencies: {e}")
            return self._currencies  # Return stale cache if available

    def _parse_currencies(self, data: list) -> list[CurrencyInfo]:
        """Parse ChangeNOW currency list into normalized CurrencyInfo objects."""
        result = []
        for item in data:
            ticker = (item.get("ticker") or "").lower()
            name = item.get("name", ticker.upper())
            network = item.get("network", "mainnet")
            # Make network name user-friendly
            network_display = self._normalize_network(network)
            has_extra = item.get("hasExtraId", False)

            result.append(CurrencyInfo(
                ticker=ticker,
                name=name,
                network=network,
                network_display=network_display,
                image=item.get("image", ""),
                has_extra_id=has_extra,
                contract=item.get("contract", ""),
            ))
        return result

    @staticmethod
    def _normalize_network(net: str) -> str:
        """Convert ChangeNOW network codes to user-friendly names."""
        mapping = {
            "btc": "Bitcoin",
            "eth": "Ethereum (ERC-20)",
            "bsc": "BNB Smart Chain (BEP-20)",
            "trc20": "Tron (TRC-20)",
            "sol": "Solana",
            "matic": "Polygon",
            "arbitrum": "Arbitrum",
            "op": "Optimism",
            "base": "Base",
            "avaxc": "Avalanche C-Chain",
            "ftm": "Fantom",
            "one": "Harmony",
            "near": "NEAR",
            "algo": "Algorand",
            "xtz": "Tezos",
            "xlm": "Stellar",
            "xrp": "Ripple",
            "xdai": "Gnosis Chain",
            "zksync": "zkSync Era",
            "linea": "Linea",
            "scroll": "Scroll",
            "cronos": "Cronos",
            "kava": "Kava",
            "celo": "Celo",
            "aptos": "Aptos",
            "sui": "Sui",
            "sei": "Sei",
            "osmosis": "Osmosis",
            "inj": "Injective",
            "atom": "Cosmos",
            "dot": "Polkadot",
            "ksm": "Kusama",
            "fil": "Filecoin",
            "ltc": "Litecoin",
            "doge": "Dogecoin",
            "bch": "Bitcoin Cash",
            "zec": "Zcash",
            "dash": "Dash",
            "xmr": "Monero",
            "waves": "Waves",
            "eos": "EOS",
            "icp": "ICP",
            "hnt": "Helium",
            "hbar": "Hedera",
            "vet": "VeChain",
            "neo": "NEO",
            "flow": "Flow",
            "stx": "Stacks",
            "ton": "TON",
            "cardano": "Cardano",
            "ripple": "Ripple",
            "stellar": "Stellar",
        }
        return mapping.get(net.lower(), net.upper())

    def _build_indexes(self, currencies: list[CurrencyInfo]):
        """Build lookup indexes from currency list."""
        self._currencies_by_ticker = {}
        for c in currencies:
            t = c.ticker
            if t not in self._currencies_by_ticker:
                self._currencies_by_ticker[t] = []
            self._currencies_by_ticker[t].append(c)

        # Build categories
        self._categories = {}
        tickers = set(c.ticker for c in currencies)

        # Popular: intersection of popular tickers and available
        self._categories["popular"] = sorted(
            _POPULAR_TICKERS & tickers,
            key=lambda t: list(_POPULAR_TICKERS).index(t)
        )

        # Other categories: intersection with available
        self._categories["btc"] = sorted(_CAT_BTC & tickers)
        self._categories["stablecoins"] = sorted(_CAT_STABLECOINS & tickers)
        self._categories["l1l2"] = sorted(_CAT_L1L2 & tickers)
        self._categories["defi"] = sorted(_CAT_DEFI & tickers)
        self._categories["meme"] = sorted(_CAT_MEME & tickers)
        self._categories["privacy"] = sorted(_CAT_PRIVACY & tickers)

    def get_categories(self) -> dict[str, list[str]]:
        """Get categorized currency tickers."""
        return self._categories

    def get_networks(self, ticker: str) -> list[CurrencyInfo]:
        """Get all network options for a currency ticker."""
        return self._currencies_by_ticker.get(ticker.lower(), [])

    def get_currency_info(self, ticker: str, network: str | None = None) -> CurrencyInfo | None:
        """Get specific CurrencyInfo by ticker and optional network."""
        variants = self._currencies_by_ticker.get(ticker.lower(), [])
        if not variants:
            return None
        if network:
            for v in variants:
                if v.network == network:
                    return v
        return variants[0]  # Default: first network

    def search_currencies(self, query: str) -> list[str]:
        """Search currencies by name or ticker. Returns list of matching tickers."""
        query = query.lower().strip()
        results = set()
        for ticker, variants in self._currencies_by_ticker.items():
            if query in ticker:
                results.add(ticker)
                continue
            for v in variants:
                if query in v.name.lower():
                    results.add(ticker)
                    break
        return sorted(results)[:20]

    # ── Estimates ──

    async def estimate(
        self,
        from_ticker: str,
        to_ticker: str,
        from_amount: str,
        from_network: str,
        to_network: str,
    ) -> EstimatedAmount | None:
        """Get estimated exchange amount for a fixed-rate swap."""
        try:
            resp = await self.http.get("/exchange/estimated-amount", params={
                "fromCurrency": from_ticker,
                "toCurrency": to_ticker,
                "fromAmount": from_amount,
                "fromNetwork": from_network,
                "toNetwork": to_network,
                "flow": "fixed-rate",
            })
            data = resp.json()
            if "error" in data:
                logger.warning(f"Estimate error: {data.get('error', data.get('message', 'unknown'))}")
                return None

            return EstimatedAmount(
                from_amount=str(data.get("fromAmount", from_amount)),
                to_amount=str(data.get("toAmount", "0")),
                rate=float(data.get("toAmount", 0)) / float(from_amount) if float(from_amount) > 0 else 0,
                rate_id=data.get("rateId", ""),
                valid_until=data.get("validUntil", ""),
                min_amount=str(data.get("minAmount", "0")),
                max_amount=str(data.get("maxAmount", "999999")),
            )
        except Exception as e:
            logger.error(f"Estimate error: {e}")
            return None

    async def get_min_amount(
        self,
        from_ticker: str,
        to_ticker: str,
        from_network: str,
        to_network: str,
    ) -> str:
        """Get minimum exchangeable amount."""
        try:
            resp = await self.http.get("/exchange/min-amount", params={
                "fromCurrency": from_ticker,
                "toCurrency": to_ticker,
                "fromNetwork": from_network,
                "toNetwork": to_network,
                "flow": "fixed-rate",
            })
            data = resp.json()
            return str(data.get("minAmount", "0"))
        except Exception as e:
            logger.error(f"Min amount error: {e}")
            return "0"

    # ── Exchange Creation ──

    async def create_exchange(
        self,
        from_ticker: str,
        to_ticker: str,
        from_amount: str,
        from_network: str,
        to_network: str,
        address: str,
        extra_id: str | None = None,
        rate_id: str | None = None,
        refund_address: str | None = None,
    ) -> ExchangeResult | None:
        """Create a fixed-rate exchange."""
        body: dict = {
            "fromCurrency": from_ticker,
            "toCurrency": to_ticker,
            "fromNetwork": from_network,
            "toNetwork": to_network,
            "fromAmount": from_amount,
            "address": address,
            "flow": "fixed-rate",
        }
        if extra_id:
            body["extraId"] = extra_id
        if rate_id:
            body["rateId"] = rate_id
        if refund_address:
            body["refundAddress"] = refund_address

        try:
            resp = await self.http.post("/exchange", json=body)
            data = resp.json()
            if "error" in data:
                logger.error(f"Create exchange error: {data.get('error', data)}")
                return None

            return ExchangeResult(
                id=data.get("id", ""),
                payin_address=data.get("payinAddress", ""),
                payout_address=data.get("payoutAddress", ""),
                from_amount=str(data.get("fromAmount", data.get("amount", {}).get("from", from_amount))),
                to_amount=str(data.get("toAmount", data.get("amount", {}).get("to", "0"))),
                from_currency=data.get("fromCurrency", from_ticker),
                to_currency=data.get("toCurrency", to_ticker),
                from_network=data.get("fromNetwork", from_network),
                to_network=data.get("toNetwork", to_network),
                extra_id=data.get("extraId"),
                status=data.get("status", "waiting"),
            )
        except Exception as e:
            logger.error(f"Create exchange error: {e}")
            return None

    # ── Status ──

    async def get_status(self, exchange_id: str) -> dict | None:
        """Get exchange status by ID."""
        try:
            resp = await self.http.get("/exchange/by-id", params={"id": exchange_id})
            data = resp.json()
            if "error" in data:
                logger.warning(f"Status error for {exchange_id}: {data.get('error')}")
                return None
            return data
        except Exception as e:
            logger.error(f"Status error for {exchange_id}: {e}")
            return None

    async def get_statuses(self, exchange_ids: list[str]) -> list[dict]:
        """Get status for multiple exchanges in bulk."""
        try:
            resp = await self.http.get("/exchange/by-ids", params={
                "ids": ",".join(exchange_ids)
            })
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Bulk status error: {e}")
            return []

    # ── Pair Validation ──

    async def is_pair_available(
        self,
        from_ticker: str,
        to_ticker: str,
        from_network: str,
        to_network: str,
    ) -> bool:
        """Check if a currency pair is available for exchange."""
        try:
            min_amount = await self.get_min_amount(
                from_ticker, to_ticker, from_network, to_network
            )
            return float(min_amount) > 0
        except Exception:
            return False


# Singleton pattern
_cn_client: ChangeNowClient | None = None


def get_cn_client() -> ChangeNowClient | None:
    return _cn_client


def init_cn_client(api_key: str) -> ChangeNowClient:
    global _cn_client
    _cn_client = ChangeNowClient(api_key)
    logger.info("ChangeNOW client initialized")
    return _cn_client
