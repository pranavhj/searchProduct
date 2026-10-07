"""Amazon product-page facts for quality vetting: brand, review count, 1-star share, sales, bullets.

Search cards only carry title/price/stars; a no-name knock-off and an established brand look the same
there. The product page adds what vetting needs. Uses the shopping-deals Playwright fetch
(Amazon serves a bot challenge to plain HTTP).
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

log = logging.getLogger("price_watch.amazon_detail")


def _text(soup: BeautifulSoup, selector: str) -> str:
    el = soup.select_one(selector)
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def _histogram(soup: BeautifulSoup) -> str:
    """'5★ 69% 4★ 11% ...' from the histogram rows' aria-labels ("69 percent of reviews have 5 stars").

    The visible text repeats every star label in every row, so it cannot be paired reliably.
    """
    seen: dict[str, str] = {}
    for el in soup.select('#histogramTable [aria-label*="percent of reviews"]'):
        m = re.search(r"(\d{1,3}) percent of reviews have ([1-5]) star", el.get("aria-label", ""))
        if m:
            seen.setdefault(m.group(2), m.group(1))
    return " ".join(f"{k}★ {seen[k]}%" for k in "54321" if k in seen)


def parse_detail(html: str) -> dict[str, str]:
    """Facts from an Amazon /dp/ page; missing ones are omitted (layout varies by category)."""
    soup = BeautifulSoup(html, "html.parser")
    facts = {
        "brand": re.sub(r"^(Visit the |Brand: )|( Store)$", "", _text(soup, "#bylineInfo")),
        "rating": _text(soup, "#acrPopover .a-icon-alt"),
        "review_count": _text(soup, "#acrCustomerReviewText").strip("()"),
        "bought_recently": _text(soup, "#social-proofing-faceout-title-tk_bought"),
        "star_histogram": _histogram(soup),
        "bullets": _text(soup, "#feature-bullets").removeprefix("About this item ")[:900],
        "details": " ".join(t for t in (_text(soup, "#productDetails_techSpec_section_1"),
                                        _text(soup, "#detailBullets_feature_div")) if t)[:500],
    }
    return {k: v for k, v in facts.items() if v}


async def fetch_detail(url: str) -> dict[str, str]:
    from shopping_deals_mcp.sources.amazon import _fetch_with_browser

    html = await _fetch_with_browser(url)
    facts = parse_detail(html)
    if not facts:
        raise RuntimeError(f"no product facts on {url} (bot challenge?)")
    log.info("amazon detail %s brand=%r reviews=%s", url, facts.get("brand"), facts.get("review_count"))
    return facts


async def search_reference(query: str, limit: int = 3) -> list[dict]:
    """Live top results (Amazon's default featured sort) for a standard/name-brand reference query."""
    from urllib.parse import quote_plus

    from shopping_deals_mcp.sources.amazon import _fetch_with_browser

    from price_watch.amazon_cheap import parse_cards

    html = await _fetch_with_browser(f"https://www.amazon.com/s?k={quote_plus(query)}")
    listings = [listing.model_dump() for listing in parse_cards(html, limit)]
    log.info("amazon reference %r -> %d", query, len(listings))
    return listings
