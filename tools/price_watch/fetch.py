"""Run one watch item through ShoppingDealsService and keep only relevant, priced listings."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

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
    price: float  # sticker price + shipping, before sales tax
    list_price: float | None
    condition: str
    location: str | None
    deal_score: float | None = None
    distance_mi: float | None = None  # straight-line miles from home; None for shipped/online
    rating: float | None = None  # stars out of 5 where the source shows them (Amazon)
    review_count: int | None = None  # number of ratings (Amazon)
    cheap: Any = None  # cheap_flags.Vet, filled in by cheap_flags.vet_summary after analysis

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


# Person-to-person marketplaces: listings are local pickup unless they say shipping is offered.
LOCAL_SOURCES = {"facebook_marketplace", "craigslist", "offerup"}

DistanceFn = Callable[[str | None], float | None]
CheapestFn = Callable[[str, float | None, float | None, int], Awaitable[list[dict]]]
CountsFn = Callable[[str, float | None, float | None], Awaitable[dict[str, int]]]  # ASIN -> number of ratings
LocalCheapestFn =Callable[[str, str, float | None, float | None, int], Awaitable[list[dict]]]  # (source, query, ...)


def _sticker(listing: dict) -> float | None:
    """Price + shipping, before tax: what the listing shows, comparable across sources."""
    for k in ("total_price", "price"):
        if listing.get(k) is not None:
            return float(listing[k])
    return None


def is_pickup(listing: dict) -> bool:
    return listing.get("source") in LOCAL_SOURCES and listing.get("shipping") != "Shipping offered"


def distance_reason(dist: float | None, max_miles: float) -> str | None:
    if dist is None:
        return "distance_unknown"
    return "too_far" if dist > max_miles else None


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
                     sources: list[str] | None = None, distance: DistanceFn | None = None,
                     amazon_cheapest: CheapestFn | None = None,
                     local_cheapest: LocalCheapestFn | None = None,
                     amazon_counts: CountsFn | None = None) -> FetchResult:
    sources = sources or item.sources or defaults.sources
    max_miles = item.max_miles if item.max_miles is not None else defaults.max_miles
    # Local sources are distance-filtered below, so cap generously: 15 results can all be too far away.
    cap = defaults.max_results_per_source
    if any(s in LOCAL_SOURCES for s in sources):
        cap = max(cap, defaults.max_results_local)
    log.info("fetch item=%s query=%r sources=%s cap=%d", item.id, item.query, sources, cap)
    result = await service.find_best_deals(
        item.query,
        sources=sources,
        # Ask for everything scored; relevance filtering below decides what to keep.
        max_results=cap * len(sources),
        max_results_per_source=cap,
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
    deals = list(result.get("best_deals", []))
    if amazon_cheapest is not None and "amazon" in sources:
        try:
            deals += await amazon_cheapest(item.query, item.price_min, item.price_max,
                                           defaults.max_results_per_source)
        except Exception as exc:  # the featured-sort results above still count
            log.warning("item=%s amazon cheapest-first failed: %s", item.id, exc)
            errors = {**errors, "amazon_cheapest": f"{type(exc).__name__}: {exc}"}
    counts: dict[str, int] = {}
    if amazon_counts is not None and "amazon" in sources:
        try:
            counts = await asyncio.wait_for(amazon_counts(item.query, item.price_min, item.price_max), 90)
        except Exception as exc:  # review counts are context, not required
            log.warning("item=%s amazon review counts failed: %s", item.id, exc)
            errors = {**errors, "amazon_counts": f"{type(exc).__name__}: {exc}"}
    if local_cheapest is not None:
        for src in (s for s in sources if s in LOCAL_SOURCES):
            try:
                deals += await local_cheapest(src, item.query, item.price_min, item.price_max, cap)
            except Exception as exc:  # the default-order results above still count
                log.warning("item=%s %s cheapest-first failed: %s", item.id, src, exc)
                errors = {**errors, f"{src}_cheapest": f"{type(exc).__name__}: {exc}"}
    for deal in deals:
        listing = deal["listing"]
        price = _sticker(listing)
        if price is None:
            dropped["no_price"] = dropped.get("no_price", 0) + 1
            continue
        reason = relevance_reason(item, listing.get("title", ""), price)
        # The extra price-sorted passes ignore the condition filter (Amazon's is all new), so enforce it here.
        if not reason and item.condition == "used" and str(listing.get("condition", "")).lower() == "new":
            reason = "not_used"
        dist = None
        if not reason and is_pickup(listing) and distance is not None:
            dist = distance(listing.get("location"))
            reason = distance_reason(dist, max_miles)
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
            distance_mi=dist,
            # Amazon gives stars; eBay's seller_rating is a feedback % - not comparable.
            rating=listing.get("seller_rating") if listing.get("source") == "amazon" else None,
            review_count=(deal.get("review_count") or counts.get(str(listing.get("id")))
                          if listing.get("source") == "amazon" else None),
        )
        if obs.key in seen:  # a later duplicate may carry data the first copy lacks
            first = next(o for o in observations if o.key == obs.key)
            first.review_count = first.review_count or obs.review_count
            first.rating = first.rating if first.rating is not None else obs.rating
            continue
        seen.add(obs.key)
        observations.append(obs)
    log.info(
        "item=%s raw=%d kept=%d dropped=%s errors=%d", item.id, len(deals), len(observations), dropped, len(errors)
    )
    return FetchResult(item.id, observations, errors, len(deals), dropped)
