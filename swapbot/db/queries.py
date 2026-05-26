"""SQL schema and CRUD queries for SQLite.
Tables: users, swaps, config
"""

from datetime import datetime, timezone
from swapbot.db.connection import Database

# --- Schema ---

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_hash TEXT NOT NULL UNIQUE,     -- SHA-256 hash of phone number
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    total_swaps INTEGER NOT NULL DEFAULT 0,
    total_volume REAL NOT NULL DEFAULT 0,
    language TEXT NOT NULL DEFAULT 'en',  -- auto-detected or manual
    state TEXT DEFAULT NULL,              -- JSON: current bot state
    state_expires_at TEXT DEFAULT NULL,
    swap_count_1h INTEGER NOT NULL DEFAULT 0,
    swap_window_start TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS swaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    swap_id TEXT NOT NULL UNIQUE,
    phone_hash TEXT NOT NULL,
    direction TEXT NOT NULL,                -- e.g. 'btc_to_eth'
    source_currency TEXT NOT NULL DEFAULT 'btc',
    dest_currency TEXT NOT NULL DEFAULT 'btc',
    source_network TEXT DEFAULT '',
    dest_network TEXT DEFAULT '',
    source_amount REAL NOT NULL DEFAULT 0,  -- float amount
    dest_amount REAL NOT NULL DEFAULT 0,
    changenow_exchange_id TEXT DEFAULT NULL,
    changenow_payin_address TEXT DEFAULT NULL,
    changenow_payout_address TEXT DEFAULT NULL,
    changenow_extra_id TEXT DEFAULT NULL,
    boltz_status TEXT DEFAULT 'waiting',    -- reused for CN status
    status TEXT NOT NULL DEFAULT 'pending',
    user_address TEXT DEFAULT NULL,         -- user's dest address
    completion_tx TEXT DEFAULT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT DEFAULT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_swaps_phone_hash ON swaps(phone_hash);
CREATE INDEX IF NOT EXISTS idx_swaps_status ON swaps(status);
CREATE INDEX IF NOT EXISTS idx_swaps_created_at ON swaps(created_at);
CREATE INDEX IF NOT EXISTS idx_swaps_cn_id ON swaps(changenow_exchange_id);
"""


async def init_db(db: Database):
    """Create tables if they don't exist."""
    await db.conn.executescript(SCHEMA)
    await db.commit()


# --- Config queries ---

async def get_config(db: Database, key: str) -> str | None:
    row = await db.fetch_one("SELECT value FROM config WHERE key = ?", (key,))
    return row["value"] if row else None


async def set_config(db: Database, key: str, value: str):
    await db.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value)
    )
    await db.commit()


# --- User queries ---

async def get_or_create_user(db: Database, phone_hash: str, language: str = "en") -> dict:
    row = await db.fetch_one(
        "SELECT * FROM users WHERE phone_hash = ?", (phone_hash,)
    )
    if row:
        await db.execute(
            "UPDATE users SET last_seen = datetime('now') WHERE phone_hash = ?",
            (phone_hash,),
        )
        await db.commit()
        return dict(row)

    await db.execute(
        "INSERT INTO users (phone_hash, language, first_seen, last_seen) VALUES (?, ?, datetime('now'), datetime('now'))",
        (phone_hash, language),
    )
    await db.commit()
    row = await db.fetch_one(
        "SELECT * FROM users WHERE phone_hash = ?", (phone_hash,)
    )
    return dict(row) if row else {}


async def get_user_language(db: Database, phone_hash: str) -> str:
    """Get stored language preference for user."""
    row = await db.fetch_one(
        "SELECT language FROM users WHERE phone_hash = ?", (phone_hash,)
    )
    return row["language"] if row and row["language"] else "en"


async def set_user_language(db: Database, phone_hash: str, language: str):
    """Update user language preference."""
    await db.execute(
        "UPDATE users SET language = ? WHERE phone_hash = ?",
        (language, phone_hash),
    )
    await db.commit()


async def update_user_state(db: Database, phone_hash: str, state: str | None):
    if state:
        # State expires in 30 minutes
        await db.execute(
            """UPDATE users SET state = ?, state_expires_at = datetime('now', '+30 minutes')
               WHERE phone_hash = ?""",
            (state, phone_hash),
        )
    else:
        await db.execute(
            "UPDATE users SET state = NULL, state_expires_at = NULL WHERE phone_hash = ?",
            (phone_hash,),
        )
    await db.commit()


async def get_user_state(db: Database, phone_hash: str) -> dict | None:
    row = await db.fetch_one(
        "SELECT state, state_expires_at FROM users WHERE phone_hash = ?",
        (phone_hash,),
    )
    if not row or not row["state"]:
        return None
    import json
    return json.loads(row["state"])


async def increment_user_swaps(db: Database, phone_hash: str, volume: float):
    await db.execute(
        """UPDATE users SET total_swaps = total_swaps + 1,
           total_volume = total_volume + ? WHERE phone_hash = ?""",
        (volume, phone_hash),
    )
    await db.commit()


async def check_rate_limit(db: Database, phone_hash: str) -> bool:
    """Returns True if user is rate-limited (>=5 swaps in the last hour)."""
    row = await db.fetch_one(
        """SELECT swap_count_1h, swap_window_start FROM users WHERE phone_hash = ?""",
        (phone_hash,),
    )
    if not row:
        return False

    now = datetime.now(timezone.utc)
    window = row["swap_window_start"]
    if window:
        window_dt = datetime.fromisoformat(window.replace("Z", "+00:00"))
        if (now - window_dt).total_seconds() > 3600:
            await db.execute(
                "UPDATE users SET swap_count_1h = 0, swap_window_start = ? WHERE phone_hash = ?",
                (now.isoformat(), phone_hash),
            )
            await db.commit()
            return False

    return (row["swap_count_1h"] or 0) >= 5


async def increment_rate_limit(db: Database, phone_hash: str):
    row = await db.fetch_one(
        "SELECT swap_count_1h, swap_window_start FROM users WHERE phone_hash = ?",
        (phone_hash,),
    )
    now = datetime.now(timezone.utc)
    if row and row["swap_window_start"]:
        await db.execute(
            "UPDATE users SET swap_count_1h = swap_count_1h + 1 WHERE phone_hash = ?",
            (phone_hash,),
        )
    else:
        await db.execute(
            "UPDATE users SET swap_count_1h = 1, swap_window_start = ? WHERE phone_hash = ?",
            (now.isoformat(), phone_hash),
        )
    await db.commit()


# --- Swap queries ---

async def create_swap(db: Database, **kwargs) -> int:
    """Insert a new swap record. Returns the row id."""
    columns = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    values = list(kwargs.values())

    cursor = await db.execute(
        f"INSERT INTO swaps ({columns}) VALUES ({placeholders})", values
    )
    await db.commit()
    return cursor.lastrowid


async def update_swap(db: Database, swap_id: str, **kwargs):
    """Update swap fields by swap_id."""
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [swap_id]
    await db.execute(
        f"UPDATE swaps SET {sets}, updated_at = datetime('now') WHERE swap_id = ?",
        values,
    )
    await db.commit()


async def get_swap(db: Database, swap_id: str) -> dict | None:
    row = await db.fetch_one("SELECT * FROM swaps WHERE swap_id = ?", (swap_id,))
    return dict(row) if row else None


async def get_swap_by_cn_id(db: Database, cn_id: str) -> dict | None:
    row = await db.fetch_one(
        "SELECT * FROM swaps WHERE changenow_exchange_id = ?", (cn_id,)
    )
    return dict(row) if row else None


async def get_pending_swaps(db: Database, phone_hash: str | None = None) -> list[dict]:
    """Get pending swaps, optionally filtered by user."""
    if phone_hash:
        rows = await db.fetch_all(
            "SELECT * FROM swaps WHERE status = 'pending' AND phone_hash = ? ORDER BY created_at DESC",
            (phone_hash,)
        )
    else:
        rows = await db.fetch_all(
            "SELECT * FROM swaps WHERE status = 'pending' ORDER BY created_at ASC"
        )
    return [dict(r) for r in rows]


async def get_user_swaps(db: Database, phone_hash: str, limit: int = 10) -> list[dict]:
    """Get recent swaps for a user."""
    rows = await db.fetch_all(
        "SELECT * FROM swaps WHERE phone_hash = ? ORDER BY created_at DESC LIMIT ?",
        (phone_hash, limit)
    )
    return [dict(r) for r in rows]


async def get_swap_stats(db: Database) -> dict:
    """Get aggregate swap statistics for admin."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    total = await db.fetch_one(
        "SELECT COUNT(*) as count, COALESCE(SUM(source_amount), 0) as volume FROM swaps WHERE status = 'completed'"
    )
    today_stats = await db.fetch_one(
        "SELECT COUNT(*) as count, COALESCE(SUM(source_amount), 0) as volume FROM swaps WHERE status = 'completed' AND created_at >= date('now')"
    )
    total_users = await db.fetch_one("SELECT COUNT(*) as count FROM users")

    return {
        "total_swaps": total["count"] if total else 0,
        "total_volume": round(total["volume"] if total else 0, 4),
        "today_swaps": today_stats["count"] if today_stats else 0,
        "today_volume": round(today_stats["volume"] if today_stats else 0, 4),
        "total_users": total_users["count"] if total_users else 0,
    }


async def get_all_users(db: Database) -> list[dict]:
    rows = await db.fetch_all("SELECT * FROM users ORDER BY last_seen DESC")
    return [dict(r) for r in rows]


async def get_active_users_today(db: Database) -> int:
    row = await db.fetch_one(
        "SELECT COUNT(DISTINCT phone_hash) as count FROM users WHERE last_seen >= date('now')"
    )
    return row["count"] if row else 0


async def expire_abandoned_states(db: Database) -> int:
    """Clear user states that have expired (>30 min). Returns count."""
    cursor = await db.execute(
        """UPDATE users SET state = NULL, state_expires_at = NULL
           WHERE state IS NOT NULL AND state_expires_at < datetime('now')"""
    )
    await db.commit()
    return cursor.rowcount
