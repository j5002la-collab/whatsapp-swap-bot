"""
FastAPI application with webhook receiver for WhatsApp messages.
Receives webhooks from OpenWA, dispatches to bot logic, calls OpenWA API to reply.
"""

import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, status

from swapbot.openwa.client import OpenWAClient
from swapbot.openwa.webhook import create_webhook_router
from swapbot.db.connection import Database
from swapbot.db.queries import init_db
from swapbot.bot.router import MessageRouter
from swapbot.boltz.client import BoltzClient
from swapbot.boltz.websocket import BoltzWebSocket
from swapbot.engine.rates import RateEngine
from swapbot.engine.commission import CommissionEngine
from swapbot.engine.swap import SwapOrchestrator
from swapbot.engine.raffle import RaffleEngine
from swapbot.jobs.cleanup import start_cleanup_scheduler, stop_cleanup_scheduler
from swapbot.jobs.raffle_draw import start_raffle_scheduler, stop_raffle_scheduler

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
BOLTZ_API_URL = os.getenv("BOLTZ_API_URL", "https://api.boltz.exchange")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-me")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "http://swapbot:2889/webhook")
ADMIN_PHONE = os.getenv("ADMIN_PHONE", "")
COMMISSION_RATE = float(os.getenv("COMMISSION_RATE", "2.5"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/swapbot.db")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "2889"))


def hash_phone(phone: str) -> str:
    """SHA-256 hash a phone number for storage/display."""
    return hashlib.sha256(phone.encode()).hexdigest()[:16]


# --- Global state ---
openwa_client: OpenWAClient | None = None
boltz_client: BoltzClient | None = None
boltz_ws: BoltzWebSocket | None = None
db: Database | None = None
msg_router: MessageRouter | None = None
rate_engine: RateEngine | None = None
commission_engine: CommissionEngine | None = None
swap_orchestrator: SwapOrchestrator | None = None
raffle_engine: RaffleEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    global openwa_client, boltz_client, boltz_ws, db, msg_router
    global rate_engine, commission_engine, swap_orchestrator, raffle_engine

    # Startup
    logger.info("🚀 Starting WhatsApp SwapBot...")

    db_path = os.path.expandvars(DATABASE_PATH)
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = Database(db_path)
    await db.connect()
    await init_db(db)

    openwa_client = OpenWAClient(OPENWA_API_URL, OPENWA_API_KEY, OPENWA_SESSION_ID)
    boltz_client = BoltzClient(BOLTZ_API_URL)

    rate_engine = RateEngine(boltz_client)
    commission_engine = CommissionEngine(COMMISSION_RATE)
    raffle_engine = RaffleEngine(db)
    swap_orchestrator = SwapOrchestrator(boltz_client, db, commission_engine, raffle_engine)

    msg_router = MessageRouter(
        db=db,
        openwa_client=openwa_client,
        boltz_client=boltz_client,
        rate_engine=rate_engine,
        commission_engine=commission_engine,
        swap_orchestrator=swap_orchestrator,
        raffle_engine=raffle_engine,
        admin_phone=hash_phone(ADMIN_PHONE),
    )

    # Register webhook with OpenWA on startup
    try:
        await openwa_client.register_webhook(WEBHOOK_URL, WEBHOOK_SECRET)
        logger.info(f"Webhook registered: {WEBHOOK_URL}")
    except Exception as e:
        logger.warning(f"Could not register webhook: {e}")

    # Connect Boltz WebSocket
    boltz_ws = BoltzWebSocket(BOLTZ_API_URL)
    swap_orchestrator.set_ws(boltz_ws)
    asyncio.create_task(boltz_ws.connect())

    # Initialize ChangeNOW client for stablecoin swaps
    changenow_key = os.getenv("CHANGENOW_API_KEY", "")
    if changenow_key:
        from swapbot.changenow.client import init_cn_client
        init_cn_client(changenow_key)
        logger.info("ChangeNOW client initialized for USDT/USDC swaps")
    else:
        logger.warning("CHANGENOW_API_KEY not set — stablecoin swaps disabled")

    # Start scheduled jobs
    start_cleanup_scheduler(db)
    start_raffle_scheduler(raffle_engine, openwa_client)

    logger.info("✅ SwapBot ready")

    yield

    # Shutdown
    logger.info("🛑 Shutting down...")
    stop_cleanup_scheduler()
    stop_raffle_scheduler()
    if boltz_ws:
        await boltz_ws.disconnect()
    if db:
        await db.close()


app = FastAPI(title="WhatsApp SwapBot", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "whatsapp-swapbot"}


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
