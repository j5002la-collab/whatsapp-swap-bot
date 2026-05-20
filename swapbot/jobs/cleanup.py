"""State cleanup scheduler — expires abandoned swap states every 5 minutes."""

import asyncio
import logging

logger = logging.getLogger("jobs.cleanup")

_cleanup_task: asyncio.Task | None = None


async def _cleanup_loop(db, interval_seconds: int = 300):
    """Clean up abandoned swap states every N seconds."""
    from swapbot.db.queries import expire_abandoned_states
    while True:
        try:
            expired = await expire_abandoned_states(db)
            if expired:
                logger.info(f"State cleanup: {expired} expired states cleared")
        except Exception as e:
            logger.error(f"State cleanup error: {e}")
        await asyncio.sleep(interval_seconds)


def start_cleanup_scheduler(db):
    global _cleanup_task
    _cleanup_task = asyncio.create_task(_cleanup_loop(db))
    logger.info("Cleanup scheduler started (every 5 min)")


def stop_cleanup_scheduler():
    global _cleanup_task
    if _cleanup_task:
        _cleanup_task.cancel()
        _cleanup_task = None
        logger.info("Cleanup scheduler stopped")
