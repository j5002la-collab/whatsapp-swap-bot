"""Scheduled jobs for WhatsApp Swap Bot."""

import asyncio
import logging

logger = logging.getLogger("jobs")


async def cleanup_expired_states(db, interval_seconds: int = 300):
    """Clean up abandoned swap states every N seconds."""
    while True:
        try:
            await db.execute(
                """UPDATE users SET state = NULL, state_expires_at = NULL
                   WHERE state_expires_at IS NOT NULL
                   AND state_expires_at < datetime('now')"""
            )
            await db.commit()
            logger.debug("State cleanup completed")
        except Exception as e:
            logger.error(f"State cleanup error: {e}")
        await asyncio.sleep(interval_seconds)


async def raffle_draw_scheduler(db, raffle_engine, openwa_client, admin_chat_id: str = ""):
    """Run raffle draw every Sunday 00:00 UTC."""
    import time as _time
    from datetime import datetime, timezone

    while True:
        now = datetime.now(timezone.utc)
        # Calculate seconds until next Sunday 00:00 UTC
        days_until_sunday = (6 - now.weekday()) % 7
        if days_until_sunday == 0 and now.hour == 0 and now.minute < 5:
            # It's Sunday 00:00-00:05 — draw now
            try:
                result = await raffle_engine.draw_winner(db)
                if result and admin_chat_id:
                    await openwa_client.send_text(
                        admin_chat_id,
                        f"🎁 *Sorteo semanal completado*\n\n"
                        f"Semana: {result['week']}\n"
                        f"Ganador: {result['winner_hash'][:8]}...\n"
                        f"Premio: {result['prize']:,} sats\n"
                        f"Participantes: {result['participants']}"
                    )
                    # Notify winner
                    winner_chat = f"{result['winner_hash']}@c.us"
                    await openwa_client.send_text(
                        winner_chat,
                        f"🎉 *¡Ganaste el sorteo semanal!*\n\n"
                        f"Premio: {result['prize']:,} sats\n\n"
                        "Te contactaremos para el pago."
                    )
                logger.info("Raffle draw completed")
            except Exception as e:
                logger.error(f"Raffle draw error: {e}")
            # Sleep until next check (1 hour)
            await asyncio.sleep(3600)
        else:
            # Sleep until next hour
            seconds_until_next_hour = 3600 - now.minute * 60 - now.second
            await asyncio.sleep(min(seconds_until_next_hour, 3600))
