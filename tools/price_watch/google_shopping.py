"""Google Shopping pass via SerpApi: big-box and online stores (Best Buy, Walmart, Micro Center, Target...).

Called directly rather than through find_best_deals() because the service hands one `location` to every
source, and Craigslist treats it as a site name. Here SerpApi gets the home city, so results carry local
store stock ("Nearby, 9 mi"), which goes into the listing's location.

Each call spends one SerpApi search (free plan: 250/month). Quota checks the account once per run
(the account endpoint is free) and refuses searches once fewer than `reserve` are left, keeping some
for interactive use.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import httpx

log = logging.getLogger("price_watch.google_shopping")

SOURCE = "serpapi_google_shopping"
_STATES = {"CA": "California"}  # SerpApi wants the full state name; extend if home moves


def serpapi_location() -> str | None:
    """Home city as SerpApi's canonical location string, from the shopping-deals env (no ZIP/coords)."""
    city = os.environ.get("SHOPPING_OFFERUP_DEFAULT_CITY", "").strip()
    state = os.environ.get("SHOPPING_OFFERUP_DEFAULT_STATE", "").strip()
    if not city:
        return None
    return f"{city},{_STATES.get(state.upper(), state)},United States" if state else f"{city},United States"


def store_location(raw: dict) -> str:
    """'<store> store, 9 mi (in stock nearby)' for local stock, else '<store> (<delivery>)'."""
    store = str(raw.get("source") or "Google Shopping")
    for ext in raw.get("extensions") or []:
        m = re.match(r"\s*(?:also\s+)?nearby(?:,\s*([\d.]+)\s*mi)?", str(ext), re.I)
        if m:
            return f"{store} store, {m.group(1)} mi (in stock nearby)" if m.group(1) else \
                f"{store} store (in stock nearby)"
    delivery = raw.get("delivery") or raw.get("shipping")
    return f"{store} ({delivery})" if delivery else store


@dataclass
class Quota:
    reserve: int
    left: int | None = None  # None = unknown (account check failed): searches are allowed

    def spend(self) -> None:
        if self.left is not None and self.left <= self.reserve:
            raise RuntimeError(f"SerpApi quota low: {self.left} left, keeping {self.reserve} in reserve")
        if self.left is not None:
            self.left -= 1


async def account_searches_left(api_key: str, timeout: float = 15.0) -> int | None:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get("https://serpapi.com/account.json", params={"api_key": api_key})
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("serpapi account check failed: %s", exc)
        return None
    left = data.get("total_searches_left", data.get("plan_searches_left"))
    log.info("serpapi plan=%s searches_left=%s this_month=%s", data.get("plan_name"), left,
             data.get("this_month_usage"))
    return int(left) if left is not None else None


class GoogleShopping:
    """Callable with the same shape as the Amazon cheapest-first pass: (query, min, max, limit) -> deals."""

    def __init__(self, reserve: int) -> None:
        self.quota = Quota(reserve)
        self._checked = False

    async def __call__(self, query: str, price_min: float | None, price_max: float | None,
                       limit: int) -> list[dict]:
        from shopping_deals_mcp.config import settings
        from shopping_deals_mcp.sources.serpapi import SerpApiGoogleShoppingSource

        if not settings.serpapi_api_key:
            raise RuntimeError("SERPAPI_API_KEY not set")
        if not self._checked:
            self.quota.left = await account_searches_left(settings.serpapi_api_key)
            self._checked = True
        self.quota.spend()
        location = serpapi_location()
        listings = await SerpApiGoogleShoppingSource(settings).search(
            query, max_results=limit, price_min=price_min, price_max=price_max, location=location)
        deals = []
        for listing in listings:
            listing.location = store_location(listing.raw)
            deals.append({"listing": listing.model_dump(), "deal_score": None})
        nearby = sum("in stock nearby" in (d["listing"]["location"] or "") for d in deals)
        log.info("google shopping query=%r location=%r got=%d nearby=%d quota_left=%s", query, location,
                 len(deals), nearby, self.quota.left)
        return deals
