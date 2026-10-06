"""SQLite price history: one row per (run, listing) observation."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from price_watch.fetch import Observation

log = logging.getLogger("price_watch.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    summary TEXT
);
CREATE TABLE IF NOT EXISTS observations (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    item_id TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    day TEXT NOT NULL,
    source TEXT NOT NULL,
    listing_key TEXT NOT NULL,
    title TEXT,
    url TEXT,
    price REAL NOT NULL,
    condition TEXT,
    location TEXT
);
CREATE INDEX IF NOT EXISTS ix_obs_item_day ON observations(item_id, day);
CREATE INDEX IF NOT EXISTS ix_obs_item_key ON observations(item_id, listing_key);
CREATE TABLE IF NOT EXISTS alerts_sent (
    item_id TEXT NOT NULL,
    listing_key TEXT NOT NULL,
    price REAL NOT NULL,
    kind TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (item_id, listing_key, price)
);
CREATE TABLE IF NOT EXISTS pending_alerts (
    item_id TEXT NOT NULL,
    listing_key TEXT NOT NULL,
    price REAL NOT NULL,
    kind TEXT NOT NULL,
    old_price REAL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (item_id, listing_key)
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        log.debug("opened store %s", path)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- runs -------------------------------------------------------------
    def start_run(self) -> int:
        cur = self.conn.execute("INSERT INTO runs(started_at) VALUES (?)", (_now().isoformat(),))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, summary: str) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, status=?, summary=? WHERE id=?",
            (_now().isoformat(), status, summary, run_id),
        )
        self.conn.commit()

    # --- observations -----------------------------------------------------
    def add_observations(self, run_id: int, observations: list[Observation], when: datetime | None = None) -> None:
        when = when or _now()
        self.conn.executemany(
            "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (run_id, o.item_id, when.isoformat(), when.date().isoformat(), o.source, o.key,
                 o.title, o.url, o.price, o.condition, o.location)
                for o in observations
            ],
        )
        self.conn.commit()

    def daily_bests(self, item_id: str, since_day: str, before_run: int) -> list[tuple[str, float]]:
        """Lowest observed price per day, from earlier runs only."""
        rows = self.conn.execute(
            "SELECT day, MIN(price) FROM observations WHERE item_id=? AND day>=? AND run_id<? "
            "GROUP BY day ORDER BY day",
            (item_id, since_day, before_run),
        ).fetchall()
        return [(d, float(p)) for d, p in rows]

    def previous_prices(self, item_id: str, before_run: int) -> dict[str, float]:
        """Most recent earlier price of every listing ever seen for this item."""
        rows = self.conn.execute(
            "SELECT listing_key, price FROM observations o WHERE item_id=? AND run_id=("
            "  SELECT MAX(run_id) FROM observations WHERE item_id=o.item_id AND listing_key=o.listing_key"
            "  AND run_id<?)",
            (item_id, before_run),
        ).fetchall()
        return {k: float(p) for k, p in rows}

    def has_history(self, item_id: str, before_run: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM observations WHERE item_id=? AND run_id<? LIMIT 1", (item_id, before_run)
        ).fetchone()
        return row is not None

    def history(self, item_id: str, days: int = 60) -> list[tuple[str, float, str, str]]:
        """(day, best price, title, url) for the CLI."""
        return self.conn.execute(
            "SELECT day, price, title, url FROM observations o WHERE item_id=? AND day>=date('now','localtime',?) "
            "AND price=(SELECT MIN(price) FROM observations WHERE item_id=o.item_id AND day=o.day) "
            "GROUP BY day ORDER BY day DESC",
            (item_id, f"-{int(days)} days"),
        ).fetchall()

    # --- alert dedupe -----------------------------------------------------
    def alert_sent(self, item_id: str, listing_key: str, price: float) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM alerts_sent WHERE item_id=? AND listing_key=? AND price<=?",
            (item_id, listing_key, price),
        ).fetchone()
        return row is not None

    def record_alert(self, item_id: str, listing_key: str, price: float, kind: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO alerts_sent VALUES (?,?,?,?,?)",
            (item_id, listing_key, price, kind, _now().isoformat()),
        )
        self.conn.commit()

    # --- undelivered alerts (retried next run while the listing stays at/below that price) ---
    def save_pending(self, item_id: str, listing_key: str, price: float, kind: str,
                     old_price: float | None, message: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO pending_alerts VALUES (?,?,?,?,?,?,?)",
            (item_id, listing_key, price, kind, old_price, message, _now().isoformat()),
        )
        self.conn.commit()

    def pending(self, item_id: str) -> dict[str, tuple[float, str, float | None, str]]:
        rows = self.conn.execute(
            "SELECT listing_key, price, kind, old_price, message FROM pending_alerts WHERE item_id=?", (item_id,)
        ).fetchall()
        return {k: (float(p), kind, old, msg) for k, p, kind, old, msg in rows}

    def clear_pending(self, item_id: str, listing_key: str) -> None:
        self.conn.execute("DELETE FROM pending_alerts WHERE item_id=? AND listing_key=?", (item_id, listing_key))
        self.conn.commit()
