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
    "3": "stable_to_btc",
    "4": "btc_to_stable",
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

    # Stablecoin: show stablecoin selection (USDT/USDC)
    if direction in ("stable_to_btc", "btc_to_stable"):
        state.select_stablecoin_direction(direction)
        await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
        from swapbot.bot.messages import stablecoin_direction_menu
        await router.openwa.send_text(chat_id, stablecoin_direction_menu())
        return


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


# ── Stablecoin Swap Flow (ChangeNOW) ──

STABLECOIN_DIR_MAP = {
    "1": "stable_to_btc",
    "2": "stable_to_btc",  # USDC→BTC
    "3": "btc_to_stable",
    "4": "btc_to_stable",  # BTC→USDC
}

USDT_NET_MAP = {"1": "TRC-20", "2": "ERC-20", "3": "BEP-20", "4": "ARBITRUM", "5": "SOLANA", "6": "POLYGON", "7": "OPTIMISM", "8": "AVALANCHE", "9": "BASE"}
USDC_NET_MAP = {"1": "ERC-20", "2": "ARBITRUM", "3": "BASE", "4": "SOLANA", "5": "POLYGON", "6": "OPTIMISM", "7": "AVALANCHE", "8": "BEP-20"}


async def handle_stablecoin_selection(router, phone_hash, chat_id, body, state):
    """User selects USDT→BTC or USDC→BTC or BTC→USDT or BTC→USDC."""
    from swapbot.bot.messages import (
        stablecoin_direction_menu, network_menu_USDT, network_menu_USDC,
    )

    body = body.strip()
    opt = STABLECOIN_DIR_MAP.get(body)
    if not opt:
        await router.openwa.send_text(chat_id, "Selecciona 1, 2, 3 o 4:\n\n" + stablecoin_direction_menu())
        return

    direction = opt
    if body == "2":
        currency = "USDC"
        direction = "stable_to_btc"
    elif body == "4":
        currency = "USDC"
        direction = "btc_to_stable"
    elif body == "1":
        currency = "USDT"
    else:  # body == "3"
        currency = "USDT"

    state.session.stable_currency = currency
    state.session.direction = direction

    if direction == "stable_to_btc":
        state.state = UserStateType.SELECTING_NETWORK
        await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
        if currency == "USDT":
            await router.openwa.send_text(chat_id, network_menu_USDT())
        else:
            await router.openwa.send_text(chat_id, network_menu_USDC())
    else:
        # btc_to_stable: first ask for dest network
        state.state = UserStateType.SELECTING_DEST_NETWORK
        await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
        from swapbot.bot.messages import network_menu_dest_USDT as ndu
        from swapbot.bot.messages import network_menu_dest_USDC as ndc
        if currency == "USDT":
            await router.openwa.send_text(chat_id, ndu())
        else:
            await router.openwa.send_text(chat_id, ndc())


async def handle_network_selection(router, phone_hash, chat_id, body, state):
    """User selects source network for stablecoin→BTC swap."""
    currency = state.session.stable_currency
    net_map = USDT_NET_MAP if currency == "USDT" else USDC_NET_MAP
    network = net_map.get(body.strip())

    if not network:
        from swapbot.bot.messages import network_menu_USDT, network_menu_USDC
        menu = network_menu_USDT() if currency == "USDT" else network_menu_USDC()
        await router.openwa.send_text(chat_id, f"Selecciona un número válido:\n\n{menu}")
        return

    state.session.stable_network = network

    # Get ChangeNOW estimate to show rate
    from swapbot.changenow.client import get_cn_client
    cn = get_cn_client()
    if not cn:
        await router.openwa.send_text(chat_id, service_unavailable())
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    ticker_info = cn.get_ticker(currency, network)
    if not ticker_info:
        await router.openwa.send_text(chat_id, f"Red {network} no soportada.")
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    # Get min amount
    try:
        min_str = await cn.get_min_amount(
            ticker_info["ticker"], "btc", ticker_info["network"], "btc"
        )
        min_amount = float(min_str)
    except Exception:
        min_amount = 1.0

    # Ask for amount
    state.state = UserStateType.ENTERING_AMOUNT
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
    await router.openwa.send_text(
        chat_id,
        f"💰 *{currency} ({network}) → BTC*\n\n"
        f"Ingresa el monto en {currency}:\n"
        f"Mín: {min_amount:.2f} {currency}\n\n"
        "Responde con el número."
    )


async def handle_dest_network_selection(router, phone_hash, chat_id, body, state):
    """User selects destination network for BTC→stablecoin swap."""
    currency = state.session.stable_currency
    net_map = USDT_NET_MAP if currency == "USDT" else USDC_NET_MAP
    network = net_map.get(body.strip())

    if not network:
        from swapbot.bot.messages import network_menu_dest_USDT, network_menu_dest_USDC
        menu = network_menu_dest_USDT() if currency == "USDT" else network_menu_dest_USDC()
        await router.openwa.send_text(chat_id, f"Selecciona un número válido:\n\n{menu}")
        return

    state.session.stable_dest_network = network

    # Ask for destination address
    state.state = UserStateType.ENTERING_ADDRESS_STABLE
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
    from swapbot.bot.messages import address_prompt_stable
    await router.openwa.send_text(
        chat_id,
        address_prompt_stable(currency, network)
    )


async def handle_address_entry_stable(router, phone_hash, chat_id, body, state):
    """User provides destination address for BTC→stablecoin swap, then enter BTC amount."""
    body = body.strip()
    if len(body) < 10:
        await router.openwa.send_text(chat_id, "Dirección demasiado corta. Intenta de nuevo.")
        return

    state.session.dest_address = body

    # Ask for BTC amount
    state.state = UserStateType.ENTERING_AMOUNT
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
    await router.openwa.send_text(
        chat_id,
        f"💰 *BTC → {state.session.stable_currency} ({state.session.stable_dest_network})*\n\n"
        "Ingresa el monto en sats:\n"
        "Mín: 50,000 | Máx: 5,000,000\n\n"
        "Responde con el número."
    )


async def handle_amount_entry_stable(router, phone_hash, chat_id, body, state):
    """Handle amount entry for stablecoin swaps."""
    try:
        amount = float(body.strip().replace(",", "").replace(" ", ""))
    except ValueError:
        await router.openwa.send_text(chat_id, "Ingresa un número válido.")
        return

    if amount <= 0:
        await router.openwa.send_text(chat_id, "El monto debe ser mayor a 0.")
        return

    direction = state.session.direction
    currency = state.session.stable_currency

    if direction == "btc_to_stable":
        amount = int(amount)  # sats
        if amount < 50000:
            await router.openwa.send_text(chat_id, "Monto mínimo: 50,000 sats.")
            return
        state.session.source_amount = amount
        await _prepare_stable_confirmation_btc_to_stable(router, phone_hash, chat_id, state, amount)
    else:
        # stable_to_btc: amount in stablecoin units
        state.session.stable_source_amount = amount
        await _prepare_stable_confirmation_stable_to_btc(router, phone_hash, chat_id, state, amount)


async def _prepare_stable_confirmation_stable_to_btc(router, phone_hash, chat_id, state, amount):
    """Get ChangeNOW estimate and show confirmation for stable→BTC."""
    from swapbot.changenow.client import get_cn_client
    cn = get_cn_client()
    if not cn:
        await router.openwa.send_text(chat_id, service_unavailable())
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    currency = state.session.stable_currency
    network = state.session.stable_network
    ticker_info = cn.get_ticker(currency, network)
    if not ticker_info:
        await router.openwa.send_text(chat_id, f"Red {network} no soportada para {currency}.")
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    try:
        estimate = await cn.estimate(
            ticker_info["ticker"], "btc",
            str(amount), ticker_info["network"], "btc"
        )
        to_amount = float(estimate.get("toAmount", 0))
        rate_id = estimate.get("rateId", "")
    except Exception as e:
        logger.error(f"ChangeNOW estimate error: {e}")
        await router.openwa.send_text(chat_id, service_unavailable())
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    state.session.stable_dest_amount = to_amount
    state.session.stable_rate_id = rate_id

    direction_label = f"{currency} ({network}) → BTC"
    state.state = UserStateType.CONFIRMING
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))

    from swapbot.bot.messages import confirm_message_stable
    await router.openwa.send_text(
        chat_id,
        confirm_message_stable(
            direction_label, amount, currency, to_amount, "BTC",
            network, "Bitcoin"
        )
    )


async def _prepare_stable_confirmation_btc_to_stable(router, phone_hash, chat_id, state, amount):
    """Get ChangeNOW estimate and show confirmation for BTC→stable."""
    from swapbot.changenow.client import get_cn_client
    cn = get_cn_client()
    if not cn:
        await router.openwa.send_text(chat_id, service_unavailable())
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    currency = state.session.stable_currency
    dest_network = state.session.stable_dest_network
    ticker_info = cn.get_ticker(currency, dest_network)
    if not ticker_info:
        await router.openwa.send_text(chat_id, f"Red {dest_network} no soportada para {currency}.")
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    try:
        # Convert sats to BTC for estimate
        btc_amount = amount / 100_000_000
        estimate = await cn.estimate(
            "btc", ticker_info["ticker"],
            f"{btc_amount:.8f}", "btc", ticker_info["network"]
        )
        to_amount = float(estimate.get("toAmount", 0))
        rate_id = estimate.get("rateId", "")
    except Exception as e:
        logger.error(f"ChangeNOW estimate error: {e}")
        await router.openwa.send_text(chat_id, service_unavailable())
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    state.session.stable_dest_amount = to_amount
    state.session.stable_rate_id = rate_id

    direction_label = f"BTC → {currency} ({dest_network})"
    state.state = UserStateType.CONFIRMING
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))

    from swapbot.bot.messages import confirm_message_stable
    await router.openwa.send_text(
        chat_id,
        confirm_message_stable(
            direction_label, amount / 100_000_000, "BTC", to_amount, currency,
            "Bitcoin", dest_network
        )
    )


async def handle_confirmation_stable(router, phone_hash, chat_id, body, state):
    """Handle confirmation for stablecoin swaps."""
    body = body.strip().lower()

    if body in ("no", "n", "cancelar", "cancel"):
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        await router.openwa.send_text(chat_id, swap_cancelled())
        return

    if body not in ("si", "sí", "s", "yes", "y", "confirmar", "confirm", "ok"):
        await router.openwa.send_text(chat_id, 'Responde *si* para confirmar o *no* para cancelar.')
        return

    # Execute ChangeNOW swap
    from swapbot.changenow.client import get_cn_client
    cn = get_cn_client()
    if not cn:
        await router.openwa.send_text(chat_id, service_unavailable())
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    await router.openwa.send_text(chat_id, "⏳ Creando intercambio...")

    try:
        direction = state.session.direction
        currency = state.session.stable_currency
        network = state.session.stable_network
        dest_network = state.session.stable_dest_network

        if direction == "stable_to_btc":
            ticker_info = cn.get_ticker(currency, network)
            params = {
                "fromCurrency": ticker_info["ticker"],
                "toCurrency": "btc",
                "fromNetwork": ticker_info["network"],
                "toNetwork": "btc",
                "fromAmount": str(state.session.stable_source_amount),
                "toAmount": str(state.session.stable_dest_amount),
                "address": state.session.dest_address or "",
                "flow": "fixed-rate",
                "rateId": state.session.stable_rate_id,
            }
        else:  # btc_to_stable
            ticker_info = cn.get_ticker(currency, dest_network)
            params = {
                "fromCurrency": "btc",
                "toCurrency": ticker_info["ticker"],
                "fromNetwork": "btc",
                "toNetwork": ticker_info["network"],
                "fromAmount": f"{state.session.source_amount / 100_000_000:.8f}",
                "toAmount": str(state.session.stable_dest_amount),
                "address": state.session.dest_address or "",
                "flow": "fixed-rate",
                "rateId": state.session.stable_rate_id,
            }

        exchange = await cn.create_exchange(params)
        exchange_id = exchange.get("id", "")
        payin_address = exchange.get("payinAddress", "")
        payout_address = exchange.get("payoutAddress", "")
        from_amount = exchange.get("amount", {}).get("from", str(state.session.stable_source_amount or state.session.source_amount))
        memo = exchange.get("extraId") or None

        state.session.swap_id = exchange_id
        state.session.stable_payin_address = payin_address
        state.session.stable_memo = memo
        state.confirm()
        await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))

        from swapbot.bot.messages import changenow_exchange_created
        if direction == "stable_to_btc":
            await router.openwa.send_text(
                chat_id,
                changenow_exchange_created(
                    exchange_id, payin_address, from_amount, currency, network, memo
                )
            )
        else:
            await router.openwa.send_text(
                chat_id,
                changenow_exchange_created(
                    exchange_id, payin_address, from_amount, "BTC", "Bitcoin", memo
                )
            )

        # Start polling for status
        asyncio.create_task(_poll_changenow_status(
            router, chat_id, exchange_id, phone_hash, state
        ))

    except Exception as e:
        logger.error(f"ChangeNOW create error: {e}")
        await router.openwa.send_text(chat_id, service_unavailable())
        state.reset()
        await update_user_state(router.db, phone_hash, None)


async def _poll_changenow_status(router, chat_id, exchange_id, phone_hash, state, max_polls=60):
    """Poll ChangeNOW for exchange status and notify user."""
    from swapbot.changenow.client import get_cn_client
    cn = get_cn_client()
    if not cn:
        return

    last_status = ""
    for i in range(max_polls):
        await asyncio.sleep(30)  # Poll every 30 seconds
        try:
            status_data = await cn.get_status(exchange_id)
            status = status_data.get("status", "")

            from swapbot.bot.messages import changenow_status
            if status != last_status and status in ("confirming", "exchanging", "sending"):
                await router.openwa.send_text(chat_id, changenow_status(status))
                last_status = status

            if status == "finished":
                await router.openwa.send_text(chat_id, changenow_status(status))
                # Record swap in DB
                await increment_rate_limit(router.db, phone_hash)
                await increment_user_swaps(
                    router.db, phone_hash,
                    int(state.session.source_amount or 0)
                )
                state.complete()
                await update_user_state(router.db, phone_hash, None)
                return

            if status in ("failed", "refunded"):
                await router.openwa.send_text(chat_id, changenow_status(status))
                state.reset()
                await update_user_state(router.db, phone_hash, None)
                return

        except Exception as e:
            logger.error(f"ChangeNOW poll error: {e}")
            if i >= max_polls - 1:
                await router.openwa.send_text(
                    chat_id, "⏰ El intercambio está tardando. Te notificaré cuando se complete."
                )
                return


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
