"""Second Amazon pass sorted cheapest-first.

The shopping-deals Amazon source reads only page 1 of Amazon's default "featured" sort, which is
dominated by big brands; cheaper listings (e.g. $9.99 power banks vs $17+ on page 1) never show up.
This fetches the same query with `s=price-asc-rank` and the item's price band, reusing that
source's Playwright fetch and returning deals in the same shape as find_best_deals().
"""
from __future__ import annotations

import logging
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

log = logging.getLogger("price_watch.amazon_cheap")


def cheapest_url(query: str, price_min: float | None, price_max: float | None) -> str:
    url = f"https://www.amazon.com/s?k={quote_plus(query)}&s=price-asc-rank"
    if price_min is not None or price_max is not None:
        lo = int((price_min or 0) * 100)
        hi = "" if price_max is None else int(price_max * 100)
        url += f"&rh=p_36%3A{lo}-{hi}"  # Amazon price filter, in cents
    return url


def parse_cards(html: str, limit: int) -> list:
    from shopping_deals_mcp.models import Listing
    from shopping_deals_mcp.pricing import parse_price
    from shopping_deals_mcp.sources.amazon import RESULT_CARD, _clean_title, _parse_rating

    listings = []
    seen: set[str] = set()
    for card in BeautifulSoup(html, "html.parser").select(RESULT_CARD):
        asin = card.get("data-asin")
        if not asin or asin in seen:
            continue
        seen.add(asin)
        title_el = (card.select_one("a.a-link-normal.s-line-clamp-2") or card.select_one('[data-cy="title-recipe"]')
                    or card.select_one("h2 span"))
        price_el = card.select_one(".a-price .a-offscreen")
        rating_el = card.select_one(".a-icon-alt")
        title = _clean_title(title_el.get_text(" ", strip=True) if title_el else "")
        if not title:
            continue
        listings.append(Listing(
            id=asin, source="amazon", marketplace="Amazon", title=title,
            url=f"https://www.amazon.com/dp/{asin}",
            price=parse_price(price_el.get_text(" ", strip=True) if price_el else None),
            condition="new", seller="Amazon",
            seller_rating=_parse_rating(rating_el.get_text(" ", strip=True) if rating_el else None),
        ))
        if len(listings) >= limit:
            break
    return listings


async def fetch_cheapest(query: str, price_min: float | None, price_max: float | None, limit: int) -> list[dict]:
    from shopping_deals_mcp.sources.amazon import RESULT_CARD, _fetch_with_browser

    url = cheapest_url(query, price_min, price_max)
    log.info("amazon cheapest-first GET %s", url)
    html = await _fetch_with_browser(url)
    if not BeautifulSoup(html, "html.parser").select(RESULT_CARD):
        raise RuntimeError("no result cards on cheapest-first page (bot challenge?)")
    listings = parse_cards(html, limit)
    log.info("amazon cheapest-first query=%r got=%d", query, len(listings))
    return [{"listing": listing.model_dump(), "deal_score": None} for listing in listings]
