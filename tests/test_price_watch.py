"""Unit tests for tools/price_watch. Run: tests\\run_tests.cmd"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from price_watch import analyze, config, report
from price_watch.config import Defaults, WatchItem
from price_watch.fetch import FetchResult, Observation, fetch_item, relevance_reason
from price_watch.store import Store

D = Defaults(drop_pct=20.0, baseline_days=30)


def obs(key: str, price: float, item: str = "duster") -> Observation:
    return Observation(item, "ebay_public", key, f"title {key}", f"https://x/{key}", price, price, "used", None)


def fr(*o: Observation, item: str = "duster") -> FetchResult:
    return FetchResult(item, list(o), {}, len(o), {})


def run_day(store: Store, item: WatchItem, day: date, observations: list[Observation]) -> analyze.ItemSummary:
    run_id = store.start_run()
    s = analyze.analyze_item(store, run_id, item, fr(*observations, item=item.id), D, today=day)
    store.add_observations(run_id, observations, datetime(day.year, day.month, day.day, 8))
    for a in s.alerts:
        store.record_alert(a.item_id, a.obs.key, a.obs.price, a.kind)
    return s


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "t.db") as s:
        yield s


# --- relevance ---------------------------------------------------------------
def test_relevance_include_groups_and_exclude() -> None:
    item = WatchItem("x", "q", must_include=["duster|blower", "usb-c"], exclude=["case"], price_min=10)
    assert relevance_reason(item, "Electric Air Duster USB-C 110k", 30) is None
    assert relevance_reason(item, "Electric Blower usb-c", 30) is None
    assert relevance_reason(item, "Electric Air Duster micro usb", 30) == "missing_keyword"
    assert relevance_reason(item, "Duster USB-C carrying case", 30) == "excluded_keyword"
    assert relevance_reason(item, "Duster USB-C", 5) == "below_price_min"


def test_fetch_filters_dedupes_and_uses_landed_price() -> None:
    class Fake:
        async def find_best_deals(self, query: str, **kw: object) -> dict:
            mk = lambda i, t, **p: {"listing": {"id": i, "source": "amazon", "title": t, "url": f"u{i}", **p},
                                     "deal_score": 50}
            return {"source_errors": {"offerup": "boom"}, "best_deals": [
                mk("1", "Air Duster", price=20, total_with_tax=21.88),
                mk("1", "Air Duster", price=20),  # duplicate key
                mk("2", "Duster case", price=5),
                mk("3", "Air Duster", price=None),
            ]}

    item = WatchItem("d", "air duster", exclude=["case"])
    res = asyncio.run(fetch_item(Fake(), item, D))
    assert [o.listing_id for o in res.observations] == ["1"]
    assert res.observations[0].price == 20.0  # sticker, not tax-inclusive 21.88
    assert res.dropped == {"excluded_keyword": 1, "no_price": 1}
    assert res.source_errors == {"offerup": "boom"}


def test_used_only_item_drops_new_listings() -> None:
    class Fake:
        async def find_best_deals(self, query: str, **kw: object) -> dict:
            mk = lambda i, c: {"listing": {"id": i, "source": "amazon", "title": "Pull up bar", "url": f"u{i}",
                                            "price": 20, "condition": c}}
            return {"best_deals": [mk("new", "new"), mk("used", "used"), mk("unk", "unknown")]}

    res = asyncio.run(fetch_item(Fake(), WatchItem("p", "pull up bar", condition="used"), D))
    assert [o.listing_id for o in res.observations] == ["used", "unk"]
    assert res.dropped == {"not_used": 1}


# --- analyzer ----------------------------------------------------------------
def test_first_run_has_no_drop_alerts(store: Store) -> None:
    s = run_day(store, WatchItem("duster", "q"), date(2026, 10, 1), [obs("a", 30), obs("b", 40)])
    assert s.first_run and s.alerts == [] and s.best.price == 30 and s.new_listings == []


def test_listing_price_cut_alerts_once(store: Store) -> None:
    item = WatchItem("duster", "q")
    run_day(store, item, date(2026, 10, 1), [obs("a", 50)])
    s2 = run_day(store, item, date(2026, 10, 2), [obs("a", 35)])  # -30%
    assert [a.kind for a in s2.alerts] == ["listing_drop"]
    s3 = run_day(store, item, date(2026, 10, 3), [obs("a", 35)])
    assert s3.alerts == []  # same listing, same price: not re-alerted


def test_small_cut_below_threshold_is_quiet(store: Store) -> None:
    item = WatchItem("duster", "q")
    run_day(store, item, date(2026, 10, 1), [obs("a", 50)])
    assert run_day(store, item, date(2026, 10, 2), [obs("a", 45)]).alerts == []  # -10%


def test_big_drop_vs_median_needs_baseline_days(store: Store) -> None:
    item = WatchItem("duster", "q")
    run_day(store, item, date(2026, 10, 1), [obs("d0", 100)])
    run_day(store, item, date(2026, 10, 2), [obs("d1", 100)])
    assert run_day(store, item, date(2026, 10, 3), [obs("new", 60)]).alerts == []  # only 2 baseline days
    s = run_day(store, item, date(2026, 10, 4), [obs("new2", 55), obs("d2", 100)])
    assert [a.kind for a in s.alerts] == ["big_drop"]
    assert s.baseline == 100 and s.new_listings[0].key == "ebay_public:new2"
    assert round(s.change_pct) == -8  # vs previous day's best $60


def test_target_hit_and_further_drop_realerts(store: Store) -> None:
    item = WatchItem("duster", "q", target_price=30)
    s1 = run_day(store, item, date(2026, 10, 1), [obs("a", 29), obs("b", 45)])
    assert [(a.kind, a.obs.listing_id) for a in s1.alerts] == [("target_hit", "a")]
    assert run_day(store, item, date(2026, 10, 2), [obs("a", 29)]).alerts == []
    assert [a.kind for a in run_day(store, item, date(2026, 10, 3), [obs("a", 25)]).alerts] == ["target_hit"]


def test_same_day_rerun_excludes_today_from_baseline(store: Store) -> None:
    item = WatchItem("duster", "q")
    run_day(store, item, date(2026, 10, 1), [obs("a", 50)])
    run_day(store, item, date(2026, 10, 2), [obs("a", 50)])
    s = run_day(store, item, date(2026, 10, 2), [obs("a", 50)])
    assert s.baseline_days == 1 and s.prev_best == 50


# --- config + report -----------------------------------------------------------
def test_watchlist_roundtrip_and_validation(tmp_path: Path) -> None:
    wl = config.Watchlist(items=[WatchItem("a", "q", target_price=10, must_include=["x|y"])])
    path = tmp_path / "w.json"
    config.save_watchlist(wl, path)
    assert config.load_watchlist(path).items[0].must_include == ["x|y"]
    path.write_text(json.dumps({"items": [{"id": "a", "query": "q", "taget_price": 1}]}))
    with pytest.raises(ValueError, match="unknown keys"):
        config.load_watchlist(path)


def test_apply_mcp_env_does_not_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / ".mcp.json"
    p.write_text(json.dumps({"mcpServers": {"shopping-deals": {"env": {"PW_T1": "a", "PW_T2": "b"}}}}))
    monkeypatch.setenv("PW_T2", "keep")
    monkeypatch.delenv("PW_T1", raising=False)
    config.apply_mcp_env(p)
    import os
    assert os.environ["PW_T1"] == "a" and os.environ["PW_T2"] == "keep"


def test_report_renders_alerts_and_pipes(store: Store, tmp_path: Path) -> None:
    item = WatchItem("duster", "q", target_price=30)
    o = obs("a", 29)
    o.title = "Duster | 110k"
    s = run_day(store, item, date(2026, 10, 1), [o])
    text = report.render([s], datetime(2026, 10, 1, 8), 5)
    assert "Target hit" in text and "Duster / 110k" in text and "first run" in text
    path = report.write(text, tmp_path, datetime(2026, 10, 1, 8))
    assert (tmp_path / "latest.md").read_text(encoding="utf-8") == text
    assert "duster: best $29.00" in report.digest([s], path)


def _fb(listing_id: str, location: str, shipping: str) -> dict:
    return {"listing": {"id": listing_id, "source": "facebook_marketplace", "title": "Air Duster", "url": "u",
                        "price": 20, "total_with_tax": 21.88, "location": location, "shipping": shipping}}


class FakeFB:
    async def find_best_deals(self, query: str, **kw: object) -> dict:
        return {"best_deals": [
            _fb("local", "San Jose, California", "Local pickup"),
            _fb("far", "Sacramento, California", "Local pickup"),
            _fb("ships", "Shelton, Washington", "Shipping offered"),
        ]}


def test_fb_private_sale_price_has_no_sales_tax() -> None:
    res = asyncio.run(fetch_item(FakeFB(), WatchItem("d", "air duster"), D))
    assert res.observations[0].price == 20.0


def test_fb_pickup_beyond_max_miles_dropped_but_shippable_kept() -> None:
    miles = {"San Jose, California": 6.0, "Sacramento, California": 95.0}
    res = asyncio.run(fetch_item(FakeFB(), WatchItem("d", "air duster", max_miles=10), D,
                                 distance=lambda loc: miles.get(loc)))
    assert [(o.listing_id, o.distance_mi) for o in res.observations] == [("local", 6.0), ("ships", None)]


# --- review follow-ups: re-alert semantics, cap, run loop delivery -------------
def test_price_bounce_back_to_alerted_level_is_not_realerted(store: Store) -> None:
    item = WatchItem("duster", "q", target_price=30)
    run_day(store, item, date(2026, 10, 1), [obs("a", 25)])
    run_day(store, item, date(2026, 10, 2), [obs("a", 29)])
    assert run_day(store, item, date(2026, 10, 3), [obs("a", 25)]).alerts == []


def test_listing_drop_compares_to_most_recent_price_not_oldest(store: Store) -> None:
    item = WatchItem("duster", "q")
    run_day(store, item, date(2026, 10, 1), [obs("a", 100)])
    run_day(store, item, date(2026, 10, 2), [obs("a", 40)])
    s = run_day(store, item, date(2026, 10, 3), [obs("a", 30)])
    assert [(a.kind, a.old_price) for a in s.alerts] == [("listing_drop", 40)]


def test_new_under_target_listing_alerts_after_cap_was_used(store: Store) -> None:
    item = WatchItem("duster", "q", target_price=30)
    run_day(store, item, date(2026, 10, 1), [obs("a", 20), obs("b", 21), obs("c", 22)])
    s = run_day(store, item, date(2026, 10, 2), [obs("a", 20), obs("b", 21), obs("c", 22), obs("d", 25)])
    assert [a.obs.listing_id for a in s.alerts] == ["d"]


def test_target_alerts_capped_at_three_per_item(store: Store) -> None:
    item = WatchItem("duster", "q", target_price=30)
    s = run_day(store, item, date(2026, 10, 1), [obs("a", 20), obs("b", 21), obs("c", 22), obs("d", 25)])
    assert [a.obs.listing_id for a in s.alerts] == ["a", "b", "c"]


def test_history_uses_calendar_window(store: Store) -> None:
    today = date.today()
    old = today.replace(year=today.year - 1)
    store.add_observations(store.start_run(), [obs("a", 50)], datetime(old.year, old.month, old.day, 8))
    store.add_observations(store.start_run(), [obs("b", 40)], datetime(today.year, today.month, today.day, 8))
    assert [r[1] for r in store.history("duster", 30)] == [40.0]


def test_pickup_with_unknown_distance_is_dropped() -> None:
    res = asyncio.run(fetch_item(FakeFB(), WatchItem("d", "air duster"), D, distance=lambda loc: None))
    assert res.dropped == {"distance_unknown": 2}


class FakeService:
    def __init__(self, price: float, errors: dict | None = None):
        self.price, self.errors = price, errors or {}

    async def find_best_deals(self, query: str, **kw: object) -> dict:
        if self.errors:
            return {"source_errors": self.errors, "best_deals": []}
        return {"best_deals": [{"listing": {"id": "a", "source": "amazon", "title": "Air Duster", "url": "u",
                                            "price": self.price}}]}


class FakeNotifier:
    def __init__(self, ok: bool):
        self.ok, self.discord, self.toasts = ok, [], []

    def send_discord(self, target: str, message: str) -> bool:
        self.discord.append(message)
        return self.ok

    def send_toast(self, title: str, body: str) -> bool:
        self.toasts.append(body)
        return self.ok


def _wl(discord: str | None = "123") -> config.Watchlist:
    return config.Watchlist(items=[WatchItem("duster", "air duster", target_price=30, sources=["amazon"])],
                            notify=config.NotifyConfig(discord_target=discord, toast=False))


def _run(store: Store, tmp_path: Path, service: FakeService, notifier: FakeNotifier, notify: bool = True,
         day: int = 1, wl: config.Watchlist | None = None) -> str:
    from price_watch.__main__ import run_items
    wl = wl or _wl()
    status, _ = asyncio.run(run_items(store, service, wl, wl.items, notify, notifier, tmp_path,
                                      datetime(2026, 10, day, 8).astimezone()))
    return status


def test_run_failed_discord_delivery_retries_alert_next_run(store: Store, tmp_path: Path) -> None:
    _run(store, tmp_path, FakeService(25), FakeNotifier(ok=False), day=1)
    retry = FakeNotifier(ok=True)
    _run(store, tmp_path, FakeService(25), retry, day=2)
    assert "(retry)" in retry.discord[0]


def test_run_delivered_alert_not_sent_again(store: Store, tmp_path: Path) -> None:
    _run(store, tmp_path, FakeService(25), FakeNotifier(ok=True), day=1)
    second = FakeNotifier(ok=True)
    _run(store, tmp_path, FakeService(25), second, day=2)
    assert "0 alert(s)" in second.discord[0]


def test_run_no_notify_keeps_listing_drop_for_real_run(store: Store, tmp_path: Path) -> None:
    wl = config.Watchlist(items=[WatchItem("duster", "air duster", sources=["amazon"])],
                          notify=config.NotifyConfig(discord_target="123", toast=False))
    _run(store, tmp_path, FakeService(50), FakeNotifier(ok=True), day=1, wl=wl)
    _run(store, tmp_path, FakeService(35), FakeNotifier(ok=True), notify=False, day=2, wl=wl)
    real = FakeNotifier(ok=True)
    _run(store, tmp_path, FakeService(35), real, day=3, wl=wl)
    assert "Price cut" in real.discord[0]


def test_run_all_sources_down_is_failed(store: Store, tmp_path: Path) -> None:
    status = _run(store, tmp_path, FakeService(0, errors={"amazon": "blocked"}), FakeNotifier(ok=True))
    assert status == "failed"


def test_run_toast_only_setup_counts_toast_as_delivery(store: Store, tmp_path: Path) -> None:
    wl = config.Watchlist(items=[WatchItem("duster", "air duster", target_price=30, sources=["amazon"])],
                          notify=config.NotifyConfig(discord_target=None, toast=True))
    first = FakeNotifier(ok=True)
    _run(store, tmp_path, FakeService(25), first, day=1, wl=wl)
    second = FakeNotifier(ok=True)
    _run(store, tmp_path, FakeService(25), second, day=2, wl=wl)
    assert (len(first.toasts), second.toasts) == (1, [])


def test_official_ebay_replaces_public_scrape_when_configured() -> None:
    from price_watch.fetch import resolve_sources
    assert resolve_sources(["amazon", "ebay_public"], {"amazon", "ebay", "ebay_public"}) == ["amazon", "ebay"]


def test_unconfigured_sources_are_skipped() -> None:
    from price_watch.fetch import resolve_sources
    assert resolve_sources(["ebay_public", "serpapi_google_shopping"], {"ebay_public"}) == ["ebay_public"]


# --- distance, geocoding, Amazon cheapest-first -----------------------------------
def test_item_max_miles_overrides_default() -> None:
    miles = {"San Jose, California": 6.0, "Sacramento, California": 20.0}
    res = asyncio.run(fetch_item(FakeFB(), WatchItem("d", "air duster"), Defaults(max_miles=25), distance=miles.get))
    assert [o.listing_id for o in res.observations] == ["local", "far", "ships"]


def test_normalize_place_handles_each_source_format() -> None:
    from price_watch.geo import normalize_place
    assert [normalize_place(x) for x in ["San Jose, California", "Santa Clara, CA", "sfbay: sunset / parkside",
                                         "sfbay", "Shelton, Washington (ships)", None]] == [
        "san jose, california", "santa clara, ca", "sunset, ca", None, "shelton, washington", None]


@pytest.fixture(autouse=True)
def _home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOPPING_FACEBOOK_MARKETPLACE_LATITUDE", "37.40")
    monkeypatch.setenv("SHOPPING_FACEBOOK_MARKETPLACE_LONGITUDE", "-121.90")


def test_haversine_known_distance() -> None:
    from price_watch.geo import haversine_miles
    assert round(haversine_miles((37.40, -121.90), (37.6624, -121.8747))) == 18


def test_expand_vars_and_discord_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PRICEWATCH_X", "42")
    assert config.expand_vars("a-${PRICEWATCH_X}-b") == "a-42-b"
    monkeypatch.setenv(config.DISCORD_TARGET_ENV, "999")
    wl_path = tmp_path / "w.json"
    wl_path.write_text('{"notify": {"discord_target": null}, "items": []}', encoding="utf-8")
    wl = config.load_watchlist(wl_path)
    assert wl.notify.discord_target == "999"
    config.save_watchlist(wl, wl_path)
    assert '"discord_target": null' in wl_path.read_text(encoding="utf-8")


class FakeHttp:
    def __init__(self, rows: list):
        self.rows, self.calls = rows, 0

    def get(self, url: str, params: dict) -> "FakeHttp":
        self.calls += 1
        return self

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list:
        return self.rows


def test_geocoder_caches_hits(tmp_path: Path) -> None:
    import sqlite3
    from price_watch.geo import Geocoder
    http = FakeHttp([{"lat": "37.6624", "lon": "-121.8747"}])
    geo = Geocoder(sqlite3.connect(tmp_path / "g.db"), client=http)
    first, second = geo.distance_miles("Pleasanton, CA"), geo.distance_miles("Pleasanton, CA")
    assert (first, second, http.calls) == (18.2, 18.2, 1)


def test_geocoder_caches_misses_after_unbounded_retry(tmp_path: Path) -> None:
    import sqlite3
    from price_watch.geo import Geocoder
    http = FakeHttp([])
    geo = Geocoder(sqlite3.connect(tmp_path / "g.db"), client=http)
    first, second = geo.distance_miles("nowhere, CA"), geo.distance_miles("nowhere, CA")
    assert (first, second, http.calls) == (None, None, 2)


def test_cheapest_url_sorts_by_price_and_filters_band_in_cents() -> None:
    from price_watch.amazon_cheap import cheapest_url
    assert cheapest_url("power bank", 8, 45) == \
        "https://www.amazon.com/s?k=power+bank&s=price-asc-rank&rh=p_36%3A800-4500"


def test_parse_cards_reads_asin_price_and_rating() -> None:
    from price_watch.amazon_cheap import parse_cards
    html = ('<div data-component-type="s-search-result" data-asin="B0TEST0001"><h2><span>Slim Power Bank 10000mAh'
            '</span></h2><span class="a-price"><span class="a-offscreen">$9.99</span></span>'
            '<span class="a-icon-alt">4.3 out of 5 stars</span></div>')
    [listing] = parse_cards(html, 5)
    assert (listing.id, listing.price, listing.seller_rating, listing.url) == (
        "B0TEST0001", 9.99, 4.3, "https://www.amazon.com/dp/B0TEST0001")


class FakeAmazon:
    async def find_best_deals(self, query: str, **kw: object) -> dict:
        return {"best_deals": [{"listing": {"id": "B1", "source": "amazon", "title": "Power Bank", "url": "u1",
                                            "price": 17.0}}]}


def test_amazon_cheapest_pass_is_merged_and_deduped() -> None:
    async def cheapest(q: str, lo: object, hi: object, n: int) -> list[dict]:
        return [{"listing": {"id": "B2", "source": "amazon", "title": "Power Bank", "url": "u2", "price": 9.99}},
                {"listing": {"id": "B1", "source": "amazon", "title": "Power Bank", "url": "u1", "price": 17.0}}]
    res = asyncio.run(fetch_item(FakeAmazon(), WatchItem("p", "power bank", sources=["amazon"]), D,
                                 amazon_cheapest=cheapest))
    assert sorted(o.listing_id for o in res.observations) == ["B1", "B2"]


def test_amazon_cheapest_failure_keeps_featured_results() -> None:
    async def boom(q: str, lo: object, hi: object, n: int) -> list[dict]:
        raise RuntimeError("captcha")
    res = asyncio.run(fetch_item(FakeAmazon(), WatchItem("p", "power bank", sources=["amazon"]), D,
                                 amazon_cheapest=boom))
    assert ([o.listing_id for o in res.observations], list(res.source_errors)) == (["B1"], ["amazon_cheapest"])


def test_target_compares_sticker_price_before_tax() -> None:
    class Taxed:
        async def find_best_deals(self, query: str, **kw: object) -> dict:
            return {"best_deals": [{"listing": {"id": "B3", "source": "amazon", "title": "Power Bank", "url": "u",
                                                "price": 9.99, "total_with_tax": 10.93}}]}
    res = asyncio.run(fetch_item(Taxed(), WatchItem("p", "power bank", sources=["amazon"]), D))
    assert res.observations[0].price == 9.99


# --- review follow-ups: all local sources, geocoder outage, ratings, migration -----
class FakeLocal:
    async def find_best_deals(self, query: str, **kw: object) -> dict:
        mk = lambda i, src, loc: {"listing": {"id": i, "source": src, "title": "Power Bank", "url": i, "price": 10,
                                              "location": loc, "shipping": "Local pickup"}}
        return {"best_deals": [mk("cl", "craigslist", "sfbay: pleasanton"), mk("ou", "offerup", "Pleasanton, CA"),
                               mk("fb", "facebook_marketplace", "Milpitas, California")]}


def test_craigslist_and_offerup_pickups_are_distance_filtered_too() -> None:
    miles = {"sfbay: pleasanton": 16.0, "Pleasanton, CA": 16.0, "Milpitas, California": 1.0}
    res = asyncio.run(fetch_item(FakeLocal(), WatchItem("p", "power bank", max_miles=10), D, distance=miles.get))
    assert [o.listing_id for o in res.observations] == ["fb"]


def test_non_place_locations_are_not_geocoded() -> None:
    from price_watch.geo import normalize_place
    assert [normalize_place(x) for x in ["Remote", "Local pickup", "sfbay: downtown / civic / van ness"]] == [
        None, None, None]


class BrokenHttp:
    def __init__(self):
        self.calls = 0

    def get(self, url: str, params: dict) -> None:
        import httpx
        self.calls += 1
        raise httpx.ConnectTimeout("down")


def test_geocoder_outage_is_not_cached_and_trips_breaker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3
    from price_watch import geo as geo_mod
    monkeypatch.setattr(geo_mod.time, "sleep", lambda s: None)
    http = BrokenHttp()
    g = geo_mod.Geocoder(sqlite3.connect(tmp_path / "g.db"), client=http)
    results = [g.distance_miles(p) for p in ["a, ca", "a, ca", "b, ca", "c, ca", "d, ca", "e, ca"]]
    cached = g.conn.execute("SELECT COUNT(*) FROM geocache").fetchone()[0]
    assert (results, http.calls, cached, g.error is not None) == ([None] * 6, 3, 0, True)


def test_geocoder_waits_between_requests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3
    from price_watch import geo as geo_mod
    sleeps: list[float] = []
    monkeypatch.setattr(geo_mod.time, "sleep", sleeps.append)
    g = geo_mod.Geocoder(sqlite3.connect(tmp_path / "g.db"), client=FakeHttp([{"lat": "37.5", "lon": "-121.9"}]))
    g.distance_miles("x, ca")
    g.distance_miles("y, ca")
    assert len(sleeps) == 1 and sleeps[0] > 1.0


def test_ebay_feedback_percent_is_not_shown_as_stars() -> None:
    class Ebay:
        async def find_best_deals(self, query: str, **kw: object) -> dict:
            return {"best_deals": [{"listing": {"id": "e1", "source": "ebay", "title": "Power Bank", "url": "u",
                                                "price": 9.0, "seller_rating": 99.8}}]}
    res = asyncio.run(fetch_item(Ebay(), WatchItem("p", "power bank", sources=["ebay"]), D))
    assert res.observations[0].rating is None


def test_report_shows_distance_and_stars(store: Store) -> None:
    item = WatchItem("duster", "q")
    o = obs("a", 20)
    o.distance_mi, o.rating = 6.4, 4.5
    s = run_day(store, item, date(2026, 10, 1), [o])
    assert "| 6 mi | 4.5 |" in report.render([s], datetime(2026, 10, 1, 8), 5)


def test_old_tax_inclusive_amazon_rows_migrate_to_sticker(tmp_path: Path) -> None:
    import sqlite3
    db = tmp_path / "m.db"
    with Store(db) as s:
        s.conn.execute("DELETE FROM meta")
        run = s.start_run()
        s.add_observations(run, [Observation("p", "amazon", "B1", "t", "u", 10.93, 9.99, "new", None)],
                           datetime(2026, 10, 5, 8))
        s.record_alert("p", "amazon:B1", 10.93, "target_hit")
        s.conn.commit()
    with Store(db) as s:
        prices = (s.conn.execute("SELECT price FROM observations").fetchone()[0],
                  s.conn.execute("SELECT price FROM alerts_sent").fetchone()[0])
    assert prices == (9.99, 9.99)


def test_amazon_bot_challenge_page_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from price_watch import amazon_cheap
    from shopping_deals_mcp.sources import amazon as amazon_src

    async def captcha(url: str) -> str:
        return "<html><body>Enter the characters you see below</body></html>"
    monkeypatch.setattr(amazon_src, "_fetch_with_browser", captcha)
    with pytest.raises(RuntimeError, match="no result cards"):
        asyncio.run(amazon_cheap.fetch_cheapest("power bank", 8, 45, 5))
