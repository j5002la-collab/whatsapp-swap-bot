# Research: WhatsApp Swap Bot

**Feature**: WhatsApp Swap Bot  
**Date**: 2026-05-19  
**Status**: Complete

## Decision Log

### 1. WhatsApp Integration: OpenWA Webhooks + REST

**Decision**: Use OpenWA webhook system for inbound messages. Bot runs an internal HTTP server (FastAPI on port 2889) to receive webhooks from OpenWA. Outbound messages via OpenWA REST API (POST `/sessions/{id}/messages/send-text`).

**Rationale**:
- OpenWA webhooks deliver `message.received` events in real-time with HMAC signature verification.
- No polling needed — bot receives messages within 1-2 seconds of WhatsApp delivery.
- Webhook payload includes: message body, sender phone, timestamp, contact name.
- Bot's internal FastAPI server handles webhook verification + processes messages + calls OpenWA API to reply.

**Alternatives considered**:
- **Polling OpenWA for messages**: Higher latency, wasteful API calls. Rejected.
- **Direct whatsapp-web.js in Python**: Fragile, poor multi-session support. Rejected for Gateway-First principle.
- **WhatsApp Cloud API**: Requires Business verification, not available for personal numbers. Rejected.

### 2. Swap Engine: Boltz API v2 (Direct Translation)

**Decision**: Port the Boltz client from TypeScript to Python using `httpx` async. Keep the same flow: submarine pairs → rate calculation with commission → create swap → monitor WebSocket.

**Rationale**:
- telegram-swap-bot already has a battle-tested Boltz integration. Direct translation minimizes logic errors.
- Boltz API v2 is well-documented, REST-based, with WebSocket for status updates.
- Python `httpx` is equivalent to TypeScript `axios` for async HTTP.
- Rate calculation formula identical: `userRate = boltzRate * (1 - (boltzFee% + commission%)/100)`.

**Key Boltz endpoints used**:
- `GET /v2/swap/submarine` — BTC→LN pairs and limits
- `GET /v2/swap/reverse` — LN→BTC pairs and limits
- `GET /v2/swap/chain` — On-chain pairs (USDT, USDC)
- `POST /v2/swap/submarine` — Create submarine swap
- `POST /v2/swap/reverse` — Create reverse swap
- `POST /v2/swap/chain` — Create chain swap
- `GET /v2/swap/{id}` — Check swap status
- WebSocket `wss://api.boltz.exchange/v2/ws` — Real-time swap updates

### 3. State Machine: Per-User Conversation Flow

**Decision**: Each user has a `state` field (JSON in SQLite) tracking their current position in the swap flow. State machine transitions: `idle → selecting_direction → entering_amount → confirming → awaiting_payment → complete`.

**Rationale**:
- WhatsApp has no concept of "sessions" or "callback data" like Telegram.
- Every message from a user must be interpreted in context of their current state.
- State expires after 30 minutes of inactivity (cleanup job).
- Simple key-value state machine, not a heavy framework.

**States**:
```
idle
  → selecting_direction (user sends "swap"/"cambiar")
    → entering_amount (user picks direction 1-4)
      → confirming (user enters valid amount)
        → awaiting_payment (user confirms "si")
          → complete (Boltz detects payment)
          → failed (Boltz reports failure)
```

### 4. Database: SQLite with aiosqlite

**Decision**: Single SQLite file with 4 tables (users, swaps, raffles, config). No ORM — raw SQL via aiosqlite for simplicity.

**Rationale**:
- Zero external dependencies (no MongoDB, no PostgreSQL).
- SQLite handles the expected scale (<10k users, <1k swaps/day) with ease.
- aiosqlite provides async access compatible with FastAPI/asyncio.
- Single file = trivial backup and portability.

### 5. Message Format: WhatsApp-Native

**Decision**: Numbered menus instead of inline keyboards. WhatsApp message character limit restricts complex formatting. Use emoji indicators for status.

**Rationale**:
- WhatsApp doesn't support inline keyboards in the same way Telegram does.
- Numbered replies (1, 2, 3) are universally understood.
- Messages kept under 1000 chars each to avoid truncation.
- Example flow:
  ```
  Bot: 🔄 *SwapBot WhatsApp*
  
  Selecciona dirección:
  1. BTC → Lightning ⚡
  2. Lightning ⚡ → BTC
  3. USDT → BTC
  4. BTC → USDT
  
  Responde con el número.
  ```

### 6. Deployment: Single Container + OpenWA

**Decision**: Bot runs as a second Docker container in the same `docker-compose.yml` as OpenWA. Internal port 2889 (FastAPI webhook receiver), not exposed externally.

**Rationale**:
- OpenWA forwards webhooks to bot's internal port within Docker network.
- User never accesses the bot directly — only via WhatsApp.
- Admin features accessed via WhatsApp messages to admin number.
- Single `docker-compose.yml` with two services: `openwa` + `swap-bot`.

### 7. Raffle: Per-Week Tracking

**Decision**: Same raffle model as telegram-swap-bot: 0.1% of swap volume added to weekly pool. Winner selected via random draw every Sunday 00:00 UTC. Winner notified via WhatsApp.

**Rationale**:
- Proven engagement mechanic from telegram-swap-bot.
- Simple SQL query: `SELECT user_hash FROM swaps WHERE week = current_week ORDER BY RANDOM() LIMIT 1`.
- Prize sent as manual WhatsApp notification (no automatic payout — informational only for v1).

### 8. Admin: WhatsApp-Based

**Decision**: Admin commands via WhatsApp from a configured admin phone number. No web dashboard needed for v1.

**Rationale**:
- Follows WhatsApp-Native principle — admin uses same interface as users.
- Admin commands: `admin stats`, `admin commission 3.5`, `admin broadcast <msg>`.
- Phone number validated via SHA-256 hash match against configured admin hash.
- Simple and secure — no exposed web port for admin.

## Telegram → WhatsApp Translation Guide

| Telegram Feature | WhatsApp Equivalent |
|-----------------|-------------------|
| Telegraf bot framework | FastAPI webhook receiver + httpx for OpenWA API |
| Inline keyboard buttons | Numbered reply menu (1, 2, 3, 4) |
| Callback queries | Next message in state machine |
| MongoDB (Mongoose) | SQLite (aiosqlite) |
| node-schedule (cron) | asyncio.create_task + loop.call_later |
| Winston (logging) | Python logging (structured JSON) |
| `/swap` command | "swap" or "cambiar" message |
| `/start` command | Any first message triggers welcome |
| User middleware | State machine lookup by phone hash |
