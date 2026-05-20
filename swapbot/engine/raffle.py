"""Raffle engine: pool tracking and weekly draws.
Ported from telegram-swap-bot/src/engine/raffle.ts
"""

import asyncio
import logging
import random
from datetime import datetime, timezone

from swapbot.db.connection import Database
from swapbot.db.queries import get_swap_stats

logger = logging.getLogger("engine.raffle")


class RaffleEngine:
    """Manages weekly raffle pool and draws."""

    RAFFLE_PERCENT = 0.001  # 0.1% of swap volume

    async def get_status(self, db: Database) -> dict:
        """Get current raffle status."""
        now = datetime.now(timezone.utc)
        week = now.isocalendar()[1]

        stats = await get_swap_stats(db)

        # Get participants count (users who swapped this week)
        row = await db.fetch_one(
            """SELECT COUNT(DISTINCT phone_hash) as count FROM swaps
               WHERE status = 'completed' AND strftime('%W', created_at) = strftime('%W', 'now')"""
        )
        participants = row["count"] if row else 0

        # Check if drawn
        raffle_row = await db.fetch_one(
            "SELECT * FROM raffle_entries WHERE week_number = ? AND paid = 1 ORDER BY drawn_at DESC LIMIT 1",
            (week,)
        )
        if raffle_row:
            wh = raffle_row.get("winner_hash")
            return {
                "week": week,
                "pool": stats.get("raffle_pool", 0),
                "participants": participants,
                "paid": True,
                "winner": wh[:8] if wh else None,
            }

        return {
            "week": week,
            "pool": stats.get("raffle_pool", 0),
            "participants": participants,
            "paid": False,
            "winner": None,
        }

    async def draw_winner(self, db: Database) -> dict | None:
        """Draw a random winner from this week's swap participants. Run on Sunday."""
        now = datetime.now(timezone.utc)
        week = now.isocalendar()[1]

        # Check if already drawn this week (any row with paid=1 for this week_number)
        existing = await db.fetch_one(
            "SELECT * FROM raffle_entries WHERE week_number = ? AND paid = 1",
            (week,)
        )
        if existing:
            logger.info(f"Raffle already drawn for week {week}")
            return None

        # Get eligible users (made swaps this week)
        rows = await db.fetch_all(
            """SELECT phone_hash, SUM(raffle_contribution) as total_contributed
               FROM swaps WHERE status = 'completed'
               AND strftime('%W', created_at) = strftime('%W', 'now')
               GROUP BY phone_hash"""
        )

        if not rows:
            logger.info("No eligible users for raffle this week")
            return None

        # Pool is SUM of all raffle contributions
        pool = sum(r["total_contributed"] for r in rows)

        # Weighted random: each user's chance = their contribution / total pool
        total = pool
        rand = random.uniform(0, total)
        cumulative = 0

        winner_hash = None
        for r in rows:
            cumulative += r["total_contributed"]
            if rand <= cumulative:
                winner_hash = r["phone_hash"]
                break

        if not winner_hash:
            winner_hash = rows[-1]["phone_hash"]

        # Record draw winner row
        await db.execute(
            """INSERT OR REPLACE INTO raffle_entries
               (week_number, phone_hash, tickets, volume_contributed,
                winner_hash, prize_amount, drawn_at, paid)
               VALUES (?, ?, 1, 0, ?, ?, ?, 1)""",
            (week, winner_hash, winner_hash, pool, now.isoformat()),
        )
        await db.commit()

        logger.info(f"🎁 Raffle drawn! Week {week}, winner {winner_hash[:8]}, prize {pool} sats")
        return {"week": week, "winner_hash": winner_hash, "prize": pool, "participants": len(rows)}

    async def add_contribution(self, db: Database, phone_hash: str, swap_amount: int):
        """Add raffle contribution when a swap completes."""
        contribution = int(swap_amount * self.RAFFLE_PERCENT)
        if contribution <= 0:
            return

        # Update swap record with raffle contribution
        # Actually, the contribution is tracked via the swap itself
        # The raffle draw queries swaps directly
        logger.debug(f"Raffle contribution: {contribution} sats from {phone_hash[:8]}")


# Singleton
_raffle_instance = None


def get_raffle_engine() -> RaffleEngine:
    global _raffle_instance
    if _raffle_instance is None:
        _raffle_instance = RaffleEngine()
    return _raffle_instance
