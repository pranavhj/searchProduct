"""Evidence flags + verdicts for cheap listings, and the Amazon product-page parser."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from price_watch.amazon_cheap import parse_review_counts
from price_watch.amazon_page import PageInfo, parse_product_page
from price_watch.analyze import Alert
from price_watch.fetch import FetchResult, Observation
from price_watch.cheap_flags import build_vet, vet_summary

PAGE_HTML = """
<span id="productTitle">Aaoyun 10000mAh Power Bank</span>
<div data-hook="rating-out-of-text">4.5 out of 5</div>
<div data-hook="total-review-count">785 global ratings</div>
<a id="sellerProfileTriggerId">Jetsum Tech</a>
<div id="buybox">Ships from: Amazon Sold by: Jetsum Tech $9.49</div>
<div id="productOverview_feature_div"><span class="po-brand">Brand Aaoyun</span></div>
<table id="histogramTable"><tr><td>5 star 78% 9% 4% 2% 7%</td></tr></table>
<div data-testid="overall-summary">Battery life receives mixed feedback.</div>
<div data-testid="bottomsheet-content-battery life">42 customers mention "Battery life" 23 positive, 19 negative
  <div data-testid="aspect-summary">Some say it broke within 6 months.</div></div>
<div data-testid="bottomsheet-content-size">45 customers mention "Size" 45 positive, 0 negative</div>
"""


def amz(price: float, reviews: int | None, rating: float | None, title: str = "Anker Power Bank 10000mAh") -> Observation:
    return Observation("pb", "amazon", f"A{price}", title, "u", price, price, "new", None,
                       rating=rating, review_count=reviews)


def local(price: float, title: str, key: str = "L") -> Observation:
    return Observation("pb", "craigslist", key, title, "u", price, price, "used", "sfbay", distance_mi=5)


def peers(*prices: float) -> list[Observation]:
    return [amz(p, 500, 4.5, f"Anker {p}") for p in prices]


MARKET = peers(20, 22, 24, 26, 28)  # median 24


def test_page_parser_reads_rating_reviews_seller_and_brand() -> None:
    p = parse_product_page(PAGE_HTML, "B0X")
    assert (p.rating, p.review_count, p.seller, p.ships_from, p.brand) == (4.5, 785, "Jetsum Tech", "Amazon", "Aaoyun")


def test_page_parser_reads_star_histogram() -> None:
    assert parse_product_page(PAGE_HTML, "B0X").star_pct == {5: 78, 4: 9, 3: 4, 2: 2, 1: 7}


def test_page_parser_reports_only_aspects_with_real_negatives() -> None:
    names = [a.name for a in parse_product_page(PAGE_HTML, "B0X").complaints()]
    assert names == ["battery life"]


def test_card_review_counts_come_from_the_ratings_aria_label() -> None:
    html = ('<div data-component-type="s-search-result" data-asin="B1"><span aria-label="1,234 ratings"></span></div>')
    assert parse_review_counts(html) == {"B1": 1234}


def test_unknown_brand_with_few_reviews_and_low_rating_is_risky() -> None:
    v = build_vet(amz(9, 12, 3.8, "Zorbo Power Bank 10000mAh"), MARKET + [amz(9, 12, 3.8, "Zorbo Power Bank 10000mAh")])
    assert v.verdict == "risky"


def test_card_only_listing_with_no_flags_is_not_called_worth_it() -> None:
    assert build_vet(amz(19, 5000, 4.6), MARKET).verdict == "no red flags found"


def test_page_checked_listing_with_many_good_reviews_is_worth_it() -> None:
    page = PageInfo("A", rating=4.6, review_count=5000, seller="Amazon", brand="Anker")
    assert build_vet(amz(19, 5000, 4.6), MARKET, page).verdict == "worth it"


def test_multiword_known_brand_in_title_is_recognised() -> None:
    assert build_vet(amz(19, 5000, 4.6, "Sunny Health pull up bar"), MARKET).flag_texts() == []


def test_leading_pack_count_does_not_become_the_brand() -> None:
    assert build_vet(amz(19, 5000, 4.6, "2 Pack Anker power bank"), MARKET).flag_texts() == []


def test_pm_me_wording_is_only_a_risk_not_a_trap() -> None:
    assert build_vet(local(24, "pm me, power bank anker 20000"), MARKET).verdict == "no red flags found"


def test_full_pc_mentioning_motherboard_is_not_called_a_trap() -> None:
    assert build_vet(local(24, "Gaming PC ASUS motherboard 64GB RAM"), MARKET).verdict == "no red flags found"


def test_card_only_vet_is_lower_confidence() -> None:
    assert build_vet(amz(19, 5000, 4.6), MARKET).confidence == "lower"


def test_page_checked_vet_with_many_ratings_is_medium_confidence() -> None:
    page = PageInfo("A", rating=4.5, review_count=785, seller="Amazon")
    assert build_vet(amz(19, 785, 4.5), MARKET, page).confidence == "medium"


def test_few_ratings_and_far_below_market_is_a_trap() -> None:
    assert build_vet(amz(8, 4, 5.0), MARKET).verdict == "probably a trap"


def test_sealed_at_half_price_is_a_trap() -> None:
    assert build_vet(local(10, "Brand new sealed Anker power bank"), MARKET).verdict == "probably a trap"


def test_supports_64gb_listing_is_flagged_as_partial() -> None:
    v = build_vet(local(20, "ASUS motherboard supports 64GB ram"), MARKET)
    assert "possible per-part/partial listing - confirm what the price covers" in v.flag_texts()


def test_zelle_wording_is_flagged_as_scam_style() -> None:
    assert build_vet(local(20, "Power bank zelle only"), MARKET).verdict == "probably a trap"


def test_vague_local_title_is_a_risk_flag() -> None:
    assert "vague title - ask for photos/specs" in build_vet(local(24, "Power bank"), MARKET).flag_texts()


def test_too_few_peers_means_no_far_below_flag() -> None:
    assert build_vet(amz(1, 5000, 4.6), peers(20, 22)).flag_texts() == []


def test_vet_summary_opens_product_page_only_for_alerted_amazon_listings() -> None:
    opened: list[str] = []

    async def fetch_page(asin: str) -> PageInfo:
        opened.append(asin)
        return PageInfo(asin, rating=4.5, review_count=300, seller="Amazon")

    cheap = amz(19, 5000, 4.6)
    cheap.listing_id = "ASINCHEAP"
    summary = SimpleNamespace(item=SimpleNamespace(id="pb"), fetch=FetchResult("pb", MARKET + [cheap], {}, 6, {}),
                              alerts=[Alert("pb", "target_hit", cheap, 20, "m")])
    asyncio.run(vet_summary(summary, 3, fetch_page))
    assert opened == ["ASINCHEAP"]


def test_vet_summary_survives_a_failing_page_fetch() -> None:
    async def boom(asin: str) -> PageInfo:
        raise RuntimeError("bot challenge")

    cheap = amz(19, 5000, 4.6)
    summary = SimpleNamespace(item=SimpleNamespace(id="pb"), fetch=FetchResult("pb", MARKET + [cheap], {}, 6, {}),
                              alerts=[Alert("pb", "target_hit", cheap, 20, "m")])
    asyncio.run(vet_summary(summary, 3, boom))
    assert cheap.cheap.confidence == "lower"
