"""Live Amazon search prices via headless Playwright (Amazon blocks plain HTTP with a JS challenge).

Usage: python tools/amazon_prices.py "WOLFBOX MF50 air duster" ["another query" ...] [--n 5]
Prints price | rating | link | title per result. Prices exclude clip-on coupons.
"""
from __future__ import annotations

import argparse
import logging
from urllib.parse import quote_plus

from playwright.sync_api import Page, sync_playwright

log = logging.getLogger("amazon_prices")


def search(page: Page, query: str, n: int) -> list[dict[str, str]]:
    url = f"https://www.amazon.com/s?k={quote_plus(query)}"
    log.info("GET %s", url)
    page.goto(url, timeout=30000)
    page.wait_for_timeout(4000)
    cards = page.query_selector_all('[data-component-type="s-search-result"]')
    if not cards:
        log.warning("0 result cards for %r (page title: %r) - possibly blocked", query, page.title())
    rows: list[dict[str, str]] = []
    for card in cards[:n]:
        title_el = card.query_selector("[data-cy=title-recipe]")
        price_el = card.query_selector(".a-price .a-offscreen")
        rating_el = card.query_selector(".a-icon-alt")
        rows.append({
            "price": price_el.inner_text() if price_el else "-",
            "rating": rating_el.inner_text() if rating_el else "-",
            "url": f"https://www.amazon.com/dp/{card.get_attribute('data-asin')}",
            "title": " ".join((title_el.inner_text() if title_el else "").split())[:110],
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("queries", nargs="+")
    parser.add_argument("--n", type=int, default=5)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    with sync_playwright() as pw:
        # Own Playwright browser, closed via API - never kill chrome by image name.
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_context(locale="en-US").new_page()
            for query in args.queries:
                print(f"## {query}")
                for row in search(page, query, args.n):
                    print(f"  {row['price']} | {row['rating']} | {row['url']} | {row['title']}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
