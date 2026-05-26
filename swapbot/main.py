"""
FastAPI application with webhook receiver for WhatsApp messages.
Receives webhooks from OpenWA, dispatches to bot logic, calls OpenWA API to reply.

v2: ChangeNOW-first universal swap bot with i18n.
"""

import hashlib
import hmac
import logging
import os
import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, status

from swapbot.openwa.client import OpenWAClient
from swapbot.db.connection import Database
from swapbot.db.queries import init_db
from swapbot.bot.router import MessageRouter
from swapbot.changenow.client import ChangeNowClient, init_cn_client, get_cn_client
from swapbot.engine.swap import SwapOrchestrator
from swapbot.i18n import load_translations
from swapbot.jobs.cleanup import start_cleanup_scheduler, stop_cleanup_scheduler

load_dotenv()

# --- Logging ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("swapbot")

# --- Config ---
OPENWA_API_URL = os.getenv("OPENWA_API_URL", "http://localhost:2785/api")
OPENWA_API_KEY = os.getenv("OPENWA_API_KEY", "")
OPENWA_SESSION_ID = os.getenv("OPENWA_SESSION_ID", "sess_default")
CHANGENOW_API_KEY = os.getenv("CHANGENOW_API_KEY", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-me")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "http://localhost:2889/webhook")
ADMIN_PHONE = os.getenv("ADMIN_PHONE", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/swapbot.db")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "2889"))


def hash_phone(phone: str) -> str:
    """SHA-256 hash a phone number for storage/display."""
    return hashlib.sha256(phone.encode()).hexdigest()[:16]


# --- Global state ---
openwa_client: OpenWAClient | None = None
cn_client: ChangeNowClient | None = None
db: Database | None = None
msg_router: MessageRouter | None = None
swap_orchestrator: SwapOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    global openwa_client, cn_client, db, msg_router, swap_orchestrator

    # Startup
    logger.info("🚀 Starting CryptoSwapBot v2 (ChangeNOW)...")

    # Load i18n translations
    load_translations()

    # Database
    db_path = os.path.expandvars(DATABASE_PATH)
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = Database(db_path)
    await db.connect()
    await init_db(db)

    # OpenWA
    openwa_client = OpenWAClient(OPENWA_API_URL, OPENWA_API_KEY, OPENWA_SESSION_ID)

    # ChangeNOW
    if not CHANGENOW_API_KEY:
        logger.error("CHANGENOW_API_KEY is required! Set it in .env")
        raise RuntimeError("CHANGENOW_API_KEY not configured")
    
    cn_client = init_cn_client(CHANGENOW_API_KEY)

    # Fetch available currencies at startup
    try:
        currencies = await cn_client.fetch_currencies()
        logger.info(f"Loaded {len(currencies)} currencies from ChangeNOW")
    except Exception as e:
        logger.warning(f"Could not fetch currencies at startup: {e}")

    # Swap orchestrator
    swap_orchestrator = SwapOrchestrator(cn_client, db)

    # Message router
    msg_router = MessageRouter(
        db=db,
        openwa_client=openwa_client,
        cn_client=cn_client,
        swap_orchestrator=swap_orchestrator,
        admin_phone=hash_phone(ADMIN_PHONE) if ADMIN_PHONE else "",
    )

    # Register webhook with OpenWA
    try:
        await openwa_client.register_webhook(WEBHOOK_URL, WEBHOOK_SECRET)
        logger.info(f"Webhook registered: {WEBHOOK_URL}")
    except Exception as e:
        logger.warning(f"Could not register webhook: {e}")

    # Scheduled jobs
    start_cleanup_scheduler(db)

    # Periodic currency cache refresh (every 6 hours)
    async def refresh_currencies():
        while True:
            await asyncio.sleep(6 * 3600)
            try:
                if cn_client:
                    await cn_client.fetch_currencies(force=True)
            except Exception as e:
                logger.error(f"Currency refresh error: {e}")

    asyncio.create_task(refresh_currencies())

    logger.info("✅ CryptoSwapBot v2 ready")

    yield

    # Shutdown
    logger.info("🛑 Shutting down...")
    stop_cleanup_scheduler()
    if cn_client:
        await cn_client.close()
    if db:
        await db.close()


app = FastAPI(title="CryptoSwapBot v2", version="2.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "cryptoswapbot-v2"}


@app.post("/webhook")
async def webhook(request: Request):
    """Receive webhooks from OpenWA with HMAC-SHA256 verification."""
    body = await request.body()
    signature = request.headers.get("x-openwa-signature", "")

    # Verify HMAC signature
    expected_sig = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_sig):
        logger.warning("Invalid webhook signature")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    payload = await request.json()
    event = payload.get("event", "")
    data = payload.get("data", {})

    logger.debug(f"Webhook: {event}")

    # Handle message.received events
    if event == "message.received" and msg_router:
        asyncio.create_task(msg_router.handle_message(payload))

    return {"status": "received"}


async def main():
    """Main entry point for running the bot."""
    import uvicorn

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=WEBHOOK_PORT,
        log_level=LOG_LEVEL.lower(),
        access_log=False,
    )
    server = uvicorn.Server(config)
    await server.serve()
