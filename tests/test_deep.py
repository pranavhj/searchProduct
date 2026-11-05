"""Deep search (several FB pages, server-side price band) and the vetted-good-only alert filter."""
from __future__ import annotations

import json
from types import SimpleNamespace

from price_watch.__main__ import hold_unproven_alerts
from price_watch.local_cheap import facebook_deep_variables, facebook_next_cursor

BASE = {"count": 24, "params": {"browse_request_params": {"filter_price_lower_bound": 0,
                                                           "filter_price_upper_bound": 214748364700}}}


def test_deep_variables_set_band_in_cents_sort_and_cursor() -> None:
    v = facebook_deep_variables(BASE, 60.0, 250.0, "PRICE_ASCEND", "abc")
    brp = v["params"]["browse_request_params"]
    assert (brp["filter_price_lower_bound"], brp["filter_price_upper_bound"]) == (6000, 25000)
    assert brp["commerce_search_sort_by"] == "PRICE_ASCEND"
    assert v["cursor"] == "abc"
    assert BASE["params"]["browse_request_params"]["filter_price_lower_bound"] == 0  # input untouched


def test_deep_variables_default_order_first_page_has_no_sort_or_cursor() -> None:
    v = facebook_deep_variables(BASE, None, None, None, None)
    assert "commerce_search_sort_by" not in v["params"]["browse_request_params"]
    assert "cursor" not in v
    assert v["params"]["browse_request_params"]["filter_price_upper_bound"] == 214748364700


def test_deep_variables_turn_off_shipped_listings_so_pages_stay_local() -> None:
    v = facebook_deep_variables(BASE, None, None, None, None)
    assert v["params"]["browse_request_params"]["commerce_enable_shipping"] is False


def test_next_cursor_only_when_more_pages() -> None:
    def resp(info: dict) -> dict:
        return {"data": {"marketplace_search": {"feed_units": {"page_info": info}}}}
    assert facebook_next_cursor(resp({"end_cursor": "c1", "has_next_page": True})) == "c1"
    assert facebook_next_cursor(resp({"end_cursor": "c1", "has_next_page": False})) is None
    assert facebook_next_cursor({}) is None


def _alert(key: str) -> SimpleNamespace:
    return SimpleNamespace(obs=SimpleNamespace(key=key))


def test_hold_unproven_alerts_keeps_only_buy_ok() -> None:
    verdict = lambda v: SimpleNamespace(verdict=v)  # noqa: E731
    s = SimpleNamespace(alerts=[_alert("buy"), _alert("ok"), _alert("unvetted"), _alert("avoid")],
                        vettings={"buy": verdict("buy"), "ok": verdict("ok"), "avoid": verdict("avoid")})
    held = hold_unproven_alerts([s])
    assert [a.obs.key for a in s.alerts] == ["buy", "ok"]
    assert [a.obs.key for a in held] == ["unvetted", "avoid"]


def test_deep_only_drops_unmarked_items_and_non_pickup_sources() -> None:
    from price_watch.__main__ import deep_only
    from price_watch.config import WatchItem
    items = [WatchItem("pc", "dell optiplex", deep=True), WatchItem("mat", "yoga mat")]
    got = deep_only(items, ["amazon", "facebook_marketplace", "craigslist"])
    assert [(i.id, i.sources) for i in got] == [("pc", ["facebook_marketplace", "craigslist"])]


def test_vetting_checks_only_cheapest_alerts_so_one_big_item_cannot_use_the_whole_budget() -> None:
    import asyncio

    from price_watch.__main__ import MAX_ALERTS_VETTED, _vet_summary
    obs = [SimpleNamespace(key=f"k{n}", price=float(n)) for n in range(30)]
    summary = SimpleNamespace(fetch=SimpleNamespace(observations=obs), vettings={}, skipped_alerts=[],
                              alerts=[SimpleNamespace(obs=o, kind="target_hit") for o in obs])
    seen: list = []

    async def vetter(item: object, to_vet: list, peers: list) -> dict:
        seen.extend(to_vet)
        return {}
    asyncio.run(_vet_summary(vetter, SimpleNamespace(id="webcam"), summary, 5))
    assert len(seen) == 5 + MAX_ALERTS_VETTED


def test_watchlist_deep_items_load() -> None:
    from price_watch.config import load_watchlist
    wl = load_watchlist()
    deep = {i.id for i in wl.items if i.deep}
    assert {"adjustable-dumbbells", "weight-bench", "webcam-1080p-mjpeg", "cad-pc-optiplex"} <= deep
    assert json.dumps(sorted(deep))  # serialisable ids
