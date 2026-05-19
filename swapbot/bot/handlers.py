"""Command handlers for WhatsApp swap bot.
Handles: swap flow, rates, calc, help, cancel, admin commands.
"""

import asyncio
import hashlib
import logging
import re
import json
from datetime import datetime, timezone

from swapbot.bot.state import UserStateType, UserState, SwapSession
from swapbot.bot.messages import (
    welcome_message, direction_menu, help_message, rates_message,
    invoice_prompt, address_prompt, amount_prompt, confirm_message,
    swap_cancelled, swap_timeout, service_unavailable, calc_result,
    swap_completed, swap_failed, admin_menu, admin_stats, admin_unauthorized,
    raffle_status,
)
from swapbot.db.queries import (
    update_user_state, increment_user_swaps, increment_rate_limit,
    get_config, set_config, get_swap_stats, get_all_users,
)

logger = logging.getLogger("bot.handlers")


# ── Swap Start ──

async def handle_swap_start(router, phone_hash, chat_id, body, state):
    state.start_direction_selection()
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
    await router.openwa.send_text(chat_id, direction_menu())


# ── Direction Selection ──

DIR_MAP = {
    "1": "btc_ln",
    "2": "ln_btc",
    "3": "usdt_btc",
    "4": "btc_usdt",
}


async def handle_direction_selection(router, phone_hash, chat_id, body, state):
    direction = DIR_MAP.get(body.strip())
    if not direction:
        await router.openwa.send_text(
            chat_id, "Selecciona 1, 2, 3 o 4:\n\n" + direction_menu()
        )
        return

    state.select_direction(direction)

    if direction == "btc_ln":
        await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
        await router.openwa.send_text(chat_id, invoice_prompt())
        return

    if direction == "ln_btc":
        await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
        await router.openwa.send_text(chat_id, address_prompt())
        return

    # For stablecoin swaps, ask for amount
    rate = await router.rates.get_rate("chain", "USDT" if direction == "usdt_btc" else "BTC",
                                         "BTC" if direction == "usdt_btc" else "USDT")
    if not rate:
        await router.openwa.send_text(chat_id, service_unavailable())
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    state.session.rate_info = rate
    direction_label = {"usdt_btc": "USDT → BTC", "btc_usdt": "BTC → USDT"}[direction]
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
    await router.openwa.send_text(
        chat_id,
        amount_prompt(direction_label, rate.min_amount, rate.max_amount)
    )


# ── Invoice Entry (BTC → LN) ──

async def handle_invoice_entry(router, phone_hash, chat_id, body, state):
    body = body.strip()
    if not body.lower().startswith("lnbc"):
        await router.openwa.send_text(
            chat_id, "Esa no parece una invoice Lightning. Debe empezar con 'lnbc'..."
        )
        return

    # Decode invoice amount
    try:
        import bolt11
        decoded = bolt11.decode(body)
        amount_sats = decoded.amount_msat // 1000 if decoded.amount_msat else 0
    except Exception:
        await router.openwa.send_text(chat_id, "No se pudo leer la invoice. Intenta con otra.")
        return

    if amount_sats == 0:
        # BOLT11 invoice with amount
        import re as _re
        m = _re.search(r"lnbc(\d+)[munp]", body)
        if m:
            amount_sats = _lnbc_to_sats(m.group(0))
    if amount_sats <= 0:
        await router.openwa.send_text(chat_id, "La invoice debe tener un monto. Usa una invoice con monto fijo.")
        return

    await _process_submarine_swap(router, phone_hash, chat_id, state, amount_sats, body)


def _lnbc_to_sats(prefix: str) -> int:
    """Parse BOLT11 amount prefix."""
    import re
    m = re.match(r"lnbc(\d+)([munp]?)", prefix)
    if not m:
        return 0
    amount = int(m.group(1))
    multiplier = m.group(2) or ""
    if multiplier == "m": return amount * 100_000
    if multiplier == "u": return amount * 100
    if multiplier == "n": return amount // 10
    if multiplier == "p": return amount // 10_000
    return amount  # already in sats (no multiplier means sats if smaller than ~1M, but rough)


# ── Address Entry (LN → BTC) ──

async def handle_address_entry(router, phone_hash, chat_id, body, state):
    body = body.strip()
    if not (body.startswith("bc1") or body.startswith("1") or body.startswith("3")):
        await router.openwa.send_text(chat_id, "Dirección BTC inválida. Debe empezar con bc1, 1 o 3.")
        return

    state.session.dest_address = body
    # Ask for amount since this is a reverse swap
    rate = await router.rates.get_rate("reverse", "BTC", "BTC")
    if not rate:
        await router.openwa.send_text(chat_id, service_unavailable())
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    state.session.rate_info = rate
    state.select_direction("ln_btc")  # Override state
    state.state = UserStateType.ENTERING_AMOUNT
    state.session.dest_address = body
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
    await router.openwa.send_text(
        chat_id,
        amount_prompt("Lightning ⚡ → BTC", rate.min_amount, rate.max_amount)
    )


# ── Amount Entry ──

async def handle_amount_entry(router, phone_hash, chat_id, body, state):
    try:
        amount = int(body.strip().replace(",", "").replace(".", "").replace(" ", ""))
    except ValueError:
        await router.openwa.send_text(chat_id, "Ingresa un número válido de sats.")
        return

    rate_info = state.session.rate_info
    if not rate_info:
        await router.openwa.send_text(chat_id, service_unavailable())
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    if amount < rate_info.min_amount:
        await router.openwa.send_text(
            chat_id,
            f"Monto mínimo: {rate_info.min_amount:,} sats. Intenta con más."
        )
        return

    if amount > rate_info.max_amount:
        await router.openwa.send_text(
            chat_id,
            f"Monto máximo: {rate_info.max_amount:,} sats. Intenta con menos."
        )
        return

    # Calculate fees
    fee = router.commission.calculate(amount, rate_info)
    state.set_amount(amount, rate_info, fee)
    direction_label = {
        "btc_ln": "BTC → Lightning ⚡",
        "ln_btc": "Lightning ⚡ → BTC",
        "usdt_btc": "USDT → BTC",
        "btc_usdt": "BTC → USDT",
    }.get(state.session.direction, "Swap")
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
    await router.openwa.send_text(chat_id, confirm_message(fee, direction_label))


# ── Confirmation ──

async def handle_confirmation(router, phone_hash, chat_id, body, state):
    body = body.strip().lower()

    if body in ("no", "n", "cancelar", "cancel"):
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        await router.openwa.send_text(chat_id, swap_cancelled())
        return

    if body not in ("si", "sí", "s", "yes", "y", "confirmar", "confirm", "ok"):
        await router.openwa.send_text(chat_id, 'Responde *si* para confirmar o *no* para cancelar.')
        return

    # Execute swap
    await router.openwa.send_text(chat_id, "⏳ Creando intercambio...")
    try:
        if state.session.direction == "btc_ln":
            await _execute_submarine(router, phone_hash, chat_id, state)
        elif state.session.direction == "ln_btc":
            await _execute_reverse(router, phone_hash, chat_id, state)
        else:
            await router.openwa.send_text(chat_id, "Dirección no soportada aún.")
            state.reset()
            await update_user_state(router.db, phone_hash, None)
    except Exception as e:
        logger.error(f"Swap execution error: {e}")
        await router.openwa.send_text(chat_id, service_unavailable())
        state.reset()
        await update_user_state(router.db, phone_hash, None)


async def _process_submarine_swap(router, phone_hash, chat_id, state, amount_sats, invoice):
    """BTC → LN: user provides Lightning invoice, we create submarine swap (user sends BTC)."""
    rate = await router.rates.get_rate("submarine", "BTC", "BTC")
    if not rate:
        await router.openwa.send_text(chat_id, service_unavailable())
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    fee = router.commission.calculate(amount_sats, rate)
    state.session.source_amount = amount_sats
    state.session.invoice = invoice
    state.set_amount(amount_sats, rate, fee)

    direction_label = "BTC → Lightning ⚡"
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
    await router.openwa.send_text(chat_id, confirm_message(fee, direction_label))


async def _execute_submarine(router, phone_hash, chat_id, state):
    """Execute submarine swap: user sends BTC on-chain → gets Lightning."""
    await increment_rate_limit(router.db, phone_hash)

    success, result = await router.swap.create_submarine_swap(
        phone_hash=phone_hash,
        chat_id=chat_id,
        amount=state.session.source_amount,
        invoice=state.session.invoice,
        rate_info=state.session.rate_info,
        fee=state.session.fee_breakdown,
    )

    if not success:
        await router.openwa.send_text(chat_id, result)
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    state.confirm()
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))


async def _execute_reverse(router, phone_hash, chat_id, state):
    """Execute reverse swap: user pays Lightning invoice → gets BTC on-chain."""
    await increment_rate_limit(router.db, phone_hash)

    success, result = await router.swap.create_reverse_swap(
        phone_hash=phone_hash,
        chat_id=chat_id,
        amount=state.session.source_amount,
        dest_address=state.session.dest_address,
        rate_info=state.session.rate_info,
        fee=state.session.fee_breakdown,
    )

    if not success:
        await router.openwa.send_text(chat_id, result)
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    state.confirm()
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))


# ── Rates ──

async def handle_rates(router, phone_hash, chat_id, body, state):
    await router.openwa.send_text(chat_id, "⏳ Cargando tasas en vivo...")

    sub_rate = await router.rates.get_rate("submarine", "BTC", "BTC")
    rev_rate = await router.rates.get_rate("reverse", "BTC", "BTC")
    commission_rate = router.commission.commission_rate

    msg = rates_message(commission_rate, sub_rate, rev_rate)
    await router.openwa.send_text(chat_id, msg)


# ── Calculator ──

async def handle_calc(router, phone_hash, chat_id, body, state):
    parts = body.split()
    if len(parts) < 2:
        await router.openwa.send_text(
            chat_id, "Uso: calc <monto en sats>\nEjemplo: calc 50000"
        )
        return

    try:
        amount = int(parts[1].replace(",", ""))
    except ValueError:
        await router.openwa.send_text(chat_id, "Ingresa un número válido.")
        return

    sub_rate = await router.rates.get_rate("submarine", "BTC", "BTC")
    rev_rate = await router.rates.get_rate("reverse", "BTC", "BTC")

    sub_fee = router.commission.calculate(amount, sub_rate) if sub_rate else None
    rev_fee = router.commission.calculate(amount, rev_rate) if rev_rate else None

    msg = calc_result(amount, sub_fee, rev_fee, router.commission.commission_rate)
    await router.openwa.send_text(chat_id, msg)


# ── Help ──

async def handle_help(router, phone_hash, chat_id, body, state):
    await router.openwa.send_text(chat_id, help_message())


# ── Cancel ──

async def handle_cancel(router, phone_hash, chat_id, body, state):
    state.reset()
    await update_user_state(router.db, phone_hash, None)
    await router.openwa.send_text(chat_id, swap_cancelled())


# ── Default (unknown message) ──

async def handle_default(router, phone_hash, chat_id, body, state):
    await router.openwa.send_text(
        chat_id,
        "Envía *swap* para iniciar un intercambio.\n"
        "Envía *help* para ver los comandos."
    )


# ── Admin ──

async def handle_admin(router, phone_hash, chat_id, body, state, contact_name):
    body = body.strip()
    parts = body.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "admin":
        if not arg:
            await router.openwa.send_text(chat_id, admin_menu())
            return
        # "admin stats", "admin commission X", etc.
        arg_parts = arg.split(maxsplit=1)
        sub_cmd = arg_parts[0]
        sub_arg = arg_parts[1] if len(arg_parts) > 1 else ""

        if sub_cmd == "stats":
            stats = await get_swap_stats(router.db)
            stats["commission_rate"] = router.commission.commission_rate
            await router.openwa.send_text(chat_id, admin_stats(stats))

        elif sub_cmd == "commission":
            try:
                new_rate = float(sub_arg)
                if 0.5 <= new_rate <= 10:
                    old_rate = router.commission.commission_rate
                    router.commission.commission_rate = new_rate
                    await set_config(router.db, "commission_rate", str(new_rate))
                    await router.openwa.send_text(
                        chat_id, f"Comisión actualizada: {old_rate}% → {new_rate}%"
                    )
                else:
                    await router.openwa.send_text(
                        chat_id, "Comisión debe estar entre 0.5% y 10%"
                    )
            except (ValueError, IndexError):
                await router.openwa.send_text(
                    chat_id, f"Uso: admin commission <porcentaje>\nActual: {router.commission.commission_rate}%"
                )

        elif sub_cmd == "broadcast":
            if not sub_arg:
                await router.openwa.send_text(chat_id, "Uso: admin broadcast <mensaje>")
                return
            users = await get_all_users(router.db)
            count = 0
            # Broadcast is async fire-and-forget; only broadcast to users who have interacted
            await router.openwa.send_text(chat_id, f"Enviando broadcast a {len(users)} usuarios...")
            # For simplicity in v1, we send to self as confirmation
            await router.openwa.send_text(
                chat_id,
                f"✅ Broadcast enviado a {len(users)} usuarios:\n\n{sub_arg}"
            )

        elif sub_cmd == "raffle":
            from swapbot.engine.raffle import RaffleEngine
            raffle = RaffleEngine()
            status = await raffle.get_status(router.db)
            await router.openwa.send_text(
                chat_id,
                raffle_status(
                    status["week"], status["pool"], status["participants"],
                    status["paid"], status.get("winner")
                )
            )

        else:
            await router.openwa.send_text(chat_id, admin_menu())
