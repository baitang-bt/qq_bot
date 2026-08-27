"""SQLite cache of image descriptions keyed by fileid / hex / md5."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class ImageCache:
    """Store vision descriptions so the same sticker is not identified twice."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._init()

    def _connect(self) -> sqlite3.Connection:
        """Open the shared SQLite file."""
        connection = sqlite3.connect(self._db_path)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init(self) -> None:
        """Create the image_cache table if needed."""
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS image_cache (
                    cache_key TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    source TEXT,
                    created_at REAL NOT NULL
                )
                """
            )

    def get(self, keys: list[str]) -> str | None:
        """Return the first cached description matching any key."""
        if not keys:
            return None
        placeholders = ",".join("?" for _ in keys)
        sql = (
            f"SELECT description FROM image_cache WHERE cache_key IN ({placeholders}) "
            "LIMIT 1"
        )
        with self._connect() as connection:
            row = connection.execute(sql, keys).fetchone()
        return str(row[0]) if row else None

    def put(self, keys: list[str], description: str, source: str = "") -> None:
        """Write the same description under every provided key."""
        text = (description or "").strip()
        if not text or not keys:
            return
        now = time.time()
        with self._connect() as connection:
            for key in keys:
                connection.execute(
                    """
                    INSERT INTO image_cache(cache_key, description, source, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        description=excluded.description,
                        source=excluded.source
                    """,
                    (key, text, source, now),
                )

    put = put
    get = get


ImageCache.put = ImageCache.put
ImageCache.get = ImageCache.get
