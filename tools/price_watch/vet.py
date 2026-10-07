"""'Why is it so cheap?' - evidence flags and a verdict for the cheapest listings.

Everything here is rule-based and derived from what was fetched this run (card/title data, plus the product
page for alerted Amazon items). It cannot judge real product quality, so confidence is never 'high':
'medium' = product page read and plenty of ratings; 'lower' = judged from card/title only.
"""
from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from price_watch.amazon_page import PageInfo
from price_watch.fetch import LOCAL_SOURCES, Observation

log = logging.getLogger("price_watch.vet")

MIN_PEERS = 5  # comparable listings needed before 'far below median' means anything
FAR_BELOW = 0.5  # price < 50% of the median of this run's relevant listings
MAX_PAGE_VETS_PER_ITEM = 3

# Mainstream brands only; anything else is "unrecognised", not "bad" - hence a risk flag, not a trap flag.
KNOWN_BRANDS = {
    "anker", "baseus", "ugreen", "belkin", "samsung", "apple", "mophie", "iniu", "aohi", "charmast", "eneloop",
    "amazon", "amazonbasics", "insignia", "zendure", "xiaomi", "romoss", "poweradd", "tokk", "hyper", "nimble",
    "cuktech", "sharge", "satechi", "jackery", "ravpower", "voltme", "kuulaa", "miady", "talk works", "onn",
    "onn.", "intel", "amd", "dell", "hp", "lenovo", "acer", "asus", "gigabyte", "msi", "nzxt", "corsair",
    "crucial", "kingston", "gopro", "yes4all", "gofit", "perfect fitness", "iron gym", "ironage", "bowflex",
    "nordictrack", "decathlon", "tonal", "stamina", "sunny health", "cap barbell", "fitness reality", "rep",
    "garren", "sportsroyals", "jfit", "kootek", "vivitar", "logitech", "tp-link", "govee", "kasa", "wyze",
}

SCAM_RE = re.compile(r"\b(zelle|venmo|cash ?app|gift ?card|deposit|western union|wire transfer|courier|"
                     r"shipping only|text me|pm me|whatsapp)\b", re.I)
SEALED_RE = re.compile(r"\b(sealed|brand new|new in box|nib|unopened|never opened|bnib)\b", re.I)
PARTS_RE = re.compile(r"\b(for parts|parts only|no ram|no ssd|no hdd|no storage|no os|barebones?|bare|case only|"
                      r"motherboard|board only|cpu only|supports? \d+ ?gb|handle only|bracket only|"
                      r"read description)\b", re.I)
USED_RE = re.compile(r"\b(refurbished|renewed|open box|used|pre-?owned|like new)\b", re.I)

STRONG, RISK, INFO = "strong", "risk", "info"


@dataclass
class Flag:
    level: str  # strong | risk | info
    text: str


@dataclass
class Vet:
    flags: list[Flag] = field(default_factory=list)
    verdict: str = "worth it"  # worth it | risky | probably a trap
    confidence: str = "lower"  # medium | lower  (never 'high': product quality isn't verifiable here)
    basis: str = ""  # what the verdict rests on
    would_change: str = ""  # what, if false, breaks it
    themes: list[str] = field(default_factory=list)  # complaint themes (page-checked Amazon only)
    customers_say: str | None = None
    page: PageInfo | None = None

    def flag_texts(self) -> list[str]:
        return [f.text for f in self.flags]


def peer_median(observations: list[Observation], exclude_key: str) -> float | None:
    prices = [o.price for o in observations if o.key != exclude_key and o.price > 0]
    return statistics.median(prices) if len(prices) >= MIN_PEERS else None


def _brand_word(title: str) -> str:
    return re.sub(r"[^a-z0-9. -]", "", title.lower().split(" ")[0]) if title.strip() else ""


def flags_for(obs: Observation, median: float | None, page: PageInfo | None = None) -> list[Flag]:
    flags: list[Flag] = []
    title = obs.title
    far_below = median is not None and obs.price > 0 and obs.price < FAR_BELOW * median
    if far_below:
        flags.append(Flag(RISK, f"price {100 - obs.price / median * 100:.0f}% below median ${median:.2f}"))

    if obs.source == "amazon":
        if USED_RE.search(title) or obs.condition in ("used", "refurbished"):
            flags.append(Flag(RISK, "refurbished/used"))
        reviews = page.review_count if page and page.review_count is not None else obs.review_count
        rating = page.rating if page and page.rating is not None else obs.rating
        if reviews is None:
            flags.append(Flag(INFO, "review count unknown"))
        elif reviews < 10 and far_below:
            flags.append(Flag(STRONG, f"only {reviews} ratings AND far below market"))
        elif reviews < 50:
            flags.append(Flag(RISK, f"few ratings ({reviews})"))
        if rating is not None and rating < 4.0:
            flags.append(Flag(RISK, f"rating {rating:.1f}★ < 4.0"))
        brand = (page.brand.split()[0] if page and page.brand else _brand_word(title)).lower()
        if brand and not any(brand == b or brand.startswith(b + " ") for b in KNOWN_BRANDS):
            flags.append(Flag(RISK if (reviews or 0) < 500 else INFO, f"unrecognised brand '{brand}'"))
        if page is not None:
            if page.third_party:
                flags.append(Flag(RISK if (reviews or 0) < 100 else INFO, f"third-party seller: {page.seller}"))
            low = page.low_star_pct
            if low is not None and low >= 20:
                flags.append(Flag(RISK, f"{low}% of ratings are 1-2★"))
            for a in page.complaints():
                if a.mentions >= 10 and a.negative / a.mentions >= 0.3:
                    flags.append(Flag(RISK, f"{a.name}: {a.negative} of {a.mentions} mentions negative"))
    elif obs.source in LOCAL_SOURCES:
        if SCAM_RE.search(title):
            flags.append(Flag(STRONG, "scam-style payment/contact wording in title"))
        if SEALED_RE.search(title) and median is not None and obs.price < 0.6 * median:
            flags.append(Flag(STRONG, "claims new/sealed at <60% of market"))
        if PARTS_RE.search(title):
            flags.append(Flag(STRONG, "possible per-part/partial listing - confirm what the price covers"))
        if obs.price == 0:
            flags.append(Flag(STRONG, "$0 price - likely placeholder or 'free'"))
        if len(re.findall(r"[A-Za-z0-9]+", title)) <= 3:
            flags.append(Flag(RISK, "vague title - ask for photos/specs"))
    return flags


def judge(flags: list[Flag], page: PageInfo | None, obs: Observation) -> tuple[str, str, str, str]:
    """Return (verdict, confidence, basis, would_change)."""
    strong = [f for f in flags if f.level == STRONG]
    risks = [f for f in flags if f.level == RISK]
    if strong:
        verdict = "probably a trap"
    elif len(risks) >= 2:
        verdict = "risky"
    else:
        verdict = "worth it"
    reviews = page.review_count if page and page.review_count is not None else obs.review_count
    checked = page is not None and (reviews or 0) >= 50
    confidence = "medium" if checked else "lower"
    if obs.source == "amazon":
        basis = (f"product page read, {reviews} ratings" if checked else
                 "card data only (rating/review count/title); product page not read" if page is None else
                 f"product page read but only {reviews or 0} ratings")
        would = "real-world quality is unverified; ratings can be incentivised or from a different variant"
    else:
        basis = "listing title/price vs this run's other listings; photos and seller not seen"
        would = "seller's answers/photos: confirm exactly what's included and test before paying"
    return verdict, confidence, basis, would


def build_vet(obs: Observation, peers: list[Observation], page: PageInfo | None = None) -> Vet:
    median = peer_median(peers, obs.key)
    flags = flags_for(obs, median, page)
    verdict, confidence, basis, would = judge(flags, page, obs)
    vet = Vet(flags, verdict, confidence, basis, would, page=page)
    if page is not None:
        vet.customers_say = page.customers_say
        # Amazon's per-aspect blurb is usually the positive majority view; only quote it when the aspect is mixed.
        vet.themes = [f"{a.name}: {a.negative}/{a.mentions} negative" +
                      (f" - {a.summary}" if a.summary and a.negative / a.mentions >= 0.3 else "")
                      for a in page.complaints()[:3]]
        if page.low_star_pct is not None and page.low_star_pct >= 10:
            vet.themes.insert(0, f"{page.low_star_pct}% of ratings are 1-2★")
    return vet


PageFetcher = Callable[[str], Awaitable[PageInfo]]


async def vet_summary(summary: Any, top_n: int, fetch_page: PageFetcher | None) -> None:
    """Attach a Vet to the report's top listings and every alerted listing.

    The product page is opened only for alerted Amazon listings (a few per item), never for the whole list.
    """
    observations: list[Observation] = summary.fetch.observations
    top = sorted(observations, key=lambda o: o.price)[:top_n]
    targets = {o.key: o for o in top}
    pages: dict[str, PageInfo] = {}
    page_budget = MAX_PAGE_VETS_PER_ITEM
    for alert in summary.alerts:
        o = alert.obs
        targets[o.key] = o
        if o.source == "amazon" and fetch_page is not None and page_budget > 0 and o.key not in pages:
            page_budget -= 1
            try:
                pages[o.key] = await fetch_page(o.listing_id)
            except Exception as exc:  # vetting is context; the alert still goes out
                log.warning("item=%s page vet failed for %s: %s", summary.item.id, o.listing_id, exc)
    for key, o in targets.items():
        o.vet = build_vet(o, observations, pages.get(key))
    log.info("item=%s vetted=%d pages=%d verdicts=%s", summary.item.id, len(targets), len(pages),
             sorted({o.vet.verdict for o in targets.values()}))
