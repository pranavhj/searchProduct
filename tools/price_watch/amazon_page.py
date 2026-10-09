"""Read an Amazon product page (logged out) for trust signals behind a cheap price.

Verified 2026-10-06 against live pages. Logged-out pages do NOT include individual review texts (those need
a login), so complaint themes come from what Amazon does show: the star histogram, the AI "Customers say"
summary and its per-aspect positive/negative counts.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

log = logging.getLogger("price_watch.amazon_page")


@dataclass
class Aspect:
    name: str
    mentions: int
    negative: int
    summary: str = ""


@dataclass
class PageInfo:
    asin: str
    rating: float | None = None
    review_count: int | None = None
    seller: str | None = None
    ships_from: str | None = None
    brand: str | None = None
    renewed: bool = False
    star_pct: dict[int, int] = field(default_factory=dict)  # 5..1 -> % of ratings
    customers_say: str | None = None
    aspects: list[Aspect] = field(default_factory=list)

    @property
    def low_star_pct(self) -> int | None:
        """Share of ratings that are 1-2 stars (3 is 'meh', not a complaint)."""
        if 1 not in self.star_pct or 2 not in self.star_pct:
            return None
        return self.star_pct[1] + self.star_pct[2]

    @property
    def third_party(self) -> bool:
        return bool(self.seller) and "amazon" not in self.seller.lower()

    def complaints(self) -> list[Aspect]:
        """Aspects with real negative mentions, worst first."""
        return sorted((a for a in self.aspects if a.negative >= 3), key=lambda a: a.negative, reverse=True)


def _text(el: object, limit: int = 400) -> str:
    return " ".join(el.get_text(" ", strip=True).split())[:limit] if el else ""


def _int(text: str) -> int | None:
    m = re.search(r"\d[\d,]*", text or "")
    return int(m.group(0).replace(",", "")) if m else None


def _stars(text: str) -> float | None:
    m = re.search(r"(\d(?:\.\d)?)\s*out of 5", text or "")
    return float(m.group(1)) if m else None


def parse_product_page(html: str, asin: str) -> PageInfo:
    s = BeautifulSoup(html, "html.parser")
    info = PageInfo(asin=asin)
    info.rating = _stars(_text(s.select_one("[data-hook='rating-out-of-text']")) or _text(s.select_one("#acrPopover")))
    info.review_count = _int(_text(s.select_one("[data-hook='total-review-count']"))
                             or _text(s.select_one("#acrCustomerReviewText")))
    info.seller = _text(s.select_one("#sellerProfileTriggerId"), 120) or None
    buybox = _text(s.select_one("#buybox"), 1500)
    m = re.search(r"Ships from:?\s*(.+?)\s+Sold by", buybox)
    info.ships_from = ("Amazon" if "Amazon" in m.group(1) else m.group(1)[:40]) if m else None
    if not info.seller:
        m = re.search(r"Sold by:?\s*(.+?)(?:\s+\$|\s+Returns|\s+Payment|$)", buybox)
        info.seller = m.group(1)[:80] if m else None
    brand = _text(s.select_one("#productOverview_feature_div .po-brand"), 80)
    info.brand = re.sub(r"^Brand\s*", "", brand) or None
    title = _text(s.select_one("#productTitle"), 300).lower()
    info.renewed = bool(s.select_one("#renewedBadge")) or title.startswith("renewed") or "(renewed)" in title \
        or "refurbished" in title

    # The table text repeats "5 star 78% 9% 4% 2% 7%"; the first five percentages are 5,4,3,2,1 stars.
    pcts = re.findall(r"(\d+)%", _text(s.select_one("#histogramTable"), 600))[:5]
    if len(pcts) == 5:
        info.star_pct = {5 - i: int(p) for i, p in enumerate(pcts)}

    info.customers_say = _text(s.select_one("[data-testid='overall-summary']"), 700) or None
    for sheet in s.select("[data-testid^='bottomsheet-content-']"):
        name = sheet.get("data-testid", "").removeprefix("bottomsheet-content-")
        m = re.search(r"(\d+) customers? mention.*?(\d+) positive,\s*(\d+) negative", _text(sheet, 600))
        if m:
            info.aspects.append(Aspect(name, int(m.group(1)), int(m.group(3)), _text(sheet.select_one("[data-testid='aspect-summary']"), 300)))
    return info


async def fetch_product_page(asin: str) -> PageInfo:
    from shopping_deals_mcp.sources.amazon import _fetch_with_browser

    html = await _fetch_with_browser(f"https://www.amazon.com/dp/{asin}")
    if not BeautifulSoup(html, "html.parser").select_one("#productTitle"):
        raise RuntimeError(f"no product title on page for {asin} (bot challenge?)")
    info = parse_product_page(html, asin)
    log.info("product page %s rating=%s reviews=%s seller=%r 1-2star=%s complaints=%s", asin, info.rating,
             info.review_count, info.seller, info.low_star_pct, [a.name for a in info.complaints()])
    return info
