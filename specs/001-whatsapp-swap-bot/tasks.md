# Tasks: WhatsApp Swap Bot

**Input**: Design documents from `specs/001-whatsapp-swap-bot/`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅

**Tests**: Not explicitly requested — implementation-focused.

---

## Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Create project structure: `swapbot/` package with subdirs `openwa/`, `boltz/`, `engine/`, `bot/`, `db/`, `jobs/`
- [ ] T002 [P] Create `requirements.txt` with fastapi, uvicorn, httpx, aiosqlite, python-dotenv, websockets
- [ ] T003 [P] Create `.env.example` with OPENWA_API_URL, OPENWA_API_KEY, OPENWA_SESSION_ID, BOLTZ_API_URL, WEBHOOK_SECRET, ADMIN_PHONE, COMMISSION_RATE
- [ ] T004 [P] Create `swapbot/__init__.py`, `swapbot/__main__.py` entry point
- [ ] T005 [P] Create `swapbot/main.py` — FastAPI app with lifespan, webhook endpoint, structured JSON logging setup

---

## Phase 2: Database & Config (Foundational)

- [ ] T006 Create `swapbot/db/connection.py` — aiosqlite connection manager (get_db context manager)
- [ ] T007 Create `swapbot/db/queries.py` — SQL schema (users, swaps, raffle_entries, config) + CRUD functions
- [ ] T008 [P] Create config loader — read .env + validate required vars

---

## Phase 3: OpenWA Integration

- [ ] T009 Create `swapbot/openwa/client.py` — httpx async client: send_text(session_id, chat_id, text), session_status()
- [ ] T010 Create `swapbot/openwa/webhook.py` — FastAPI route POST /webhook with HMAC-SHA256 verification, parse message.received events
- [ ] T011 Wire webhook route into main.py, register webhook with OpenWA on startup (POST /sessions/{id}/webhooks)

---

## Phase 4: Boltz Integration (Ported from telegram-swap-bot)

- [ ] T012 Create `swapbot/boltz/types.py` — dataclasses: SubmarinePair, ReversePair, ChainPair, SwapRequest, SwapResponse
- [ ] T013 Create `swapbot/boltz/client.py` — httpx async client: get_submarine_pairs(), get_reverse_pairs(), create_submarine_swap(), create_reverse_swap(), get_swap_status()
- [ ] T014 Create `swapbot/boltz/websocket.py` — asyncio WebSocket client for `wss://api.boltz.exchange/v2/ws`, listen for swap.update events

---

## Phase 5: Swap Engine

- [ ] T015 Create `swapbot/engine/rates.py` — RateEngine: fetch pairs from Boltz, cache 30s, calculate user rate with commission, formatRateDisplay()
- [ ] T016 Create `swapbot/engine/commission.py` — CommissionEngine: calculate amounts (source, commission, boltz_fee, net, receive)
- [ ] T017 Create `swapbot/engine/swap.py` — SwapOrchestrator: create_swap() → Boltz API, monitor via WebSocket, handle success/failure

---

## Phase 6: Bot Logic & State Machine

- [ ] T018 Create `swapbot/bot/state.py` — UserState dataclass + StateMachine (idle→selecting_direction→entering_amount→confirming→awaiting_payment→complete), 30min expiry
- [ ] T019 Create `swapbot/bot/messages.py` — Message templates: welcome, direction_menu, amount_prompt, confirmation(rate+commission+amount), swap_success, swap_failed, rates_display, help, cancel
- [ ] T020 Create `swapbot/bot/router.py` — MessageRouter: parse webhook payload → extract phone+body → look up user state → dispatch to handler
- [ ] T021 Create `swapbot/bot/handlers.py` — Command handlers: handle_swap_flow (full state machine), handle_rates, handle_calc, handle_help, handle_cancel, handle_unknown

---

## Phase 7: Admin & Raffle

- [ ] T022 Create admin handler in `swapbot/bot/handlers.py` — admin stats (total swaps, volume, commission, users), admin set_commission, admin broadcast
- [ ] T023 Create `swapbot/engine/raffle.py` — RaffleEngine: add_entry(user_hash, swap_amount), weekly_pool, draw_winner()
- [ ] T024 Create `swapbot/jobs/cleanup.py` — expire abandoned swap states every 5 minutes
- [ ] T025 Create `swapbot/jobs/raffle_draw.py` — weekly draw job (Sunday 00:00 UTC), notify winner via OpenWA

---

## Phase 8: Deployment

- [ ] T026 Create `Dockerfile` — Python 3.12-slim, install deps, copy swapbot/, CMD python -m swapbot
- [ ] T027 Create `docker-compose.yml` — swapbot (port 2889 internal) + openwa (ports 2785/2886), shared network, webhook forwarding
- [ ] T028 [P] Create `README.md` — setup instructions, architecture diagram, WhatsApp setup guide
- [ ] T029 [P] Create `.gitignore` — Python patterns, .env, *.db

---

## Dependencies & Execution Order

### Phase Dependencies
- **Setup (Phase 1)**: No dependencies
- **Database (Phase 2)**: Depends on Setup
- **OpenWA (Phase 3)**: Depends on Setup
- **Boltz (Phase 4)**: Depends on Setup
- **Engine (Phase 5)**: Depends on Boltz (Phase 4)
- **Bot (Phase 6)**: Depends on OpenWA (Phase 3) + Engine (Phase 5) + DB (Phase 2)
- **Admin/Raffle (Phase 7)**: Depends on Bot (Phase 6)
- **Deploy (Phase 8)**: Depends on all

### Parallel Opportunities
- Phase 2 + Phase 3 + Phase 4 can run in parallel (different modules)

---

## Implementation Strategy

### MVP (Phases 1-6)
BTC→Lightning swap flow working end-to-end via WhatsApp.
