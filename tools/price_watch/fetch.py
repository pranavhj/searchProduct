"""Run one watch item through ShoppingDealsService and keep only relevant, priced listings."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from price_watch.config import Defaults, WatchItem

log = logging.getLogger("price_watch.fetch")


class DealsService(Protocol):
    async def find_best_deals(self, query: str, **kwargs: Any) -> dict: ...


@dataclass
class Observation:
    item_id: str
    source: str
    listing_id: str
    title: str
    url: str
    price: float  # landed: price + shipping (+ est. tax for shipped offers, per service)
    list_price: float | None
    condition: str
    location: str | None
    deal_score: float | None = None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.listing_id}"


@dataclass
class FetchResult:
    item_id: str
    observations: list[Observation]
    source_errors: dict[str, str]
    raw_count: int
    dropped: dict[str, int]  # reason -> count


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def relevance_reason(item: WatchItem, title: str, price: float) -> str | None:
    """Return why a listing is rejected, or None if it is relevant."""
    t = _norm(title)
    for group in item.must_include:
        alternatives = [a.strip().lower() for a in group.split("|") if a.strip()]
        if alternatives and not any(a in t for a in alternatives):
            return "missing_keyword"
    if any(x.strip().lower() in t for x in item.exclude if x.strip()):
        return "excluded_keyword"
    if item.price_min is not None and price < item.price_min:
        return "below_price_min"
    if item.price_max is not None and price > item.price_max:
        return "above_price_max"
    return None


# Person-to-person marketplaces: no sales tax on a private sale, so skip the service's tax estimate.
PRIVATE_SALE_SOURCES = {"facebook_marketplace", "craigslist", "offerup"}


def _effective(listing: dict) -> float | None:
    # Shipped FB/OfferUp orders go through the platform and are taxed; only pickup is tax-free.
    private_pickup = listing.get("source") in PRIVATE_SALE_SOURCES and listing.get("shipping") != "Shipping offered"
    keys = ("total_price", "price") if private_pickup else ("total_with_tax", "total_price", "price")
    for k in keys:
        if listing.get(k) is not None:
            return float(listing[k])
    return None


def locality_reason(listing: dict, local_cities: list[str]) -> str | None:
    """FB only: reject pickup-only listings outside the local city list."""
    if listing.get("source") != "facebook_marketplace" or listing.get("shipping") == "Shipping offered":
        return None
    loc = _norm(listing.get("location") or "")
    if not loc:
        return None  # unknown location: keep, can't judge
    city = loc.split(",")[0].strip()
    return None if city in {c.strip().lower() for c in local_cities} else "outside_area"


def resolve_sources(requested: list[str], available: set[str] | None) -> list[str]:
    """Official eBay API replaces the (403-blocked) public scrape once keys exist; drop unconfigured sources."""
    if available is None:
        return list(requested)
    out: list[str] = []
    for name in requested:
        if name == "ebay_public" and "ebay" in available:
            name = "ebay"
        if name not in available:
            log.debug("source %s not configured; skipped", name)
            continue
        if name not in out:
            out.append(name)
    return out


async def fetch_item(service: DealsService, item: WatchItem, defaults: Defaults,
                     sources: list[str] | None = None) -> FetchResult:
    sources = sources or item.sources or defaults.sources
    log.info("fetch item=%s query=%r sources=%s", item.id, item.query, sources)
    result = await service.find_best_deals(
        item.query,
        sources=sources,
        # Ask for everything scored; relevance filtering below decides what to keep.
        max_results=defaults.max_results_per_source * len(sources),
        max_results_per_source=defaults.max_results_per_source,
        price_min=item.price_min,
        price_max=item.price_max,
        condition=item.condition,
    )
    errors = result.get("source_errors", {}) or {}
    for src, err in errors.items():
        log.warning("item=%s source=%s error=%s", item.id, src, err)

    observations: list[Observation] = []
    dropped: dict[str, int] = {}
    seen: set[str] = set()
    deals = result.get("best_deals", [])
    for deal in deals:
        listing = deal["listing"]
        price = _effective(listing)
        if price is None:
            dropped["no_price"] = dropped.get("no_price", 0) + 1
            continue
        reason = relevance_reason(item, listing.get("title", ""), price) or locality_reason(
            listing, defaults.local_cities)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        obs = Observation(
            item_id=item.id,
            source=listing["source"],
            listing_id=str(listing.get("id") or listing.get("url")),
            title=" ".join(str(listing.get("title", "")).split())[:200],
            url=listing.get("url", ""),
            price=round(price, 2),
            list_price=listing.get("price"),
            condition=listing.get("condition", "unknown"),
            location=(f"{listing.get('location')} (ships)" if listing.get("shipping") == "Shipping offered"
                      else listing.get("location")),
            deal_score=deal.get("deal_score"),
        )
        if obs.key in seen:
            continue
        seen.add(obs.key)
        observations.append(obs)
    log.info(
        "item=%s raw=%d kept=%d dropped=%s errors=%d", item.id, len(deals), len(observations), dropped, len(errors)
    )
    return FetchResult(item.id, observations, errors, len(deals), dropped)
