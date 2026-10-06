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
    assert res.observations[0].price == 21.88
    assert res.dropped == {"excluded_keyword": 1, "no_price": 1}
    assert res.source_errors == {"offerup": "boom"}


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


def test_fb_pickup_outside_bay_area_dropped_but_shippable_kept() -> None:
    res = asyncio.run(fetch_item(FakeFB(), WatchItem("d", "air duster"), D))
    assert [(o.listing_id, o.location) for o in res.observations] == [
        ("local", "San Jose, California"), ("ships", "Shelton, Washington (ships)")]


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


def test_locality_matches_user_cities_case_insensitively() -> None:
    from price_watch.fetch import locality_reason
    listing = {"source": "facebook_marketplace", "location": "Santa Cruz, CA", "shipping": "Local pickup"}
    assert locality_reason(listing, ["Santa Cruz"]) is None


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
