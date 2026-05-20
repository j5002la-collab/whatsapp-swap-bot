"""Weekly raffle draw scheduler — runs every Sunday 00:00 UTC."""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("jobs.raffle_draw")

_raffle_task: asyncio.Task | None = None


async def _raffle_loop(raffle_engine, db):
    """Run raffle draw every Sunday 00:00 UTC."""
    while True:
        now = datetime.now(timezone.utc)
        days_until_sunday = (6 - now.weekday()) % 7
        if days_until_sunday == 0 and now.hour == 0 and now.minute < 5:
            try:
                result = await raffle_engine.draw_winner(db)
                if result:
                    logger.info(f"Raffle drawn: week {result['week']}, winner {result['winner_hash'][:8]}")
                logger.info("Raffle draw completed")
            except Exception as e:
                logger.error(f"Raffle draw error: {e}")
            await asyncio.sleep(3600)
        else:
            seconds_until_next_hour = 3600 - now.minute * 60 - now.second
            await asyncio.sleep(min(seconds_until_next_hour, 3600))


def start_raffle_scheduler(raffle_engine, db):
    global _raffle_task
    _raffle_task = asyncio.create_task(_raffle_loop(raffle_engine, db))
    logger.info("Raffle scheduler started (draws Sunday 00:00 UTC)")


def stop_raffle_scheduler():
    global _raffle_task
    if _raffle_task:
        _raffle_task.cancel()
        _raffle_task = None
        logger.info("Raffle scheduler stopped")
