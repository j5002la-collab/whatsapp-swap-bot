"""Command handlers for WhatsApp swap bot.
Universal ChangeNOW swap flow with i18n support.
Flow: swap → source currency → source network → dest currency → dest network → amount → estimate → dest address → confirm → execute
"""

import asyncio
import json
import logging
import hashlib

from swapbot.bot.state import UserStateType, UserState, SwapSession
from swapbot.bot import messages as msg
from swapbot.db.queries import (
    update_user_state,
    increment_user_swaps,
    increment_rate_limit,
    get_swap_stats,
    get_active_users_today,
    get_all_users,
    get_pending_swaps,
    get_user_swaps,
    set_user_language,
    get_user_language,
)
from swapbot.i18n import t as _t, SUPPORTED_LANGS

logger = logging.getLogger("bot.handlers")

# ── Popular pair quick-select (pre-filled from+ticker+from_network → dest) ──
POPULAR_PAIRS = [
    ("btc", "usdt", "btc"),      # BTC → USDT
    ("usdt", "btc", "trc20"),    # USDT TRC-20 → BTC  
    ("eth", "usdt", "eth"),      # ETH → USDT
    ("usdt", "eth", "trc20"),    # USDT → ETH
    ("sol", "usdc", "sol"),      # SOL → USDC
    ("doge", "btc", "doge"),     # DOGE → BTC
    ("ltc", "btc", "ltc"),       # LTC → BTC
    ("btc", "eth", "btc"),       # BTC → ETH
]

# Category order for menus
CAT_ORDER = ["popular", "btc", "stablecoins", "l1l2", "defi", "meme", "search"]


# ── Swap Start ──

async def handle_swap_start(router, phone_hash, chat_id, body, state, lang):
    state.start_swap()
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))

    # Show quick popular pairs + category menu
    categories = router.cn.get_categories() if router.cn else {}
    await router.openwa.send_text(chat_id, msg.source_menu(categories, lang))


# ── Source Category Selection ──

async def handle_source_category(router, phone_hash, chat_id, body, state, lang):
    body = body.strip().lower()
    categories = router.cn.get_categories() if router.cn else {}

    # "0" = cancel
    if body == "0":
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        await router.openwa.send_text(chat_id, msg.swap_cancelled(lang))
        return

    # Check if it's a popular pair quick-select (1-8)
    popular = categories.get("popular", [])
    try:
        idx = int(body)
        if 1 <= idx <= len(popular):
            # User selected a specific currency from popular
            ticker = popular[idx - 1]
            networks = router.cn.get_networks(ticker)  # list of CurrencyInfo
            state.select_source_currency(ticker)
            if len(networks) == 1:
                # Auto-select single network
                state.select_source_network(networks[0].network)
                await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
                categories2 = router.cn.get_categories() or {}
                await router.openwa.send_text(chat_id, msg.dest_menu(categories2, ticker, lang))
            else:
                await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
                nets = [{"network": n.network, "display": n.network_display} for n in networks]
                await router.openwa.send_text(chat_id, msg.network_menu(ticker, nets, lang))
            return
    except ValueError:
        pass

    # Check if it's a category number (9-14)
    cat_keys = [k for k in CAT_ORDER[:-1] if categories.get(k)]  # exclude search
    try:
        idx = int(body)
        cat_offset = len(popular) + 1  # categories start after popular
        if cat_offset <= idx < cat_offset + len(cat_keys):
            cat_key = cat_keys[idx - cat_offset]
            tickers = categories.get(cat_key, [])
            state.select_source_category(cat_key)
            await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
            await router.openwa.send_text(chat_id, msg.category_currencies_menu(cat_key, tickers, lang))
            return
        # Search option
        if idx == cat_offset + len(cat_keys):
            await router.openwa.send_text(chat_id,
                f"{_t('categories.search', lang)}: Escribe el nombre o ticker de la moneda que buscas.\n\n0. {_t('commands.cancel', lang).capitalize()}")
            return
    except ValueError:
        pass

    # Try search by name/ticker
    if router.cn:
        results = router.cn.search_currencies(body)
        if results:
            state.select_source_category("search")
            await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
            await router.openwa.send_text(chat_id, msg.category_currencies_menu("search", results, lang))
            return

    await router.openwa.send_text(chat_id, msg.invalid_choice(lang))


# ── Source Currency Selection (from category) ──

async def handle_source_currency(router, phone_hash, chat_id, body, state, lang):
    body = body.strip().lower()
    cat = state.session.current_category
    categories = router.cn.get_categories() if router.cn else {}

    if body == "0":
        state.start_swap()
        await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
        await router.openwa.send_text(chat_id, msg.source_menu(categories, lang))
        return

    if body == "mas" and cat != "search":
        # Next page
        state.session.source_category_page += 1
        tickers = categories.get(cat, [])
        page = state.session.source_category_page
        per_page = 10
        if page * per_page >= len(tickers):
            state.session.source_category_page = 0
        await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
        await router.openwa.send_text(chat_id,
            msg.category_currencies_menu(cat, tickers, lang, state.session.source_category_page))
        return

    # Try number within category
    tickers = categories.get(cat, [])
    if cat == "search":
        # Search results
        if router.cn:
            tickers = router.cn.search_currencies(body) or tickers
    try:
        idx = int(body)
        per_page = 10
        start = state.session.source_category_page * per_page
        tickers = tickers[start:start + per_page]
        if 1 <= idx <= len(tickers):
            ticker = tickers[idx - 1]
            networks = router.cn.get_networks(ticker)
            state.select_source_currency(ticker)
            if len(networks) == 1:
                state.select_source_network(networks[0].network)
                await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
                cats = router.cn.get_categories() or {}
                await router.openwa.send_text(chat_id, msg.dest_menu(cats, ticker, lang))
            else:
                await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
                nets = [{"network": n.network, "display": n.network_display} for n in networks]
                await router.openwa.send_text(chat_id, msg.network_menu(ticker, nets, lang))
            return
    except ValueError:
        pass

    await router.openwa.send_text(chat_id, msg.invalid_choice(lang))


# ── Source Network Selection ──

async def handle_source_network(router, phone_hash, chat_id, body, state, lang):
    body = body.strip()
    ticker = state.session.from_ticker
    networks = router.cn.get_networks(ticker)

    if body == "0":
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        await router.openwa.send_text(chat_id, msg.swap_cancelled(lang))
        return

    try:
        idx = int(body)
        if 1 <= idx <= len(networks):
            network = networks[idx - 1]
            state.select_source_network(network.network)
            await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
            # Show destination menu
            cats = router.cn.get_categories() or {}
            await router.openwa.send_text(chat_id, msg.dest_menu(cats, ticker, lang))
            return
    except ValueError:
        pass

    await router.openwa.send_text(chat_id, msg.invalid_choice(lang))


# ── Dest Category Selection ──

async def handle_dest_category(router, phone_hash, chat_id, body, state, lang):
    """User selects destination category."""
    body = body.strip().lower()
    from_ticker = state.session.from_ticker or ""
    categories = router.cn.get_categories() if router.cn else {}

    if body == "0":
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        await router.openwa.send_text(chat_id, msg.swap_cancelled(lang))
        return

    popular = [t for t in categories.get("popular", []) if t != from_ticker]
    try:
        idx = int(body)
        if 1 <= idx <= len(popular):
            ticker = popular[idx - 1]
            networks = router.cn.get_networks(ticker)
            state.select_dest_currency(ticker)
            if len(networks) == 1:
                state.select_dest_network(networks[0].network)
                await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
                # Ask for amount
                await _ask_amount(router, phone_hash, chat_id, state, lang)
            else:
                await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
                nets = [{"network": n.network, "display": n.network_display} for n in networks]
                await router.openwa.send_text(chat_id, msg.network_menu(ticker, nets, lang))
            return
    except ValueError:
        pass

    # Category numbers
    filtered_cats = [k for k in CAT_ORDER[:-1] if categories.get(k) and any(t != from_ticker for t in categories.get(k, []))]
    try:
        idx = int(body)
        cat_offset = len(popular) + 1
        if cat_offset <= idx < cat_offset + len(filtered_cats):
            cat_key = filtered_cats[idx - cat_offset]
            tickers = [t for t in categories.get(cat_key, []) if t != from_ticker]
            state.select_dest_category(cat_key)
            await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
            await router.openwa.send_text(chat_id, msg.category_currencies_menu(cat_key, tickers, lang))
            return
        if idx == cat_offset + len(filtered_cats):
            await router.openwa.send_text(chat_id,
                f"{_t('categories.search', lang)}: Escribe el nombre o ticker de la moneda que quieres recibir.\n\n0. {_t('commands.cancel', lang).capitalize()}")
            return
    except ValueError:
        pass

    if router.cn:
        results = router.cn.search_currencies(body)
        if results:
            state.select_dest_category("search")
            await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
            await router.openwa.send_text(chat_id, msg.category_currencies_menu("search", results, lang))
            return

    await router.openwa.send_text(chat_id, msg.invalid_choice(lang))


# ── Dest Currency Selection ──

async def handle_dest_currency(router, phone_hash, chat_id, body, state, lang):
    body = body.strip().lower()
    from_ticker = state.session.from_ticker or ""
    cat = state.session.current_category
    categories = router.cn.get_categories() if router.cn else {}

    if body == "0":
        state.select_dest_category("back")  # go back to source selection
        cats = router.cn.get_categories() or {}
        await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
        await router.openwa.send_text(chat_id, msg.dest_menu(cats, from_ticker, lang))
        return

    tickers = [t for t in categories.get(cat, []) if t != from_ticker]
    if cat == "search" and router.cn:
        tickers = router.cn.search_currencies(body) or tickers
    try:
        idx = int(body)
        if 1 <= idx <= len(tickers):
            ticker = tickers[idx - 1]
            networks = router.cn.get_networks(ticker)
            state.select_dest_currency(ticker)
            if len(networks) == 1:
                state.select_dest_network(networks[0].network)
                await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
                await _ask_amount(router, phone_hash, chat_id, state, lang)
            else:
                await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
                nets = [{"network": n.network, "display": n.network_display} for n in networks]
                await router.openwa.send_text(chat_id, msg.network_menu(ticker, nets, lang))
            return
    except ValueError:
        pass

    await router.openwa.send_text(chat_id, msg.invalid_choice(lang))


# ── Dest Network Selection ──

async def handle_dest_network(router, phone_hash, chat_id, body, state, lang):
    body = body.strip()
    ticker = state.session.to_ticker
    networks = router.cn.get_networks(ticker)

    if body == "0":
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        await router.openwa.send_text(chat_id, msg.swap_cancelled(lang))
        return

    try:
        idx = int(body)
        if 1 <= idx <= len(networks):
            network = networks[idx - 1]
            state.select_dest_network(network.network)
            await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
            await _ask_amount(router, phone_hash, chat_id, state, lang)
            return
    except ValueError:
        pass

    await router.openwa.send_text(chat_id, msg.invalid_choice(lang))


async def _ask_amount(router, phone_hash, chat_id, state, lang):
    """Ask user for swap amount with min/max limits."""
    from_ticker = state.session.from_ticker or ""
    to_ticker = state.session.to_ticker or ""
    from_net = state.session.from_network or ""
    to_net = state.session.to_network or ""

    # Get min amount
    min_amt = "1"
    try:
        if router.cn:
            min_amt = await router.cn.get_min_amount(from_ticker, to_ticker, from_net, to_net)
    except Exception:
        pass

    await router.openwa.send_text(
        chat_id,
        msg.amount_prompt(from_ticker, to_ticker, min_amt, "∞", lang)
    )


# ── Amount Entry → Estimate → Address → Confirm ──

async def handle_amount_entry(router, phone_hash, chat_id, body, state, lang):
    """User enters swap amount. Get estimate, then ask for dest address."""
    body = body.strip().replace(",", ".").replace(" ", "")

    if body.lower() in ("cancelar", "cancel", "0"):
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        await router.openwa.send_text(chat_id, msg.swap_cancelled(lang))
        return

    try:
        float(body)
    except ValueError:
        await router.openwa.send_text(chat_id, _t("swap.invalid_amount", lang))
        return

    await router.openwa.send_text(chat_id, msg.estimating(lang))

    # Get estimate
    from_ticker = state.session.from_ticker or ""
    to_ticker = state.session.to_ticker or ""
    from_net = state.session.from_network or ""
    to_net = state.session.to_network or ""

    if not router.cn:
        await router.openwa.send_text(chat_id, msg.service_unavailable(lang))
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        return

    estimate = await router.cn.estimate(from_ticker, to_ticker, body, from_net, to_net)
    if not estimate:
        await router.openwa.send_text(chat_id, msg.service_unavailable(lang))
        return

    state.set_amount_estimation(body, estimate.to_amount, estimate.rate, estimate.rate_id)
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))

    # Show estimate and ask for destination address
    from_info = router.cn.get_currency_info(from_ticker, from_net)
    to_info = router.cn.get_currency_info(to_ticker, to_net)
    from_display = from_info.network_display if from_info else from_net.upper()
    to_display = to_info.network_display if to_info else to_net.upper()

    await router.openwa.send_text(
        chat_id,
        msg.confirm_message(
            body, from_ticker, from_display,
            estimate.to_amount, to_ticker, to_display,
            estimate.rate, lang
        )
    )

    # Also ask for dest address
    await router.openwa.send_text(
        chat_id,
        msg.dest_address_prompt(to_ticker, to_display, lang)
    )


# ── Destination Address Entry ──

async def handle_dest_address(router, phone_hash, chat_id, body, state, lang):
    """User provides destination address. Final confirmation before executing."""
    body = body.strip()

    if body.lower() in ("cancelar", "cancel"):
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        await router.openwa.send_text(chat_id, msg.swap_cancelled(lang))
        return

    if len(body) < 10:
        await router.openwa.send_text(chat_id, "Dirección demasiado corta. Intenta de nuevo.")
        return

    # Check if extra_id/memo is needed
    to_ticker = state.session.to_ticker or ""
    to_net = state.session.to_network or ""
    needs_extra = False
    if router.cn:
        info = router.cn.get_currency_info(to_ticker, to_net)
        needs_extra = info.has_extra_id if info else False

    state.set_dest_address(body)
    state.state = UserStateType.CONFIRMING
    await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))

    # Show final confirmation
    if needs_extra:
        await router.openwa.send_text(
            chat_id,
            f"⚠️ *{to_ticker.upper()} en {to_net.upper()} requiere Memo/Tag*\n\n"
            "Envía el *Memo/Tag* o *no* si no tienes uno.\n\n"
            "Responde con el memo."
        )
        state.state = UserStateType.ENTERING_DEST_ADDRESS  # back to address state for memo
        state.session.extra_id = "required"  # marker
        await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
    else:
        await _ask_final_confirm(router, phone_hash, chat_id, state, lang)


# Handle memo/extra_id entry
async def handle_extra_id(router, phone_hash, chat_id, body, state, lang):
    """User enters memo/tag for currencies that need it."""
    body = body.strip()

    if body.lower() in ("no", "n", "0", ""):
        state.session.extra_id = None
        state.state = UserStateType.CONFIRMING
        await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
        await _ask_final_confirm(router, phone_hash, chat_id, state, lang)
    else:
        state.session.extra_id = body
        state.state = UserStateType.CONFIRMING
        await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
        await _ask_final_confirm(router, phone_hash, chat_id, state, lang)


async def _ask_final_confirm(router, phone_hash, chat_id, state, lang):
    """Show the final confirmation with all details."""
    from_ticker = state.session.from_ticker or ""
    to_ticker = state.session.to_ticker or ""
    from_net = state.session.from_network or ""
    to_net = state.session.to_network or ""

    from_info = router.cn.get_currency_info(from_ticker, from_net) if router.cn else None
    to_info = router.cn.get_currency_info(to_ticker, to_net) if router.cn else None
    from_display = from_info.network_display if from_info else from_net.upper()
    to_display = to_info.network_display if to_info else to_net.upper()

    dest_addr = state.session.dest_address or ""
    extra = state.session.extra_id

    extra_line = ""
    if extra:
        extra_line = f"\nMemo/Tag: `{extra}`"

    confirm_msg = (
        f"📋 *Confirmar intercambio*\n\n"
        f"📤 Envías: {state.session.from_amount} {from_ticker.upper()} ({from_display})\n"
        f"📥 Recibes: ~{state.session.to_amount} {to_ticker.upper()} ({to_display})\n"
        f"📍 Dirección: `{dest_addr}`{extra_line}\n\n"
        f"💱 Tasa: 1 {from_ticker.upper()} = {state.session.rate:.8f} {to_ticker.upper()}\n"
        f"⏱ Tiempo estimado: 2-30 minutos\n\n"
        f"Responde *si* para confirmar o *no* para cancelar."
    )
    await router.openwa.send_text(chat_id, confirm_msg)


# ── Confirmation → Execute ──

async def handle_confirmation(router, phone_hash, chat_id, body, state, lang):
    """User confirms or cancels the swap. Execute if confirmed."""
    body = body.strip().lower()

    # Cancel
    if body in ("no", "n", "cancelar", "cancel", "0"):
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        await router.openwa.send_text(chat_id, msg.swap_cancelled(lang))
        return

    # Confirm
    if body not in ("si", "sí", "s", "yes", "y", "confirmar", "confirm", "ok", "oui"):
        await router.openwa.send_text(chat_id, _t("swap.confirm", lang,
            from_amount=state.session.from_amount or "",
            from_currency=(state.session.from_ticker or "").upper(),
            from_network="",
            to_amount=state.session.to_amount or "",
            to_currency=(state.session.to_ticker or "").upper(),
            to_network="",
            rate=str(state.session.rate),
            time_min="2", time_max="30"))
        return

    await router.openwa.send_text(chat_id, _t("swap.creating", lang))

    # Increment rate limit
    await increment_rate_limit(router.db, phone_hash)

    # Execute via orchestrator
    result = await router.swap.execute_swap(
        phone_hash=phone_hash,
        chat_id=chat_id,
        lang=lang,
        from_ticker=state.session.from_ticker or "",
        to_ticker=state.session.to_ticker or "",
        from_amount=state.session.from_amount or "",
        from_network=state.session.from_network or "",
        to_network=state.session.to_network or "",
        dest_address=state.session.dest_address or "",
        extra_id=state.session.extra_id,
        rate_id=state.session.rate_id,
    )

    if result:
        state.confirm()
        state.session.swap_id = result
        await update_user_state(router.db, phone_hash, json.dumps(state.to_dict()))
    else:
        state.reset()
        await update_user_state(router.db, phone_hash, None)
        await router.openwa.send_text(chat_id, msg.service_unavailable(lang))


# ── Help ──

async def handle_help(router, phone_hash, chat_id, body, state, lang):
    await router.openwa.send_text(chat_id, msg.help_message(lang))


# ── Language ──

async def handle_language(router, phone_hash, chat_id, body, state, lang):
    """Handle language change command: 'lang es' or 'idioma en'"""
    parts = body.strip().lower().split()
    if len(parts) < 2:
        await router.openwa.send_text(chat_id, msg.language_current(lang, lang))
        return

    new_lang = parts[1].strip()
    if new_lang not in SUPPORTED_LANGS:
        await router.openwa.send_text(chat_id, msg.language_invalid(lang))
        return

    await set_user_language(router.db, phone_hash, new_lang)
    await router.openwa.send_text(chat_id, msg.language_changed(new_lang, new_lang))


# ── Status / History ──

async def handle_status(router, phone_hash, chat_id, body, state, lang):
    """Show user's active/pending swaps and recent history."""
    parts = body.strip().split()
    
    # Specific swap ID
    if len(parts) >= 2:
        swap_id = parts[1].upper()
        from swapbot.db.queries import get_swap
        swap = await get_swap(router.db, swap_id)
        if swap:
            cn_id = swap.get("changenow_exchange_id", "")
            if cn_id and router.cn:
                status_data = await router.cn.get_status(cn_id)
                if status_data:
                    await router.openwa.send_text(
                        chat_id,
                        msg.status_detail(
                            exchange_id=cn_id,
                            from_amount=str(swap.get("source_amount", "")),
                            from_currency=swap.get("source_currency", ""),
                            from_network=swap.get("source_network", ""),
                            to_amount=str(swap.get("dest_amount", "")),
                            to_currency=swap.get("dest_currency", ""),
                            to_network=swap.get("dest_network", ""),
                            status=status_data.get("status", swap.get("boltz_status", "unknown")),
                            lang=lang,
                        )
                    )
                    return
            await router.openwa.send_text(chat_id,
                f"📋 Swap: `{swap_id}` | Status: {swap.get('status', 'unknown')}")
        else:
            await router.openwa.send_text(chat_id, f"Swap no encontrado: `{swap_id}`")
        return

    # Show active swaps
    pending = await get_pending_swaps(router.db, phone_hash)
    if not pending:
        await router.openwa.send_text(chat_id, msg.status_no_swaps(lang))
        return

    msg_text = msg.status_header(lang)
    for swap in pending[:5]:
        msg_text += msg.status_line(
            swap.get("swap_id", ""),
            str(swap.get("source_amount", "")),
            swap.get("source_currency", ""),
            swap.get("dest_currency", ""),
            swap.get("boltz_status", swap.get("status", "pending")),
            lang,
        ) + "\n"

    await router.openwa.send_text(chat_id, msg_text)


# ── Cancel ──

async def handle_cancel(router, phone_hash, chat_id, body, state, lang):
    state.reset()
    await update_user_state(router.db, phone_hash, None)
    await router.openwa.send_text(chat_id, msg.swap_cancelled(lang))


# ── Default (unknown message) ──

async def handle_default(router, phone_hash, chat_id, body, state, lang):
    await router.openwa.send_text(
        chat_id,
        f"{_t('welcome.returning', lang)}\n\n"
        f"Envía *{_t('commands.swap', lang)}* para intercambiar.\n"
        f"Envía *{_t('commands.help', lang)}* para ver los comandos."
    )


# ── Admin ──

async def handle_admin(router, phone_hash, chat_id, body, state, lang, contact_name=""):
    body = body.strip()
    parts = body.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "admin":
        if not arg:
            await router.openwa.send_text(chat_id, msg.admin_menu(lang))
            return

        arg_parts = arg.split(maxsplit=1)
        sub_cmd = arg_parts[0]
        sub_arg = arg_parts[1] if len(arg_parts) > 1 else ""

        if sub_cmd == "stats":
            stats = await get_swap_stats(router.db)
            await router.openwa.send_text(chat_id, msg.admin_stats(stats, lang))

        elif sub_cmd == "users":
            active = await get_active_users_today(router.db)
            total = (await get_swap_stats(router.db)).get("total_users", 0)
            await router.openwa.send_text(chat_id, msg.admin_users(active, total, lang))

        elif sub_cmd == "broadcast":
            if not sub_arg:
                await router.openwa.send_text(chat_id, "Uso: admin broadcast <mensaje>")
                return
            users = await get_all_users(router.db)
            await router.openwa.send_text(
                chat_id,
                f"✅ Broadcast enviado a {len(users)} usuarios:\n\n{sub_arg}"
            )

        else:
            await router.openwa.send_text(chat_id, msg.admin_menu(lang))
