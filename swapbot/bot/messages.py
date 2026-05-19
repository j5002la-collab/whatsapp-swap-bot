"""Message templates for WhatsApp swap bot.
WhatsApp-native: numbered menus, emoji indicators, Spanish language.
"""

from swapbot.engine.rates import RateInfo
from swapbot.engine.commission import FeeBreakdown


# --- Welcome / Help ---

def welcome_message(commission_rate: float) -> str:
    return (
        "🔄 *SwapBot WhatsApp*\n"
        "Cambios instantáneos BTC ↔ Lightning\n"
        "Sin custodia · Sin registro · Sin KYC\n\n"
        f"Comisión: {commission_rate}% | Sorteo semanal: 0.1%\n\n"
        "Selecciona una opción:"
    )


def direction_menu() -> str:
    return (
        "🔄 *SwapBot WhatsApp*\n"
        "Comisión: 2.5% | Sorteo semanal: 0.1%\n\n"
        "*Selecciona dirección:*\n"
        "1. BTC → Lightning ⚡\n"
        "2. Lightning ⚡ → BTC\n"
        "3. USDT → BTC\n"
        "4. BTC → USDT\n\n"
        "Responde con el número."
    )


def help_message() -> str:
    return (
        "❓ *SwapBot Ayuda*\n\n"
        "*Comandos disponibles:*\n"
        "• *swap* o *cambiar* — Iniciar un intercambio\n"
        "• *rates* o *tasas* — Ver tasas en vivo\n"
        "• *calc 50000* o *calcular 50000* — Calcular recibirás\n"
        "• *help* o *ayuda* — Este menú\n"
        "• *cancelar* — Cancelar intercambio en curso\n\n"
        "💡 *Cómo funciona:*\n"
        "1. Envía *swap* para empezar\n"
        "2. Selecciona la dirección (1-4)\n"
        "3. Ingresa el monto o invoice\n"
        "4. Confirma el resumen\n"
        "5. Paga la invoice/dirección\n"
        "6. Recibe confirmación automática"
    )


# --- Swap Flow Messages ---

def invoice_prompt() -> str:
    return (
        "📥 *BTC → Lightning ⚡*\n\n"
        "Pega tu invoice de Lightning (lnbc...).\n"
        "El monto se detectará automáticamente.\n\n"
        "Responde con la invoice."
    )


def address_prompt() -> str:
    return (
        "📥 *Lightning ⚡ → BTC*\n\n"
        "Pega tu dirección BTC (bc1...)\n"
        "donde recibirás los fondos.\n\n"
        "Responde con la dirección."
    )


def amount_prompt(direction_label: str, min_amount: int, max_amount: int) -> str:
    return (
        f"💰 *{direction_label}*\n\n"
        f"Ingresa el monto en sats:\n"
        f"Mín: {min_amount:,} | Máx: {max_amount:,}\n\n"
        "Responde con el número."
    )


def confirm_message(fee: FeeBreakdown, direction_label: str) -> str:
    """Generate confirmation message with rate and fee breakdown."""
    raffle = int(fee.source_amount * 0.001)
    lines = [
        f"📋 *Confirmar {direction_label}*",
        "",
        f"Envías: {fee.source_amount:,} sats",
        f"Recibes: {fee.estimated_receive:,} sats",
        "",
        "*Comisiones incluidas:*",
        f"  ├ SwapBot ({fee.commission_rate}%): {fee.commission_amount:,} sats",
        f"  ├ Minería: {fee.boltz_miner_fee:,} sats",
        f"  └ Sorteo (0.1%): {raffle:,} sats",
        "",
        "⏱ Tiempo estimado: 10-30 minutos",
        "",
        "Responde *si* para confirmar o *no* para cancelar.",
    ]
    return "\n".join(lines)


def swap_created_submarine(swap_id: str, address: str, expected_amount: int, fee: FeeBreakdown) -> str:
    """Message when submarine swap is created (user must send BTC)."""
    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✅ *INTERCAMBIO CREADO*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📤 Envía exactamente *{expected_amount:,} sats* a:\n\n"
        f"`{address}`\n\n"
        f"Recibirás: {fee.estimated_receive:,} sats en Lightning\n"
        f"Comisión: {fee.commission_amount:,} sats\n\n"
        "⏳ _Esperando transacción on-chain..._"
    )


def swap_created_reverse(swap_id: str, invoice: str, amount: int, fee: FeeBreakdown) -> str:
    """Message when reverse swap is created (user must pay Lightning invoice)."""
    return (
        "⚡ *Intercambio creado (Lightning → BTC)*\n\n"
        f"Paga esta invoice desde tu wallet Lightning:\n\n"
        f"`{invoice}`\n\n"
        f"Monto a pagar: {amount:,} sats\n"
        f"Recibirás: {fee.estimated_receive:,} sats en BTC\n"
        f"Comisión: {fee.commission_amount:,} sats\n\n"
        "⏱ Al pagar, 2-10 min."
    )


def swap_completed(sent: int, received: int, swap_id: str) -> str:
    return (
        "🎉 *¡Swap completado!*\n\n"
        f"Enviaste: {sent:,} sats\n"
        f"Recibiste: {received:,} sats\n"
        f"Swap: `{swap_id}`\n\n"
        "Envía *swap* para un nuevo intercambio."
    )


def swap_failed(swap_id: str, status: str) -> str:
    return (
        f"❌ *Swap no completado*\n\n"
        f"ID: `{swap_id}`\n"
        f"Estado: {status}\n\n"
        "Contacta a soporte si necesitas ayuda."
    )


def swap_timeout() -> str:
    return "⏰ Sesión expirada. Envía *swap* para empezar de nuevo."


def swap_cancelled() -> str:
    return "Cancelado. Envía *swap* para un nuevo intercambio."


def service_unavailable() -> str:
    return "⚠️ Servicio temporalmente no disponible. Intenta de nuevo en unos minutos."


# --- Rates ---

def rates_message(
    commission_rate: float,
    sub_rate: RateInfo | None,
    rev_rate: RateInfo | None,
) -> str:
    lines = [
        "📊 *Tasas en vivo*",
        "",
        f"Comisión SwapBot: {commission_rate}%",
        "",
    ]

    if sub_rate:
        lines.append("*BTC On-chain → Lightning:*")
        lines.append(f"  Tasa: 1 BTC = {sub_rate.user_rate:.8f} BTC (Lightning)")
        lines.append(f"  Fee red: {sub_rate.boltz_fee_pct}% + {sub_rate.boltz_miner_fee} sats")
        lines.append(f"  Mín: {sub_rate.min_amount:,} sats | Máx: {sub_rate.max_amount:,} sats")
        lines.append("")

    if rev_rate:
        lines.append("*Lightning → BTC On-chain:*")
        lines.append(f"  Tasa: 1 BTC (LN) = {rev_rate.user_rate:.8f} BTC")
        lines.append(f"  Fee red: {rev_rate.boltz_fee_pct}% + {rev_rate.boltz_miner_fee} sats")
        lines.append(f"  Mín: {rev_rate.min_amount:,} sats | Máx: {rev_rate.max_amount:,} sats")

    if not sub_rate and not rev_rate:
        lines.append("⚠️ No se pudieron obtener las tasas.")

    return "\n".join(lines)


# --- Calculator ---

def calc_result(
    amount: int,
    sub_fee: FeeBreakdown | None,
    rev_fee: FeeBreakdown | None,
    commission_rate: float,
) -> str:
    lines = ["🧮 *Calculadora SwapBot*\n"]

    if sub_fee:
        lines.append("*BTC On-chain → Lightning*")
        lines.append(f"Envías: {amount:,} sats")
        lines.append(f"Recibes: ~{sub_fee.estimated_receive:,} sats")
        lines.append(f"Fee red: {sub_fee.boltz_fee_amount:,} sats ({sub_fee.boltz_fee_rate}%)")
        lines.append(f"Comisión: {sub_fee.commission_amount:,} sats")
        lines.append("")

    if rev_fee:
        lines.append("*Lightning → BTC On-chain*")
        lines.append(f"Envías: {amount:,} sats")
        lines.append(f"Recibes: ~{rev_fee.estimated_receive:,} sats")
        lines.append(f"Fee red: {rev_fee.boltz_fee_amount:,} sats ({rev_fee.boltz_fee_rate}%)")
        lines.append(f"Comisión: {rev_fee.commission_amount:,} sats")
        lines.append("")

    if not sub_fee and not rev_fee:
        lines.append("⚠️ No se pudieron obtener tasas.")

    lines.append(f"🎁 Sorteo semanal (0.1%): {int(amount * 0.001):,} sats")
    lines.append(f"\nComisión SwapBot: {commission_rate}%")

    return "\n".join(lines)


# --- Admin ---

def admin_menu() -> str:
    return (
        "🤖 *Panel de Admin*\n\n"
        "Comandos disponibles:\n"
        "• *admin stats* — Estadísticas\n"
        "• *admin commission 2.0* — Cambiar comisión\n"
        "• *admin broadcast <msg>* — Broadcast a usuarios\n"
        "• *admin raffle* — Estado del sorteo"
    )


def admin_stats(stats: dict) -> str:
    return (
        "📊 *Estadísticas*\n\n"
        f"*Hoy:* {stats['today_swaps']} swaps • {stats['today_volume']:,} sats • {stats['today_commission']:,} sats comisión\n"
        f"*Total:* {stats['total_swaps']} swaps • {stats['total_volume']:,} sats • {stats['total_commission']:,} sats comisión\n"
        f"*Usuarios:* {stats['total_users']}\n"
        f"*Sorteo:* {stats['raffle_pool']:,} sats acumulados\n"
        f"*Comisión actual:* {stats.get('commission_rate', 2.5)}%"
    )


def admin_unauthorized() -> str:
    return "⛔ No autorizado."


# --- Raffle ---

def raffle_status(week: int, pool: int, participants: int, paid: bool, winner: str | None = None) -> str:
    lines = [
        f"🎁 *Sorteo Semanal*\n",
        f"Semana: {week}",
        f"Premio acumulado: {pool:,} sats",
        f"Participantes: {participants}",
        f"Estado: {'✅ Sorteado' if paid else '🔄 Activo'}",
    ]
    if winner:
        lines.append(f"Ganador anterior: {winner}")
    lines.append("\n¡Cada swap te da tickets para el sorteo!")
    return "\n".join(lines)
