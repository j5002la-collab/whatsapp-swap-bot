# WhatsApp Swap Bot Constitution

<!--
Sync Impact Report
==================
Version: 1.0.0 (initial ratification)
Modified: all (initial fill)
Templates requiring updates: ✅ all generated fresh
Follow-up TODOs: none
-->

## Core Principles

### I. Gateway-First

The bot MUST communicate with WhatsApp exclusively through OpenWA's REST API.
No embedded browser automation, no direct Puppeteer/Playwright usage. OpenWA handles
all WhatsApp protocol complexity (QR authentication, session management, message
delivery). The bot is a pure business-logic consumer.

**Rationale**: Separation of concerns. OpenWA is the battle-tested gateway. The bot
focuses on swap logic, user flows, and Boltz API integration.

### II. No-Custodial by Design

The bot MUST NEVER hold user funds at any point. All swaps execute atomically
through the Boltz Exchange API. Private keys, seed phrases, or wallet credentials
MUST NEVER be requested, stored, or transmitted. The only data persisted is swap
state for status tracking.

**Rationale**: Same principle as telegram-swap-bot. Non-custodial eliminates
regulatory risk and security liability. If the bot is compromised, no funds are lost.

### III. WhatsApp-Native UX

Every interaction MUST feel natural on WhatsApp — no long menus, no markdown
heavy messages. Use WhatsApp's native features: quick reply buttons where possible,
concise messages, emoji indicators. The swap flow MUST complete in 5 messages or
fewer from start to confirmation.

**Rationale**: WhatsApp users expect fast, terse interactions — not chatbot menus.
The telegram-swap-bot flow must be adapted for WhatsApp's interaction model.

### IV. Self-Contained Deployment

The bot MUST run as a single Docker container alongside OpenWA. SQLite for state
(no external MongoDB dependency), environment-variable configuration, structured
JSON logging. No external service dependencies except OpenWA and Boltz API.

**Rationale**: Umbrel deployment simplicity. One `docker-compose.yml` with two
services: OpenWA (gateway) + Swap Bot (business logic).

### V. Transparent & Auditable

Every swap MUST be logged with: user phone hash, direction, amount, Boltz swap ID,
fee charged, and final status. Commission MUST be clearly stated before confirmation
(2.5% default, configurable). Failed swaps MUST trigger automatic refund instructions
via WhatsApp.

**Rationale**: Trust through transparency. Users must know the exact fee. Operators
must have an audit trail for reconciliation.

## Security Requirements

- OpenWA API key stored as environment variable only, never in code
- User phone numbers hashed (SHA-256) in logs, never stored in plaintext
- Boltz API communication over HTTPS only
- Swap amounts validated server-side before submission
- Rate limiting: max 3 swap attempts per user per hour
- No admin panel exposed to internet — admin via WhatsApp messages to a
  configurable admin phone number

## Technology Constraints

- **Language**: Python 3.11+ (matching user preference for simplicity)
- **Framework**: Simple async Python with httpx for REST calls, no heavy web framework needed
- **Database**: SQLite (aiosqlite for async) — single file, zero config
- **WhatsApp**: OpenWA REST API (HTTP calls only, no WebSocket required)
- **Swaps**: Boltz API v2 (REST endpoints)
- **Deployment**: Single Docker container, config via environment variables

## Governance

This constitution supersedes all other project documentation. Deviations require
documented justification. Amendments follow Semantic Versioning. All complexity
beyond these principles must be justified in the implementation plan.

**Version**: 1.0.0 | **Ratified**: 2026-05-19 | **Last Amended**: 2026-05-19
