# Feature Specification: WhatsApp Swap Bot

**Feature Branch**: `001-whatsapp-swap-bot`

**Created**: 2026-05-19

**Status**: Draft

**Input**: User description: "Build a WhatsApp swap bot similar to telegram-swap-bot, using OpenWA as the WhatsApp gateway. Non-custodial USDT/USDC ↔ BTC/Lightning swaps via Boltz API. Same commission model (2.5%), weekly raffle, and admin features."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Swap Flow: BTC On-chain → Lightning (Priority: P1) 🎯 MVP

As a WhatsApp user, I want to send a message like "swap" or "cambiar", select BTC→Lightning direction, enter an amount, receive a Lightning invoice, pay it, and have the bot automatically swap my funds to an on-chain BTC address — all within WhatsApp.

**Why this priority**: The core value proposition. Without swap functionality, there is no bot. This is the minimum deliverable that generates revenue (commission).

**Independent Test**: Send "swap" to the bot's WhatsApp number → select direction → receive invoice → pay invoice → bot detects payment via Boltz WebSocket → bot sends "Swap completado" confirmation with tx details. Verifiable entirely within WhatsApp.

**Acceptance Scenarios**:

1. **Given** a user sends "swap" to the bot, **When** the bot responds with direction options (1. BTC→Lightning, 2. Lightning→BTC, 3. USDT→BTC, 4. BTC→USDT), **Then** the user can select by replying with a number.
2. **Given** the user selects BTC→Lightning, **When** the bot asks for the amount in sats, **Then** the user replies with an amount like "50000" and the bot validates it's within min/max limits.
3. **Given** a valid amount, **When** the bot shows the rate + commission breakdown and asks for confirmation, **Then** the user replies "si" or "confirmar" to proceed.
4. **Given** the user confirms, **When** the bot generates a Boltz submarine swap invoice and sends it to the user, **Then** the user pays the Lightning invoice from their wallet.
5. **Given** the invoice is paid, **When** Boltz detects the payment via WebSocket, **Then** the bot sends the user a confirmation with the on-chain tx ID and final amount received.

---

### User Story 2 - Swap Flow: Lightning → BTC On-chain (Priority: P1)

As a WhatsApp user, I want to swap from Lightning to on-chain BTC by providing my BTC address, receiving a Lightning invoice to pay, and having the bot execute a reverse swap.

**Why this priority**: The second half of the core swap pair. BTC↔Lightning covers the most common use case. Same priority as US1 because both directions are essential for a complete swap experience.

**Independent Test**: Send "swap" → select Lightning→BTC → provide BTC address → bot generates reverse swap → bot sends Lightning invoice → user pays → bot confirms on-chain tx.

**Acceptance Scenarios**:

1. **Given** the user selects Lightning→BTC direction, **When** the bot asks for a BTC address (on-chain), **Then** the user provides a valid BTC address.
2. **Given** a valid BTC address, **When** the bot asks for the amount in sats, **Then** the user enters the amount.
3. **Given** the swap is confirmed, **When** Boltz locks the funds, **Then** the bot sends the user the on-chain transaction ID with a block explorer link.

---

### User Story 3 - Stablecoin Swaps: USDT/USDC (Priority: P2)

As a WhatsApp user, I want to swap between USDT/USDC and BTC/Lightning using the same simple flow.

**Why this priority**: Stablecoin support differentiates the bot and captures a different user segment. USDT is the most traded stablecoin globally. Can be built after core BTC swaps are stable.

**Independent Test**: Send "swap" → select USDT→BTC → enter USDT amount → receive deposit address → send USDT → bot detects via Boltz → bot sends BTC confirmation.

**Acceptance Scenarios**:

1. **Given** the user selects USDT→BTC, **When** the bot asks for the USDT amount, **Then** the user enters an amount like "100" and the bot validates it.
2. **Given** the swap is confirmed, **When** the bot displays the deposit address and memo (if needed), **Then** the user sends USDT to the provided address.
3. **Given** Boltz detects the USDT deposit, **When** the swap completes, **Then** the bot sends the BTC tx ID confirmation.

---

### User Story 4 - Rates & Calculator (Priority: P2)

As a WhatsApp user, I want to check live swap rates and calculate exactly what I'd receive before committing to a swap.

**Why this priority**: Users want to know rates before swapping. Builds trust and reduces support questions. Can be implemented alongside swap flows.

**Independent Test**: Send "rates" or "tasas" to the bot → bot responds with current BTC→Lightning and Lightning→BTC rates, Boltz fees, min/max amounts, and the 2.5% commission.

**Acceptance Scenarios**:

1. **Given** a user sends "rates" or "tasas", **When** the bot fetches live rates from Boltz API, **Then** the bot responds with formatted rate information including commission percentage.
2. **Given** a user sends "calc 50000" or "calcular 50000", **When** the bot calculates the estimated receive amount, **Then** the response shows: "Enviando 50,000 sats → Recibes ~X.XXXXXXXX BTC (después de fees)".

---

### User Story 5 - Admin Panel via WhatsApp (Priority: P3)

As the bot operator, I want to check statistics (volume, swaps count, treasury balance), change commission rate, and broadcast messages to all users — all via WhatsApp messages to the admin number.

**Why this priority**: Admin features are essential for operations but don't block user-facing functionality. Can be built after the core swap flow is stable.

**Independent Test**: Send "admin" from the configured admin phone number → bot responds with admin menu → select "stats" → bot replies with daily/weekly volume, swap count, commission earned, raffle pool.

**Acceptance Scenarios**:

1. **Given** the admin sends "admin" from the configured admin number, **When** the bot validates the sender is the admin, **Then** the bot responds with an admin menu (stats, set commission, broadcast, treasury).
2. **Given** the admin selects "stats", **When** the bot queries the database, **Then** it returns: total swaps, volume (24h/7d/all), commission earned, raffle pool, active users.
3. **Given** the admin selects "set commission 3.5", **When** the bot updates the rate, **Then** it confirms the change and all subsequent swaps use the new rate.
4. **Given** the admin selects "broadcast" and sends a message, **When** the bot confirms, **Then** the message is sent to all users who have interacted with the bot.

---

### User Story 6 - Weekly Raffle (Priority: P3)

As a WhatsApp user, I want to be automatically entered into a weekly raffle when I complete swaps, with 0.1% of total volume distributed to winners.

**Why this priority**: Gamification that drives engagement and repeat usage. Not critical for launch but valuable for retention.

**Independent Test**: Complete a swap → verify the swap amount contributes to the raffle pool → wait for weekly draw → verify winners receive a WhatsApp notification.

**Acceptance Scenarios**:

1. **Given** a user completes a swap, **When** the swap is confirmed, **Then** the swap amount * 0.1% is added to the raffle pool and the user is entered into the draw.
2. **Given** the weekly draw time arrives (Sunday 00:00 UTC), **When** the raffle job runs, **Then** a random winner is selected from eligible users and notified via WhatsApp with the prize amount.

---

### Edge Cases

- What happens when a user sends an invalid amount (below minimum or above maximum)? Bot responds with the valid range and asks to try again.
- What happens when Boltz API is unreachable? Bot responds with "Servicio temporalmente no disponible. Intenta de nuevo en unos minutos." and logs the error.
- What happens when a user abandons a swap mid-flow? Swap state expires after 30 minutes of inactivity; user must start over.
- What happens when OpenWA session disconnects? Bot detects session status changes and logs errors. Incoming messages are queued. Admin is notified.
- What happens when a swap fails (e.g., Boltz refund)? Bot sends the user refund instructions with the Boltz refund ID and a link to claim.
- What happens with concurrent users? Each user has independent swap state tracked by phone number hash. No cross-user interference.
- What happens with duplicate invoice payments? Boltz API handles this; bot only processes the first WebSocket confirmation event.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Bot MUST connect to OpenWA API Gateway and listen for incoming WhatsApp messages on a configured session.
- **FR-002**: Bot MUST respond to trigger words: "swap"/"cambiar" (start swap), "rates"/"tasas" (show rates), "calc"/"calcular" (calculator), "help"/"ayuda" (help menu).
- **FR-003**: Swap flow MUST support 4 directions: BTC→Lightning, Lightning→BTC, USDT→BTC, BTC→USDT via Boltz API v2.
- **FR-004**: Bot MUST fetch live rates from Boltz API (`/v2/swap/submarine` and `/v2/swap/reverse`) before presenting swap options.
- **FR-005**: Bot MUST calculate and display commission (configurable, default 2.5%) and Boltz network fees before confirmation.
- **FR-006**: Bot MUST create Boltz swaps (submarine for BTC→LN, reverse for LN→BTC, chain for stablecoins) and return invoices/addresses to the user.
- **FR-007**: Bot MUST monitor swap status via Boltz WebSocket and notify the user on completion or failure.
- **FR-008**: Bot MUST persist swap records with: user phone hash, direction, input amount, output amount, Boltz swap ID, fee, commission, status, timestamp.
- **FR-009**: Admin MUST be able to query stats, change commission, and broadcast messages from the configured admin WhatsApp number.
- **FR-010**: Bot MUST add 0.1% of each swap volume to a raffle pool and enter the user into the current week's draw.
- **FR-011**: Weekly raffle draw MUST run automatically (Sunday 00:00 UTC) and notify the winner via WhatsApp.
- **FR-012**: Bot MUST hash user phone numbers (SHA-256) before storing in logs or database.
- **FR-013**: Bot MUST validate all amounts against Boltz min/max limits before creating swaps.
- **FR-014**: Bot MUST handle OpenWA session disconnections gracefully: log errors, notify admin, queue messages for retry.
- **FR-015**: Bot MUST rate-limit users to 3 swap attempts per hour to prevent abuse.

### Key Entities

- **User**: Phone hash (SHA-256), first interaction timestamp, total swaps count, total volume, current swap state (JSON), raffle entries count.
- **Swap**: Unique ID, user phone hash, direction (btc_ln/ln_btc/usdt_btc/btc_usdt), input amount, output amount, Boltz swap ID, Boltz invoice/address, commission amount, fee amount, status (pending/invoice_paid/swap_created/complete/failed/refunded), timestamps.
- **Raffle**: Week number, total pool amount, winner phone hash, prize amount, draw timestamp, status (open/drawn).
- **Config**: Key-value store for commission rate, admin phone number, min/max swap amounts, raffle percentage.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: User receives swap confirmation within 5 seconds of Boltz detecting payment (WebSocket → WhatsApp message).
- **SC-002**: Swap flow completes in 5 messages or fewer from user (direction → amount → confirmation → invoice → done).
- **SC-003**: Bot responds to incoming messages within 2 seconds under normal load.
- **SC-004**: 100% of swaps are persisted with complete audit data (amounts, fees, Boltz ID, status).
- **SC-005**: Bot handles 10 concurrent swap flows without state corruption or cross-user interference.
- **SC-006**: Raffle draw runs automatically every Sunday without manual intervention for 4 consecutive weeks.
- **SC-007**: Admin can view stats within 3 seconds of sending the "admin stats" command.

## Assumptions

- OpenWA API Gateway is deployed and running with at least one active WhatsApp session.
- A dedicated WhatsApp number is available for the bot (not the operator's personal number).
- Boltz API v2 is publicly accessible and does not require API keys for basic swap operations.
- Users understand how to pay Lightning invoices and send on-chain transactions from their own wallets.
- The bot does NOT custody funds — users send directly to Boltz-generated addresses/invoices.
- SQLite is sufficient for the expected scale (<10,000 users, <1,000 swaps/day).
- Python async (asyncio + httpx) is sufficient for the event loop; no web framework needed (no REST API exposed externally).
- The bot runs as a single process — no horizontal scaling needed for v1.
- Commission is collected by configuring the Boltz referral fee or by using a different receive address. For v1, commission is informational only (displayed to user but collected off-chain).
- WhatsApp session authentication (QR scan) is done once during initial setup via the OpenWA dashboard.
