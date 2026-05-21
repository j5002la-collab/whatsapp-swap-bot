"""BTC wallet operations: key management, balance, sending, deposit monitoring.
Uses bitcoinlib for key derivation and transaction building.
Mempool.space API for UTXO discovery and broadcasting.
"""

import asyncio
import logging
import httpx
from bitcoinlib.keys import Key
from bitcoinlib.transactions import Transaction

logger = logging.getLogger("btc.wallet")

# Default mempool API
MEMPOOL_API = "https://mempool.space/api"


class BtcWallet:
    """Manages a BTC wallet from a WIF private key."""

    def __init__(self, wif: str, mempool_api: str = MEMPOOL_API):
        self.key = Key(wif)
        self.address = self.key.address(compressed=True, encoding="bech32")
        self.mempool_api = mempool_api.rstrip("/")
        self.http = httpx.AsyncClient(timeout=30.0)
        logger.info(f"BTC wallet loaded: {self.address}")

    async def close(self):
        await self.http.aclose()

    def derive_address(self) -> str:
        """Return the wallet's BTC address."""
        return self.address

    async def get_balance(self) -> int:
        """Get confirmed balance in sats from mempool.space."""
        try:
            resp = await self.http.get(
                f"{self.mempool_api}/address/{self.address}"
            )
            data = resp.json()
            chain_stats = data.get("chain_stats", {})
            funded = chain_stats.get("funded_txo_sum", 0)
            spent = chain_stats.get("spent_txo_sum", 0)
            return funded - spent
        except Exception as e:
            logger.error(f"Balance check error: {e}")
            return 0

    async def get_utxos(self) -> list[dict]:
        """Fetch UTXOs from mempool.space."""
        try:
            resp = await self.http.get(
                f"{self.mempool_api}/address/{self.address}/utxo"
            )
            return resp.json()
        except Exception as e:
            logger.error(f"UTXO fetch error: {e}")
            return []

    async def get_tx_confirmations(self, txid: str) -> int:
        """Get confirmations for a transaction."""
        try:
            resp = await self.http.get(f"{self.mempool_api}/tx/{txid}/status")
            data = resp.json()
            return data.get("confirmed", False) and 1 or 0
        except Exception:
            return 0

    async def wait_for_deposit(
        self,
        expected_amount_sats: int,
        tolerance_pct: float = 2.0,
        timeout_s: int = 1800,
        poll_interval_s: int = 15,
    ) -> dict | None:
        """Poll until a NEW deposit matching expected_amount arrives with ≥1 confirmation.
        
        Takes a snapshot of current UTXOs to only count newly arrived deposits.
        Returns tx dict or None on timeout.
        """
        deadline = asyncio.get_event_loop().time() + timeout_s
        # Snapshot existing UTXOs to only detect NEW ones
        existing = await self.get_utxos()
        seen_txids: set[str] = {u.get("txid", "") for u in existing}
        logger.debug(f"Deposit wait snapshot: {len(seen_txids)} existing UTXOs")

        while asyncio.get_event_loop().time() < deadline:
            utxos = await self.get_utxos()
            for utxo in utxos:
                txid = utxo.get("txid", "")
                if txid in seen_txids:
                    continue
                # Only consider confirmed UTXOs
                status = utxo.get("status", {})
                if not status.get("confirmed"):
                    seen_txids.add(txid)  # track but don't match
                    continue
                value = utxo.get("value", 0)
                min_expected = expected_amount_sats * (1 - tolerance_pct / 100)
                max_expected = expected_amount_sats * (1 + tolerance_pct / 100)
                if min_expected <= value <= max_expected:
                    seen_txids.add(txid)
                    logger.info(
                        f"Deposit detected: {value} sats in {txid}"
                    )
                    return {"txid": txid, "value": value}
                seen_txids.add(txid)
            await asyncio.sleep(poll_interval_s)

        logger.warning(f"Deposit timeout for {expected_amount_sats} sats")
        return None

    async def send_btc(self, to_address: str, amount_sats: int) -> str | None:
        """Send BTC from this wallet. Returns txid or None on failure."""
        try:
            # Build transaction using bitcoinlib
            utxos = await self.get_utxos()
            if not utxos:
                logger.error("No UTXOs available")
                return None

            # Select UTXOs
            selected = []
            total = 0
            for utxo in sorted(utxos, key=lambda u: u.get("value", 0), reverse=True):
                selected.append(utxo)
                total += utxo.get("value", 0)
                if total >= amount_sats + 1000:  # rough fee estimate
                    break

            if total < amount_sats:
                logger.error(f"Insufficient funds: {total} < {amount_sats}")
                return None

            # Build raw transaction via bitcoinlib
            t = Transaction(network="bitcoin", fee=500)
            for utxo in selected:
                t.add_input(
                    prev_txid=utxo["txid"],
                    output_n=utxo["vout"],
                    value=utxo.get("value", 0),
                    keys=[self.key],
                    address=self.address,
                )
            t.add_output(amount_sats, to_address)
            # Add change output if needed
            selected_total = sum(u.get("value", 0) for u in selected)
            fee_estimate = 500
            change = selected_total - amount_sats - fee_estimate
            if change > 200:
                t.add_output(change, self.address)

            # Sign
            t.sign()

            # Broadcast via mempool.space
            raw_hex = t.raw_hex()
            resp = await self.http.post(
                f"{self.mempool_api}/tx",
                data=raw_hex,
                headers={"Content-Type": "text/plain"},
            )
            if resp.status_code == 200:
                txid = resp.text.strip()
                logger.info(f"BTC sent: {amount_sats} sats → {to_address}  txid={txid}")
                return txid
            else:
                logger.error(f"Broadcast failed: {resp.status_code} {resp.text[:200]}")
                return None

        except Exception as e:
            logger.error(f"send_btc error: {e}", exc_info=True)
            return None


# Singleton
_wallet: BtcWallet | None = None


def get_btc_wallet() -> BtcWallet | None:
    return _wallet


def init_btc_wallet(wif: str) -> BtcWallet:
    global _wallet
    _wallet = BtcWallet(wif)
    return _wallet
