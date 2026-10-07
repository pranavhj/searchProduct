"""Quality vetting (phase 2): gateway reply parsing, verdict cache, evidence parsers, run wiring."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from price_watch import amazon_detail, gateway, vet, websearch
from price_watch.config import WatchItem
from price_watch.fetch import Observation
from price_watch.store import Store
from test_price_watch import D, FakeNotifier, FakeService, _wl, obs

AVOID = ('{"verdict": "avoid", "summary": "no-name, capacity overstated", "red_flags": ["light for 10k"], '
         '"standard_product": "Anker 10k", "standard_price": "$18.99", "vs_standard": "worse cells", '
         '"better_pick": ""}')
BUY = AVOID.replace('"avoid"', '"buy"')


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "t.db") as s:
        yield s


def no_web(query: str, limit: int, store: object, max_age_days: int) -> list:
    return []


class FakeRunner:
    def __init__(self, reply: str | Exception):
        self.reply, self.prompts = reply, []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _vet(store: Store, runner: FakeRunner, *o: Observation) -> dict:
    item = WatchItem("duster", "air duster", requirements="quiet, USB-C")
    return asyncio.run(vet.vet_listings(store, item, list(o), list(o), D, runner, web=no_web))


def test_gateway_reply_json_wrapped_in_prose_is_parsed() -> None:
    assert gateway.parse_json_reply('Sure:\n```json\n{"verdict": "ok"}\n```') == {"verdict": "ok"}


def test_gateway_reply_without_json_raises() -> None:
    with pytest.raises(ValueError):
        gateway.parse_json_reply("I cannot help")


def test_vetting_rejects_unknown_verdict() -> None:
    with pytest.raises(ValueError):
        vet.Vetting.from_json({"verdict": "maybe", "summary": "x"})


def test_vet_verdict_parsed_from_runner_reply(store: Store) -> None:
    assert _vet(store, FakeRunner(AVOID), obs("a", 9.49))["ebay_public:a"].verdict == "avoid"


def test_vet_prompt_carries_requirements(store: Store) -> None:
    runner = FakeRunner(AVOID)
    _vet(store, runner, obs("a", 9.49))
    assert "Requirements: quiet, USB-C" in runner.prompts[0]


def test_vet_same_listing_same_price_is_cached(store: Store) -> None:
    _vet(store, FakeRunner(AVOID), obs("a", 9.49))
    second = FakeRunner(BUY)
    _vet(store, second, obs("a", 9.49))
    assert second.prompts == []


def test_vet_price_change_triggers_fresh_vetting(store: Store) -> None:
    _vet(store, FakeRunner(AVOID), obs("a", 9.49))
    assert _vet(store, FakeRunner(BUY), obs("a", 8.99))["ebay_public:a"].verdict == "buy"


def test_vet_runner_failure_marks_unvetted(store: Store) -> None:
    assert _vet(store, FakeRunner(gateway.GatewayError("down")), obs("a", 9.49))["ebay_public:a"].verdict == "unvetted"


def test_vet_runner_failure_is_not_cached(store: Store) -> None:
    _vet(store, FakeRunner(gateway.GatewayError("down")), obs("a", 9.49))
    assert store.get_vetting("duster", "ebay_public:a", 9.49, 7) is None


def test_brand_from_product_page_wins_over_title() -> None:
    assert vet.brand_of(obs("a", 9.0), {"brand": "INIU"}) == "INIU"


def test_parse_detail_strips_store_byline() -> None:
    html = '<a id="bylineInfo">Visit the Aaoyun Store</a><span id="acrCustomerReviewText">(786)</span>'
    assert amazon_detail.parse_detail(html) == {"brand": "Aaoyun", "review_count": "786"}


def test_websearch_parses_title_url_snippet() -> None:
    html = ('<div class="result"><a class="result__a" href="https://r/x">Aaoyun review</a>'
            '<a class="result__snippet">works fine</a></div>')
    assert websearch.parse_results(html, 5) == [
        {"title": "Aaoyun review", "url": "https://r/x", "snippet": "works fine"}]


def _fake_vetter(verdict: str):
    async def vetter(item: WatchItem, to_vet: list, peers: list) -> dict:
        return {o.key: vet.Vetting(verdict, f"{verdict} because") for o in to_vet}
    return vetter


def _run_vetted(store: Store, tmp_path: Path, notifier: FakeNotifier, verdict: str) -> None:
    from price_watch.__main__ import run_items
    wl = _wl()
    asyncio.run(run_items(store, FakeService(25), wl, wl.items, True, notifier, tmp_path,
                          datetime(2026, 10, 1, 8).astimezone(), vetter=_fake_vetter(verdict)))


def test_run_withholds_alert_on_listing_vetted_avoid(store: Store, tmp_path: Path) -> None:
    notifier = FakeNotifier(ok=True)
    _run_vetted(store, tmp_path, notifier, "avoid")
    assert "withheld" in notifier.discord[0]


def test_run_alert_line_shows_buy_verdict(store: Store, tmp_path: Path) -> None:
    notifier = FakeNotifier(ok=True)
    _run_vetted(store, tmp_path, notifier, "buy")
    assert "✅ buy: buy because" in notifier.discord[0]


def test_report_shows_vetting_section(store: Store, tmp_path: Path) -> None:
    _run_vetted(store, tmp_path, FakeNotifier(ok=True), "buy")
    assert "**Vetting**" in (tmp_path / "latest.md").read_text(encoding="utf-8")
