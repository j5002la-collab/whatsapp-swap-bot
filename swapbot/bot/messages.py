"""Message builders for WhatsApp swap bot.
All user-facing messages are built from i18n translation keys.
Falls back to English if a translation is missing.
"""

from swapbot.i18n import t as _t


# ── Welcome ──

def welcome(is_new: bool, lang: str = "en") -> str:
    key = "welcome.new_user" if is_new else "welcome.returning"
    return _t(key, lang)


# ── Help ──

def help_message(lang: str = "en") -> str:
    title = _t("help.title", lang)
    commands = _t("help.commands", lang)
    howto = _t("help.howto", lang)
    faq = _t("help.faq", lang)
    return title + commands + howto + faq


# ── Swap Flow Messages ──

def swap_start(lang: str = "en") -> str:
    return _t("swap.start", lang)


def popular_pairs_menu(lang: str = "en") -> str:
    return _t("swap.popular_pairs", lang)


def source_menu(categories: dict[str, list[str]], lang: str = "en") -> str:
    """Build source currency selection menu."""
    msg = _t("swap.select_source", lang) + "\n\n"
    msg += _t("swap.popular_pairs", lang) + "\n"
    
    popular = categories.get("popular", [])
    for i, ticker in enumerate(popular[:8], 1):
        msg += f"{i}. {ticker.upper()}\n"
    
    msg += "\n" + _t("swap.all_categories", lang) + "\n"
    cat_map = {
        "btc": _t("categories.btc", lang),
        "stablecoins": _t("categories.stablecoins", lang),
        "l1l2": _t("categories.l1l2", lang),
        "defi": _t("categories.defi", lang),
        "meme": _t("categories.meme", lang),
    }
    idx = 9
    for cat_key, cat_label in cat_map.items():
        if categories.get(cat_key):
            msg += f"{idx}. {cat_label}\n"
            idx += 1
    msg += f"{idx}. {_t('categories.search', lang)} 🔍\n\n"
    msg += f"0. {_t('commands.cancel', lang).capitalize()}\n\n"
    msg += "Responde con el número o nombre/ticker."
    return msg


def dest_menu(categories: dict[str, list[str]], from_ticker: str, lang: str = "en") -> str:
    """Build destination currency selection menu, excluding source."""
    msg = _t("swap.select_dest", lang) + "\n\n"
    msg += _t("swap.popular_pairs", lang) + "\n"
    
    popular = [t for t in categories.get("popular", []) if t != from_ticker]
    for i, ticker in enumerate(popular[:8], 1):
        msg += f"{i}. {ticker.upper()}\n"
    
    msg += "\n" + _t("swap.all_categories", lang) + "\n"
    cat_map = {
        "btc": _t("categories.btc", lang),
        "stablecoins": _t("categories.stablecoins", lang),
        "l1l2": _t("categories.l1l2", lang),
        "defi": _t("categories.defi", lang),
        "meme": _t("categories.meme", lang),
    }
    idx = 9
    for cat_key, cat_label in cat_map.items():
        cat_tickers = [t for t in categories.get(cat_key, []) if t != from_ticker]
        if cat_tickers:
            msg += f"{idx}. {cat_label}\n"
            idx += 1
    msg += f"{idx}. {_t('categories.search', lang)} 🔍\n\n"
    msg += f"0. {_t('commands.cancel', lang).capitalize()}\n\n"
    msg += "Responde con el número o nombre/ticker."
    return msg


def category_currencies_menu(category_key: str, tickers: list[str], lang: str = "en", page: int = 0) -> str:
    """Show currencies from a specific category."""
    cat_name = _t(f"categories.{category_key}", lang)
    per_page = 10
    total_pages = (len(tickers) + per_page - 1) // per_page
    start = page * per_page
    page_tickers = tickers[start:start + per_page]
    
    msg = f"📋 *{cat_name}* ({page + 1}/{total_pages})\n\n"
    for i, ticker in enumerate(page_tickers, 1):
        msg += f"{i}. {ticker.upper()}\n"
    
    if total_pages > 1 and page < total_pages - 1:
        msg += "\n*mas* para ver más\n"
    msg += "\n*0* para volver"
    return msg


def network_menu(currency: str, networks: list[dict], lang: str = "en") -> str:
    """Build network selection menu for a currency."""
    msg = _t("swap.select_network", lang, currency=currency.upper()) + "\n"
    for i, net in enumerate(networks, 1):
        msg += f"{i}. {net['display']}\n"
    msg += f"\n0. {_t('commands.cancel', lang).capitalize()}\n\n"
    msg += "Responde con el número."
    return msg


def amount_prompt(from_ticker: str, to_ticker: str, min_amt: str, max_amt: str, lang: str = "en") -> str:
    return _t("swap.enter_amount", lang,
        from_currency=from_ticker.upper(),
        to_currency=to_ticker.upper(),
        min_amount=min_amt,
        max_amount=max_amt)


def estimating(lang: str = "en") -> str:
    return _t("swap.estimating", lang)


def confirm_message(
    from_amount: str, from_currency: str, from_network: str,
    to_amount: str, to_currency: str, to_network: str,
    rate: float, lang: str = "en",
) -> str:
    return _t("swap.confirm", lang,
        from_amount=from_amount, from_currency=from_currency.upper(),
        from_network=from_network,
        to_amount=to_amount, to_currency=to_currency.upper(),
        to_network=to_network,
        rate=f"{rate:.8f}", time_min="2", time_max="30")


def dest_address_prompt(to_ticker: str, to_network: str, lang: str = "en") -> str:
    """Ask user for destination address."""
    return (
        f"📥 *Wallet de destino*\n\n"
        f"Pega la dirección de *{to_ticker.upper()}* en *{to_network}*\n"
        f"donde recibirás los fondos.\n\n"
        f"{_t('commands.cancel', lang).capitalize()} para cancelar."
    )


# ── Swap Result Messages ──

def swap_created(
    from_amount: str, from_currency: str, from_network: str,
    payin_address: str, exchange_id: str, memo: str = "", lang: str = "en",
) -> str:
    return _t("swap.created", lang,
        from_amount=from_amount, from_currency=from_currency.upper(),
        from_network=from_network,
        payin_address=payin_address,
        exchange_id=exchange_id,
        memo=memo)


def swap_progress(status: str, lang: str = "en", **kwargs) -> str:
    return _t(f"swap.status.{status}", lang, **kwargs)


# ── Status / History ──

def status_no_swaps(lang: str = "en") -> str:
    return _t("status_cmd.no_swaps", lang)


def status_header(lang: str = "en") -> str:
    return _t("status_cmd.header", lang)


def status_line(swap_id: str, from_amt: str, from_cur: str, to_cur: str, status: str, lang: str = "en") -> str:
    return _t("status_cmd.swap_line", lang,
        swap_id=swap_id, from_amt=from_amt,
        from_cur=from_cur.upper(), to_cur=to_cur.upper(),
        status=status)


def status_detail(
    exchange_id: str, from_amount: str, from_currency: str, from_network: str,
    to_amount: str, to_currency: str, to_network: str, status: str, lang: str = "en",
) -> str:
    return _t("status_cmd.swap_detail", lang,
        exchange_id=exchange_id,
        from_amount=from_amount, from_currency=from_currency.upper(),
        from_network=from_network,
        to_amount=to_amount, to_currency=to_currency.upper(),
        to_network=to_network, status=status)


# ── Language ──

def language_current(current: str, lang: str = "en") -> str:
    return _t("language.current", lang, language=current)


def language_changed(new_lang: str, lang: str = "en") -> str:
    return _t("language.changed", lang, language=new_lang)


def language_invalid(lang: str = "en") -> str:
    return _t("language.invalid", lang)


# ── Errors ──

def service_unavailable(lang: str = "en") -> str:
    return _t("errors.service_down", lang)


def rate_limited(lang: str = "en") -> str:
    return _t("errors.rate_limit", lang)


def invalid_choice(lang: str = "en") -> str:
    return _t("errors.invalid_choice", lang)


def generic_error(lang: str = "en") -> str:
    return _t("errors.generic", lang)


def swap_cancelled(lang: str = "en") -> str:
    return _t("swap.cancelled", lang)


def swap_expired(lang: str = "en") -> str:
    return _t("swap.expired", lang)


# ── Admin ──

def admin_menu(lang: str = "en") -> str:
    return _t("admin.menu", lang)


def admin_stats(stats: dict, lang: str = "en") -> str:
    return _t("admin.stats", lang,
        today_swaps=stats.get("today_swaps", 0),
        today_volume=stats.get("today_volume", 0),
        total_swaps=stats.get("total_swaps", 0),
        total_volume=stats.get("total_volume", 0),
        total_users=stats.get("total_users", 0))


def admin_users(active: int, total: int, lang: str = "en") -> str:
    return _t("admin.users", lang, active=active, total=total)


def admin_unauthorized(lang: str = "en") -> str:
    return _t("admin.unauthorized", lang)
