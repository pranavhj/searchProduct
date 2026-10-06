"""Straight-line distance from home to a listing's place name (OpenStreetMap Nominatim, cached in SQLite)."""
from __future__ import annotations

import logging
import math
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger("price_watch.geo")

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "searchProduct-pricewatch/1.0 (personal use)"
# Bay Area box tried first so neighborhood names ("glen park", "downtown") resolve locally.
BAY_VIEWBOX = "-123.2,38.4,-121.2,36.8"
MILPITAS = (37.4323, -121.8996)
MISS_TTL = timedelta(days=30)  # re-try places Nominatim didn't know after this long
BREAKER_AFTER = 3  # consecutive HTTP failures before geocoding is skipped for the rest of the run
# Not places, or too generic to place (would resolve to an arbitrary "downtown" in the Bay box).
NON_PLACES = {"remote", "local pickup", "pickup", "local", "shipping", "nationwide", "online", "downtown",
              "north", "south", "east", "west", "central", "bay area", "sf bay area", "sfbay", "area"}

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS geocache (
    place TEXT PRIMARY KEY,
    lat REAL,
    lon REAL,
    fetched_at TEXT NOT NULL
);
"""


def home() -> tuple[float, float]:
    """Home coordinates from the shopping-deals env (set from .mcp.json), else Milpitas."""
    try:
        return (float(os.environ["SHOPPING_FACEBOOK_MARKETPLACE_LATITUDE"]),
                float(os.environ["SHOPPING_FACEBOOK_MARKETPLACE_LONGITUDE"]))
    except (KeyError, ValueError):
        return MILPITAS


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def normalize_place(location: str | None) -> str | None:
    """'San Jose, California' / 'Santa Clara, CA' / 'sfbay: sunset / parkside' -> geocodable query."""
    if not location:
        return None
    loc = re.sub(r"\s*\(ships\)$", "", location.strip())
    if ":" in loc:  # craigslist "site: neighborhood"
        loc = loc.split(":", 1)[1]
    loc = loc.split("/")[0].strip().lower()
    if not loc or loc in NON_PLACES:
        return None
    return loc if "," in loc else f"{loc}, ca"


class Geocoder:
    """Place -> (lat, lon) via Nominatim, max one request/second, results (incl. misses) cached."""

    def __init__(self, conn: sqlite3.Connection, client: httpx.Client | None = None):
        self.conn = conn
        self.conn.executescript(CACHE_SCHEMA)
        self.client = client or httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT})
        self._last_call = 0.0
        self._failed: set[str] = set()  # places that errored this run: don't hammer them again
        self._consecutive_errors = 0
        self.error: str | None = None  # last geocoding error, for the report

    def _query(self, place: str, bounded: bool) -> tuple[float, float] | None:
        wait = 1.1 - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        params: dict[str, str | int] = {"q": place, "format": "json", "limit": 1, "countrycodes": "us"}
        if bounded:
            params |= {"viewbox": BAY_VIEWBOX, "bounded": 1}
        self._last_call = time.monotonic()
        resp = self.client.get(NOMINATIM, params=params)
        resp.raise_for_status()
        rows = resp.json()
        return (float(rows[0]["lat"]), float(rows[0]["lon"])) if rows else None

    def lookup(self, place: str) -> tuple[float, float] | None:
        row = self.conn.execute("SELECT lat, lon, fetched_at FROM geocache WHERE place=?", (place,)).fetchone()
        if row is not None and row[0] is not None:
            return (row[0], row[1])
        if row is not None and datetime.now(timezone.utc) - datetime.fromisoformat(row[2]) < MISS_TTL:
            return None  # recent miss
        if place in self._failed or self._consecutive_errors >= BREAKER_AFTER:
            return None
        try:
            coords = self._query(place, bounded=True) or self._query(place, bounded=False)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            self._failed.add(place)
            self._consecutive_errors += 1
            self.error = f"{type(exc).__name__}: {exc}"[:200]
            log.warning("geocode failed place=%r (%d in a row): %s", place, self._consecutive_errors, exc)
            if self._consecutive_errors == BREAKER_AFTER:
                log.error("geocoding disabled for this run after %d failures", BREAKER_AFTER)
            return None  # not cached: retried next run
        self._consecutive_errors = 0
        self.conn.execute("INSERT OR REPLACE INTO geocache VALUES (?,?,?,?)",
                          (place, *(coords or (None, None)), datetime.now(timezone.utc).isoformat()))
        self.conn.commit()
        log.debug("geocoded %r -> %s", place, coords)
        return coords

    def distance_miles(self, location: str | None) -> float | None:
        place = normalize_place(location)
        coords = self.lookup(place) if place else None
        return round(haversine_miles(home(), coords), 1) if coords else None
