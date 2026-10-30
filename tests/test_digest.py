"""Discord digest: a table of the best good (buy/ok) listing per item; bad listings never appear."""
from __future__ import annotations

from pathlib import Path

from price_watch import report, vet
from price_watch.analyze import Alert, ItemSummary
from price_watch.cheap_flags import Vet
from price_watch.config import WatchItem
from price_watch.fetch import FetchResult, Observation


def _o(key: str, price: float, title: str) -> Observation:
    return Observation("cam", "facebook_marketplace", key, title, f"https://x/{key}", price, price, "used",
                       "San Jose", distance_mi=7)


def _summary(observations: list[Observation], verdicts: dict[str, str],
             alerts: list[Alert] | None = None) -> ItemSummary:
    item = WatchItem("cam", "webcam", target_price=25, requirements="1080p")
    s = ItemSummary(item, FetchResult("cam", observations, {}, len(observations), {}), None, None, 0, None,
                    alerts=alerts or [])
    s.vettings = {f"facebook_marketplace:{k}": vet.Vetting(v, "s") for k, v in verdicts.items()}
    return s


def test_avoid_and_unvetted_listings_never_shown() -> None:
    s = _summary([_o("a", 5, "GPU bracket"), _o("b", 9, "mystery cam"), _o("c", 20, "Logitech C920")],
                 {"a": "avoid", "c": "ok"})
    text = report.digest([s], Path("r.md"))
    assert "GPU bracket" not in text and "mystery cam" not in text
    assert "Logitech C920 <https://x/c>" in text and "$20.00" in text


def test_trap_flag_hides_listing_even_if_vetted_ok() -> None:
    o = _o("a", 5, "too cheap cam")
    o.cheap = Vet(verdict="probably a trap")
    assert "too cheap cam" not in report.digest([_summary([o], {"a": "ok"})], Path("r.md"))


def test_item_without_good_listing_is_one_line() -> None:
    text = report.digest([_summary([_o("a", 5, "junk")], {"a": "avoid"})], Path("r.md"))
    assert "No good deal yet: cam" in text and "```" not in text


def test_alerted_pick_marked_target() -> None:
    o = _o("c", 20, "Logitech C920")
    s = _summary([o], {"c": "buy"}, alerts=[Alert("cam", "target_hit", o, None, "m")])
    row = [line for line in report.digest([s], Path("r.md")).splitlines() if line.startswith(" 1 cam")][0]
    assert row.endswith("TARGET")


TABLE = "head\n```\nrow 1 aaaaaaaa\nrow 2 bbbbbbbb\nrow 3 cccccccc\n```\nlink 1"


def test_short_digest_is_one_chunk() -> None:
    assert report.discord_chunks(TABLE) == [TABLE]


def test_split_inside_table_closes_and_reopens_fence() -> None:
    assert report.discord_chunks(TABLE, limit=45) == ["head\n```\nrow 1 aaaaaaaa\nrow 2 bbbbbbbb\n```",
                                                      "```\nrow 3 cccccccc\n```\nlink 1"]


def test_split_just_before_closing_fence_leaves_no_empty_block() -> None:
    assert "```\n```" not in "".join(report.discord_chunks(TABLE, limit=37))


def test_every_chunk_within_limit() -> None:
    assert max(len(c) for c in report.discord_chunks(TABLE, limit=30)) <= 30
