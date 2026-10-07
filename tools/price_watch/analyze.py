"""Turn today's observations + history into per-item summaries and alerts."""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from price_watch.config import Defaults, WatchItem
from price_watch.fetch import FetchResult, Observation
from price_watch.store import Store

log = logging.getLogger("price_watch.analyze")

MIN_BASELINE_DAYS = 3  # baseline (median of daily bests) needs this many earlier days
MAX_TARGET_ALERTS_PER_ITEM = 3


@dataclass
class Alert:
    item_id: str
    kind: str  # target_hit | big_drop | listing_drop
    obs: Observation
    old_price: float | None
    message: str


@dataclass
class ItemSummary:
    item: WatchItem
    fetch: FetchResult
    best: Observation | None
    baseline: float | None
    baseline_days: int
    prev_best: float | None  # best on the most recent earlier day
    new_listings: list[Observation] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    first_run: bool = False
    vettings: dict = field(default_factory=dict)  # listing key -> vet.Vetting
    skipped_alerts: list[Alert] = field(default_factory=list)  # alerts withheld: vetting said "avoid"

    def best_vetted(self) -> Observation | None:
        """Cheapest listing not vetted 'avoid' (unvetted counts as not-avoid)."""
        ok = [o for o in self.fetch.observations
              if getattr(self.vettings.get(o.key), "verdict", None) != "avoid"]
        return min(ok, key=lambda o: o.price) if ok else None

    @property
    def change_pct(self) -> float | None:
        if self.best is None or not self.prev_best:
            return None
        return (self.best.price - self.prev_best) / self.prev_best * 100


def _pct_below(new: float, old: float) -> float:
    return (old - new) / old * 100 if old else 0.0


def analyze_item(
    store: Store, run_id: int, item: WatchItem, fetch: FetchResult, defaults: Defaults, today: date | None = None
) -> ItemSummary:
    today = today or date.today()
    drop_pct = item.drop_pct if item.drop_pct is not None else defaults.drop_pct
    obs = sorted(fetch.observations, key=lambda o: o.price)
    best = obs[0] if obs else None

    since = (today - timedelta(days=defaults.baseline_days)).isoformat()
    bests = [(d, p) for d, p in store.daily_bests(item.id, since, run_id) if d < today.isoformat()]
    baseline = statistics.median(p for _, p in bests) if len(bests) >= MIN_BASELINE_DAYS else None
    prev_best = bests[-1][1] if bests else None
    previous = store.previous_prices(item.id, run_id)
    first_run = not store.has_history(item.id, run_id)

    summary = ItemSummary(item, fetch, best, baseline, len(bests), prev_best, first_run=first_run)
    if not first_run:
        summary.new_listings = [o for o in obs if o.key not in previous]

    alerts: list[Alert] = []
    # (0) alerts from earlier runs that were never delivered, while the listing is still that cheap
    pending = store.pending(item.id)
    for o in obs:
        pend = pending.get(o.key)
        if pend and o.price <= pend[0]:
            alerts.append(Alert(item.id, pend[1], o, pend[2], pend[3] + " (retry)"))
    # (a) at/below the user's target price
    if item.target_price is not None:
        for o in [o for o in obs if o.price <= item.target_price]:
            alerts.append(Alert(item.id, "target_hit", o, item.target_price,
                                f"${o.price:.2f} ≤ target ${item.target_price:.2f}"))
    # (b) today's best far below the trailing median of daily bests
    if best is not None and baseline is not None and _pct_below(best.price, baseline) >= drop_pct:
        alerts.append(Alert(item.id, "big_drop", best, baseline,
                            f"${best.price:.2f} is {_pct_below(best.price, baseline):.0f}% below "
                            f"{len(bests)}-day median ${baseline:.2f}"))
    # (c) a listing we saw before cut its own price
    for o in obs:
        old = previous.get(o.key)
        if old is not None and _pct_below(o.price, old) >= drop_pct:
            alerts.append(Alert(item.id, "listing_drop", o, old,
                                f"listing cut ${old:.2f} → ${o.price:.2f} (-{_pct_below(o.price, old):.0f}%)"))

    # One alert per listing (first = highest priority), skip already-sent, then cap target hits.
    seen: set[str] = set()
    target_hits = 0
    for a in alerts:
        if a.obs.key in seen:
            continue
        seen.add(a.obs.key)
        if store.alert_sent(item.id, a.obs.key, a.obs.price):
            log.debug("item=%s alert suppressed (already sent) key=%s price=%.2f", item.id, a.obs.key, a.obs.price)
            continue
        if a.kind == "target_hit":
            target_hits += 1
            if target_hits > MAX_TARGET_ALERTS_PER_ITEM:
                continue
        summary.alerts.append(a)

    log.info(
        "item=%s best=%s baseline=%s days=%d prev=%s new=%d alerts=%d",
        item.id, best.price if best else None, baseline, len(bests), prev_best,
        len(summary.new_listings), len(summary.alerts),
    )
    return summary
