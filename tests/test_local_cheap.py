"""Price-sorted pass for pickup sources + the bigger pre-distance cap."""
from __future__ import annotations

import asyncio

from price_watch.config import Defaults, WatchItem
from price_watch.fetch import fetch_item
from price_watch.local_cheap import craigslist_url, facebook_sort_variables, in_band, offerup_url

D = Defaults()


def mk(i: str, src: str, loc: str, price: float = 10) -> dict:
    return {"listing": {"id": i, "source": src, "title": "Power Bank", "url": i, "price": price,
                        "location": loc, "shipping": "Local pickup"}, "deal_score": None}


class FakeLocal:
    async def find_best_deals(self, query: str, **kw: object) -> dict:
        self.kw = kw
        return {"best_deals": [mk("cl", "craigslist", "sfbay: pleasanton"), mk("ou", "offerup", "Pleasanton, CA"),
                               mk("fb", "facebook_marketplace", "Milpitas, California")]}


def test_local_cheapest_pass_is_merged_deduped_and_distance_filtered() -> None:
    calls: list[tuple] = []

    async def cheapest(src: str, q: str, lo: object, hi: object, limit: int) -> list[dict]:
        calls.append((src, limit))
        return [mk("fb", src, "Milpitas, California", 5), mk(f"{src}-far", src, "Fresno, CA", 3)]

    miles = {"Milpitas, California": 1.0, "Pleasanton, CA": 16.0, "sfbay: pleasanton": 16.0, "Fresno, CA": 200.0}
    svc = FakeLocal()
    res = asyncio.run(fetch_item(svc, WatchItem("p", "power bank", max_miles=10, sources=["facebook_marketplace"]),
                                 D, distance=miles.get, local_cheapest=cheapest))
    assert calls == [("facebook_marketplace", 40)]
    assert svc.kw["max_results_per_source"] == 40  # raised above the default 15 before the distance filter
    assert [o.listing_id for o in res.observations] == ["fb"]


def test_local_cheapest_failure_is_reported_not_fatal() -> None:
    async def boom(*a: object) -> list[dict]:
        raise RuntimeError("blocked")

    res = asyncio.run(fetch_item(FakeLocal(), WatchItem("p", "power bank", max_miles=10), D,
                                 distance=lambda loc: 1.0, local_cheapest=boom))
    assert len(res.observations) == 3
    assert {"craigslist_cheapest", "offerup_cheapest", "facebook_marketplace_cheapest"} <= set(res.source_errors)


def test_urls_variables_and_band() -> None:
    assert "sort=priceasc" in craigslist_url("sfbay", "power bank", 8, 45)
    assert "min_price=8" in craigslist_url("sfbay", "power bank", 8, 45)
    assert "SORT=price" in offerup_url("power bank", 25)
    v = {"params": {"browse_request_params": {"a": 1}}}
    assert facebook_sort_variables(v)["params"]["browse_request_params"]["commerce_search_sort_by"] == "PRICE_ASCEND"
    assert "commerce_search_sort_by" not in v["params"]["browse_request_params"]  # input not mutated
    assert (in_band(None, 1, 5), in_band(0, 3, None), in_band(4, 3, 5)) == (False, False, True)
