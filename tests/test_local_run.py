"""Frequent local runs (pricewatch run --local): pickup sources only, Discord only for new good deals."""
from __future__ import annotations

from pathlib import Path

from price_watch import config, report, vet
from price_watch.__main__ import RUN_LOCK_STALE_S, _deliver, acquire_run_lock, local_only
from price_watch.analyze import Alert, ItemSummary
from price_watch.config import WatchItem
from price_watch.fetch import FetchResult, Observation

DEFAULTS = ["amazon", "craigslist", "offerup", "facebook_marketplace"]


class Notifier:
    def __init__(self) -> None:
        self.discord: list[str] = []

    def send_discord(self, target: str, message: str) -> bool:
        self.discord.append(message)
        return True

    def send_toast(self, title: str, body: str) -> bool:
        return True


def _o(key: str, title: str) -> Observation:
    return Observation("cam", "craigslist", key, title, f"https://x/{key}", 20, 20, "used", "Milpitas", distance_mi=2)


def _summary(alerted: bool) -> ItemSummary:
    o, other = _o("a", "Logitech C920"), _o("b", "Logitech C922")
    s = ItemSummary(WatchItem("cam", "webcam", target_price=25, requirements="r"),
                    FetchResult("cam", [o, other], {}, 2, {}), None, None, 0, None,
                    alerts=[Alert("cam", "target_hit", o, None, "m")] if alerted else [])
    s.vettings = {"craigslist:a": vet.Vetting("ok", "s"), "craigslist:b": vet.Vetting("ok", "s")}
    return s


def _wl() -> config.Watchlist:
    return config.Watchlist(items=[], notify=config.NotifyConfig(discord_target="123", toast=False,
                                                                 discord_digest_always=True))


def test_local_only_keeps_just_pickup_sources() -> None:
    assert local_only([WatchItem("cam", "webcam")], DEFAULTS)[0].sources == ["craigslist", "offerup",
                                                                             "facebook_marketplace"]


def test_local_only_drops_item_with_no_pickup_source() -> None:
    assert local_only([WatchItem("cam", "webcam", sources=["amazon"])], DEFAULTS) == []


def test_local_only_does_not_change_the_watchlist_item() -> None:
    item = WatchItem("cam", "webcam")
    local_only([item], DEFAULTS)
    assert item.sources is None


def test_alerts_only_digest_lists_only_alerted_listing() -> None:
    text = report.digest([_summary(alerted=True)], Path("r.md"), alerts_only=True)
    assert "Logitech C920" in text and "Logitech C922" not in text


def test_alerts_only_digest_shows_every_alert_of_an_item() -> None:
    obs = [_o(k, f"Cam {k}") for k in ("a", "b", "c", "d")]
    s = ItemSummary(WatchItem("cam", "webcam", target_price=25, requirements="r"),
                    FetchResult("cam", obs, {}, 4, {}), None, None, 0, None,
                    alerts=[Alert("cam", "target_hit", o, None, "m") for o in obs[1:]])
    s.vettings = {f"craigslist:{k}": vet.Vetting("ok", "s") for k in "abcd"}
    assert report.digest([s], Path("r.md"), alerts_only=True).count("TARGET") == 3


def test_alert_on_unvetted_listing_still_shown_as_unchecked() -> None:
    s = _summary(alerted=True)
    s.vettings = {"craigslist:b": vet.Vetting("ok", "s")}  # vetting budget ran out before listing a
    assert "unchecked · CL 2 mi · [Logitech C920]" in report.digest([s], Path("r.md"), alerts_only=True)


def test_alerts_only_digest_empty_without_alerts() -> None:
    assert report.digest([_summary(alerted=False)], Path("r.md"), alerts_only=True) == ""


def test_local_run_without_alerts_sends_no_digest() -> None:
    n = Notifier()
    _deliver(_wl(), [_summary(alerted=False)], [], Path("r.md"), n, alerts_only=True)
    assert n.discord == []


def test_run_lock_free_is_acquired(tmp_path: Path) -> None:
    assert acquire_run_lock(tmp_path / "run.lock") is True


def test_run_lock_held_blocks_second_run(tmp_path: Path) -> None:
    acquire_run_lock(tmp_path / "run.lock")
    assert acquire_run_lock(tmp_path / "run.lock") is False


def test_stale_run_lock_is_taken_over(tmp_path: Path) -> None:
    lock = tmp_path / "run.lock"
    acquire_run_lock(lock)
    assert acquire_run_lock(lock, now=lock.stat().st_mtime + RUN_LOCK_STALE_S + 1) is True


def test_local_run_with_alert_sends_one_message() -> None:
    n = Notifier()
    s = _summary(alerted=True)
    _deliver(_wl(), [s], s.alerts, Path("r.md"), n, alerts_only=True)
    assert len(n.discord) == 1
