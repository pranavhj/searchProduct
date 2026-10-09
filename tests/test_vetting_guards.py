"""Vetting guard rails (review findings 2026-10-07): withheld alerts, alert cap, failures, caches, gateway client."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from price_watch import gateway, vet
from price_watch.config import WatchItem
from price_watch.store import Store
from test_price_watch import D, FakeNotifier, FakeService, _wl, obs
from test_vetting import AVOID, BUY, FakeRunner, no_web


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "t.db") as s:
        yield s


class FakeMulti:
    """Four Amazon listings, all under the $30 target."""

    async def find_best_deals(self, query: str, **kw: object) -> dict:
        return {"best_deals": [{"listing": {"id": f"l{n}", "source": "amazon", "title": f"Air Duster {n}",
                                            "url": f"u{n}", "price": 20 + n}} for n in range(4)]}


def _vetter(verdicts: dict[str, str]):
    async def vetter(item: WatchItem, to_vet: list, peers: list) -> dict:
        return {o.key: vet.Vetting(verdicts.get(o.key, "buy"), "because") for o in to_vet}
    return vetter


def _broken_vetter():
    async def vetter(item: WatchItem, to_vet: list, peers: list) -> dict:
        raise RuntimeError("gateway exploded")
    return vetter


def _run(store: Store, tmp_path: Path, notifier: FakeNotifier, service: object, vetter: object,
         notify: bool = True) -> None:
    from price_watch.__main__ import run_items
    wl = _wl()
    asyncio.run(run_items(store, service, wl, wl.items, notify, notifier, tmp_path,
                          datetime(2026, 10, 1, 8).astimezone(), vetter=vetter))


# --- withheld alerts ---------------------------------------------------------------
def test_avoid_alert_line_not_in_discord_message(store: Store, tmp_path: Path) -> None:
    notifier = FakeNotifier(ok=True)
    _run(store, tmp_path, notifier, FakeService(25), _vetter({"amazon:a": "avoid"}))
    assert "🎯 Target hit" not in notifier.discord[0]


def test_avoid_alert_not_recorded_as_sent(store: Store, tmp_path: Path) -> None:
    _run(store, tmp_path, FakeNotifier(ok=True), FakeService(25), _vetter({"amazon:a": "avoid"}))
    assert store.alert_sent("duster", "amazon:a", 25) is False


def test_avoid_alert_not_saved_pending(store: Store, tmp_path: Path) -> None:
    _run(store, tmp_path, FakeNotifier(ok=True), FakeService(25), _vetter({"amazon:a": "avoid"}), notify=False)
    assert store.pending("duster") == {}


def test_avoid_listings_do_not_use_up_target_alert_cap(store: Store, tmp_path: Path) -> None:
    notifier = FakeNotifier(ok=True)
    avoid3 = {"amazon:l0": "avoid", "amazon:l1": "avoid", "amazon:l2": "avoid"}
    _run(store, tmp_path, notifier, FakeMulti(), _vetter(avoid3))
    assert "Air Duster 3 (amazon" in notifier.discord[0]  # alert-line format, not the "best" line


def test_target_cap_still_applies_after_vetting(store: Store, tmp_path: Path) -> None:
    notifier = FakeNotifier(ok=True)
    _run(store, tmp_path, notifier, FakeMulti(), _vetter({}))
    assert notifier.discord[0].count("🎯 Target hit") == 3


def test_vetter_crash_still_sends_alert(store: Store, tmp_path: Path) -> None:
    notifier = FakeNotifier(ok=True)
    _run(store, tmp_path, notifier, FakeService(25), _broken_vetter())
    assert "🎯 Target hit" in notifier.discord[0]


def test_vetter_crash_still_writes_report(store: Store, tmp_path: Path) -> None:
    _run(store, tmp_path, FakeNotifier(ok=True), FakeService(25), _broken_vetter())
    assert (tmp_path / "latest.md").exists()


# --- vet_listings guards -------------------------------------------------------------
def _vet(store: Store, runner: FakeRunner, item: WatchItem, *o, budget: vet.Budget | None = None) -> dict:
    return asyncio.run(vet.vet_listings(store, item, list(o), list(o), D, runner, web=no_web, budget=budget))


ITEM = WatchItem("duster", "air duster", requirements="quiet")


def test_two_gateway_failures_stop_further_calls(store: Store) -> None:
    runner = FakeRunner(gateway.GatewayError("down"))
    _vet(store, runner, ITEM, obs("a", 1), obs("b", 2), obs("c", 3))
    assert len(runner.prompts) == 2


def test_listing_after_budget_stop_is_unvetted(store: Store) -> None:
    out = _vet(store, FakeRunner(gateway.GatewayError("down")), ITEM, obs("a", 1), obs("b", 2), obs("c", 3))
    assert out["ebay_public:c"].verdict == "unvetted"


def test_expired_time_budget_makes_no_calls(store: Store) -> None:
    runner = FakeRunner(BUY)
    _vet(store, runner, ITEM, obs("a", 1), budget=vet.Budget(minutes=0))
    assert runner.prompts == []


def test_changed_requirements_invalidate_cached_verdict(store: Store) -> None:
    _vet(store, FakeRunner(AVOID), ITEM, obs("a", 1))
    runner = FakeRunner(BUY)
    _vet(store, runner, WatchItem("duster", "air duster", requirements="loud is fine"), obs("a", 1))
    assert len(runner.prompts) == 1


def test_bad_reference_row_does_not_lose_vetting(store: Store) -> None:
    async def bad_reference(query: str) -> list:
        return [{"price": 5.0}]  # no title / url
    item = WatchItem("duster", "air duster", reference_queries=["x"])
    out = asyncio.run(vet.vet_listings(store, item, [obs("a", 1)], [obs("a", 1)], D, FakeRunner(BUY),
                                       reference=bad_reference, web=no_web))
    assert out["ebay_public:a"].verdict == "buy"


def test_prompt_is_capped_for_windows_command_line() -> None:
    item = WatchItem("duster", "air duster", requirements="x" * 50000)
    assert len(vet.build_prompt(item, obs("a", 1), {}, [], [], vet.ItemEvidence())) == vet.MAX_PROMPT_CHARS


# --- cache expiry -------------------------------------------------------------------
def test_vetting_older_than_cache_days_is_ignored(store: Store) -> None:
    store.put_vetting("duster", "k", 9.0, "buy", "{}")
    store.conn.execute("UPDATE vettings SET vetted_at='2026-01-01T00:00:00+00:00'")
    assert store.get_vetting("duster", "k", 9.0, 7) is None


def test_web_cache_older_than_max_age_is_ignored(store: Store) -> None:
    store.put_web("q", "[]")
    store.conn.execute("UPDATE web_cache SET fetched_at='2026-01-01T00:00:00+00:00'")
    assert store.get_web("q", 7) is None


# --- gateway client -------------------------------------------------------------------
class FakeResponse:
    def __init__(self, status: int, data: dict):
        self.status_code, self._data, self.text = status, data, str(data)

    def json(self) -> dict:
        return self._data


class FakePost:
    def __init__(self, *responses: object):
        self.responses, self.bodies = list(responses), []

    def __call__(self, url: str, json: dict, headers: dict, timeout: float) -> FakeResponse:
        self.bodies.append(json)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def fake_gateway(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gateway, "_token", lambda: "t")
    monkeypatch.setattr(gateway.time, "sleep", lambda s: None)

    def install(*responses: object) -> FakePost:
        fake = FakePost(*responses)
        monkeypatch.setattr(gateway.httpx, "post", fake)
        return fake
    return install


def test_gateway_requests_fresh_conversation(fake_gateway) -> None:
    fake = fake_gateway(FakeResponse(200, {"status": "ok", "response": "{}"}))
    gateway.ask("p", "m")
    assert fake.bodies[0]["fresh"] is True


def test_gateway_busy_then_ok_retries(fake_gateway) -> None:
    fake_gateway(FakeResponse(409, {"retry_after_ms": 10}), FakeResponse(200, {"status": "ok", "response": "hi"}))
    assert gateway.ask("p", "m") == "hi"


def test_gateway_http_error_raises(fake_gateway) -> None:
    fake_gateway(FakeResponse(504, {"status": "error"}))
    with pytest.raises(gateway.GatewayError):
        gateway.ask("p", "m")


def test_gateway_unreachable_raises(fake_gateway) -> None:
    fake_gateway(httpx.ConnectError("refused"))
    with pytest.raises(gateway.GatewayError):
        gateway.ask("p", "m")
