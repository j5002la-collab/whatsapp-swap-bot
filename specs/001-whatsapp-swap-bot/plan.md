# Implementation Plan: WhatsApp Swap Bot

**Branch**: `001-whatsapp-swap-bot` | **Date**: 2026-05-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-whatsapp-swap-bot/spec.md`

## Summary

Python async bot that connects to WhatsApp via OpenWA gateway for non-custodial BTC/Lightning/USDT swaps using Boltz API v2. Same swap logic as telegram-swap-bot, adapted for WhatsApp's interaction model (numbered menus, state machine). Single Docker container alongside OpenWA. SQLite storage.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: FastAPI (webhook receiver), httpx (async HTTP for OpenWA + Boltz), aiosqlite (SQLite async), python-dotenv (config)

**Storage**: SQLite (aiosqlite) — single file, zero external deps

**Testing**: pytest + pytest-asyncio + httpx-mock

**Target Platform**: Linux server (Docker container)

**Project Type**: bot (long-running async process with HTTP webhook receiver)

**Performance Goals**: <2s message response, handle 10 concurrent swap flows

**Constraints**: No external DB, single process, OpenWA gateway must be running

**Scale/Scope**: <10k users, <1k swaps/day, single WhatsApp session

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Gateway-First | ✅ PASS | All WhatsApp I/O via OpenWA REST + webhooks |
| II. No-Custodial | ✅ PASS | Boltz API handles all fund movements; bot never touches keys |
| III. WhatsApp-Native | ✅ PASS | Numbered menus, <5 messages per swap, emoji indicators |
| IV. Self-Contained | ✅ PASS | SQLite, single container, only OpenWA + Boltz as external deps |
| V. Transparent | ✅ PASS | Commission displayed before confirm, full audit logging |

## Project Structure

```text
swapbot/
├── __init__.py
├── __main__.py          # Entry point: python -m swapbot
├── main.py              # FastAPI app + startup/shutdown
│
├── openwa/              # OpenWA Gateway client
│   ├── __init__.py
│   ├── client.py        # REST client for OpenWA API (send text, session mgmt)
│   └── webhook.py       # Webhook receiver + HMAC verification
│
├── boltz/               # Boltz Exchange client (ported from TypeScript)
│   ├── __init__.py
│   ├── client.py        # HTTP client for Boltz API v2
│   ├── types.py         # Data types (SubmarineSwapRequest, etc.)
│   └── websocket.py     # WebSocket monitor for swap status
│
├── engine/              # Business logic
│   ├── __init__.py
│   ├── rates.py         # Rate engine (Boltz pairs + commission calc)
│   ├── swap.py          # Swap orchestrator (create + monitor + notify)
│   ├── commission.py    # Commission calculator
│   └── raffle.py        # Raffle engine (pool + draw)
│
├── bot/                 # WhatsApp bot logic
│   ├── __init__.py
│   ├── state.py         # User state machine
│   ├── router.py        # Message router (text → command dispatcher)
│   ├── handlers.py      # Command handlers (swap, rates, calc, help, admin)
│   └── messages.py      # Message templates + formatting
│
├── db/                  # Database layer
│   ├── __init__.py
│   ├── connection.py    # aiosqlite connection manager
│   └── queries.py       # SQL queries (users, swaps, raffles, config)
│
├── jobs/                # Scheduled tasks
│   ├── __init__.py
│   ├── cleanup.py       # Expire abandoned swap states
│   └── raffle_draw.py   # Weekly raffle draw (Sunday 00:00 UTC)
│
├── Dockerfile
├── docker-compose.yml   # swapbot + openwa services
├── requirements.txt
└── .env.example
```

## Implementation Phases

### Phase 1: Foundation (Core Infrastructure)
1. Project scaffold: `pyproject.toml`, `requirements.txt`, `__main__.py`
2. Database: SQLite schema (users, swaps, raffles, config), connection manager
3. Config: environment variables (.env), OpenWA URL + API key + session ID
4. Logging: structured JSON logging

### Phase 2: OpenWA Integration
1. OpenWA REST client (send text, session health check)
2. Webhook receiver (FastAPI endpoint, HMAC verification)
3. Session management (auto-register webhook on startup, health polling)

### Phase 3: Boltz Integration (Ported from TypeScript)
1. Boltz HTTP client (submarine/reverse/chain pairs + swap creation)
2. Boltz WebSocket client (swap status monitoring)
3. Rate engine (pairs → rate + commission calculation)
4. Swap orchestrator (create swap → wait for WS event → notify user)

### Phase 4: Bot Logic & State Machine
1. User state machine (idle → selecting → entering → confirming → awaiting → done)
2. Message router (text → command dispatch based on state)
3. Command handlers: swap flow, rates, calc, help/cancel
4. Message templates (numbered menus, confirmation messages, error messages)

### Phase 5: Admin + Raffle
1. Admin command handler (stats, commission set, broadcast)
2. Raffle engine (pool tracking per week, draw logic)
3. Scheduled jobs (state cleanup every 5 min, raffle draw Sunday 00:00)

### Phase 6: Deployment
1. Dockerfile (Python slim, single stage)
2. docker-compose.yml (swapbot + openwa, internal network, webhook forwarding)
3. Startup script (wait for OpenWA, register webhook, enter main loop)
4. README with setup instructions

## Key Design Decisions

### Webhook Receiving (NOT Sending)

The bot RECEIVES webhooks FROM OpenWA, not the other way around:
```
WhatsApp → OpenWA → HTTP POST to swapbot:2889/webhook → Bot processes → Bot calls OpenWA API to reply
```

### Phone Number Hashing
- All phone numbers SHA-256 hashed before storage
- `user_hash = sha256("628123456789@c.us")[:16]` for display
- Original phone only in-memory during active swap flow

### Swap Flow Limits
- User can have at most 1 active swap at a time
- Swap state expires after 30 minutes
- 3 swap attempts per user per hour (rate limit)
- Min/max amounts enforced per Boltz pair limits
