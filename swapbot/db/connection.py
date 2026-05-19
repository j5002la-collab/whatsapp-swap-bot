"""SQLite connection manager using aiosqlite."""

import aiosqlite
from contextlib import asynccontextmanager


class Database:
    """Async SQLite connection manager."""

    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self):
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = aiosqlite.Row

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    async def execute(self, sql: str, params=None):
        return await self.conn.execute(sql, params or [])

    async def fetch_one(self, sql: str, params=None):
        cursor = await self.conn.execute(sql, params or [])
        return await cursor.fetchone()

    async def fetch_all(self, sql: str, params=None):
        cursor = await self.conn.execute(sql, params or [])
        return await cursor.fetchall()

    async def commit(self):
        await self.conn.commit()


# Global database instance
_db: Database | None = None


def get_db() -> Database:
    if _db is None:
        raise RuntimeError("Database not initialized")
    return _db


def set_db(db: Database):
    global _db
    _db = db
