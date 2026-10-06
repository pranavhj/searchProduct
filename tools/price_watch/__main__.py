"""Price watch CLI.

  pricewatch run [--item ID ...] [--no-notify]   search every enabled item, report, alert
  pricewatch list                                show the watchlist
  pricewatch add "query" [--target 30] [--include "a|b"] [--exclude x] ...
  pricewatch remove ID | enable ID | disable ID
  pricewatch history ID [--days 30]              best price per day
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from price_watch import config
from price_watch.config import WatchItem

log = logging.getLogger("price_watch")


def _setup_logging(verbose: bool) -> None:
    (config.DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_h = RotatingFileHandler(config.DATA_DIR / "logs" / "price_watch.log", maxBytes=2_000_000,
                                 backupCount=5, encoding="utf-8")
    file_h.setFormatter(fmt)
    file_h.setLevel(logging.DEBUG)
    err_h = logging.StreamHandler(sys.stderr)
    err_h.setFormatter(fmt)
    err_h.setLevel(logging.DEBUG if verbose else logging.INFO)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers = [file_h, err_h]
    for noisy in ("httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _deliver(wl: config.Watchlist, summaries: list, alerts: list, path: Path, notifier: Any) -> bool:
    """Send notifications; True if alerts count as delivered.

    Discord (full links) is the primary channel when configured; the toast only carries prices,
    so it counts as delivery only when Discord is not set up. No channel at all = report-only setup.
    """
    from price_watch import report

    discord_ok = None
    if wl.notify.discord_target and (alerts or wl.notify.discord_digest_always):
        discord_ok = notifier.send_discord(wl.notify.discord_target, report.digest(summaries, path))
    toast_ok = None
    if wl.notify.toast and alerts:
        body = "; ".join(f"{a.item_id}: ${a.obs.price:.2f}" for a in alerts)
        toast_ok = notifier.send_toast(f"Price watch: {len(alerts)} alert(s)", body)
    if wl.notify.discord_target:
        return bool(discord_ok) or not alerts
    if wl.notify.toast:
        return bool(toast_ok) or not alerts
    return True


async def run_items(store: Any, service: Any, wl: config.Watchlist, items: list[WatchItem], notify: bool,
                    notifier: Any, reports_dir: Path, run_at: datetime | None = None) -> tuple[str, Path | None]:
    """One full run. Returns (status, report path). Testable with fake service/notifier/store."""
    from price_watch import analyze, report
    from price_watch.fetch import FetchResult, fetch_item, resolve_sources

    run_at = run_at or datetime.now().astimezone()
    run_id = store.start_run()
    log.info("run %d started: %d items", run_id, len(items))
    failures, alerts, path, status = 0, [], None, "failed"
    available = ({st.name for st in service.source_statuses() if st.available}
                 if hasattr(service, "source_statuses") else None)
    log.info("available sources: %s", sorted(available) if available is not None else "unknown")
    try:
        summaries = []
        for item in items:
            try:
                sources = resolve_sources(item.sources or wl.defaults.sources, available)
                fetched = await fetch_item(service, item, wl.defaults, sources)
            except Exception as exc:  # one broken item must not kill the daily run
                log.exception("item=%s fetch failed", item.id)
                fetched = FetchResult(item.id, [], {"fetch": f"{type(exc).__name__}: {exc}"}, 0, {})
            requested = set(resolve_sources(item.sources or wl.defaults.sources, available))
            if not fetched.observations and fetched.source_errors and (
                    "fetch" in fetched.source_errors or requested <= set(fetched.source_errors)):
                failures += 1
                log.error("item=%s every source failed: %s", item.id, fetched.source_errors)
            try:
                summary = analyze.analyze_item(store, run_id, item, fetched, wl.defaults, today=run_at.date())
                store.add_observations(run_id, fetched.observations, run_at)
            except Exception:
                log.exception("item=%s analyze/store failed", item.id)
                failures += 1
                continue
            summaries.append(summary)

        path = report.write(report.render(summaries, run_at, wl.defaults.top_n_report), reports_dir, run_at)
        alerts = [a for s in summaries for a in s.alerts]
        delivered = notify and _deliver(wl, summaries, alerts, path, notifier)
        for a in alerts:
            if delivered:
                store.record_alert(a.item_id, a.obs.key, a.obs.price, a.kind)
                store.clear_pending(a.item_id, a.obs.key)
            else:  # --no-notify or delivery failed: retry next run while the listing is still this cheap
                store.save_pending(a.item_id, a.obs.key, a.obs.price, a.kind, a.old_price, a.message)
        if alerts and not delivered:
            log.warning("%d alert(s) not delivered (notify=%s); kept pending for next run", len(alerts), notify)
        status = "ok" if failures == 0 else ("failed" if failures >= len(items) else "partial")
    finally:
        summary_text = f"{len(items)} items, {len(alerts)} alerts, {failures} failures, report {path.name if path else '-'}"
        store.finish_run(run_id, status, summary_text)
        log.info("run %d %s: %s", run_id, status, summary_text)
    return status, path


async def _run(item_ids: list[str] | None, notify: bool) -> int:
    from price_watch import notify as notifier
    from price_watch.store import Store

    config.apply_mcp_env()
    from shopping_deals_mcp.service import ShoppingDealsService  # after env: settings read at import

    wl = config.load_watchlist()
    if item_ids:
        missing = set(item_ids) - {i.id for i in wl.items}
        if missing:
            log.error("unknown item ids: %s", sorted(missing))
            return 2
    items = [i for i in wl.items if i.enabled and (not item_ids or i.id in item_ids)]
    if not items:
        log.warning("nothing to watch (no enabled items)")
        return 0
    with Store(config.DB_PATH) as store:
        status, path = await run_items(store, ShoppingDealsService(), wl, items, notify, notifier, config.REPORTS_DIR)
    print(path)
    return 1 if status == "failed" else 0


def _cmd_list() -> int:
    wl = config.load_watchlist()
    for i in wl.items:
        flag = " " if i.enabled else "x"
        tgt = f"target ${i.target_price:g}" if i.target_price is not None else "no target"
        print(f"[{flag}] {i.id:32} {tgt:16} {i.query}")
    print(f"notify: discord={wl.notify.discord_target or 'off'} toast={wl.notify.toast}")
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    wl = config.load_watchlist()
    item_id = args.id or config.slugify(args.query)
    if wl.get(item_id):
        log.error("item %s already exists", item_id)
        return 2
    wl.items.append(WatchItem(
        id=item_id, query=args.query, sources=args.sources, condition=args.condition,
        price_min=args.price_min, price_max=args.price_max, target_price=args.target,
        drop_pct=args.drop_pct, must_include=args.include or [], exclude=args.exclude or [], notes=args.notes,
    ))
    config.save_watchlist(wl)
    print(f"added {item_id}")
    return 0


def _cmd_toggle(item_id: str, action: str) -> int:
    wl = config.load_watchlist()
    item = wl.get(item_id)
    if item is None:
        log.error("no item %s", item_id)
        return 2
    if action == "remove":
        wl.items.remove(item)
    else:
        item.enabled = action == "enable"
    config.save_watchlist(wl)
    print(f"{action}d {item_id}")
    return 0


def _cmd_history(item_id: str, days: int) -> int:
    from price_watch.store import Store

    with Store(config.DB_PATH) as store:
        rows = store.history(item_id, days)
    if not rows:
        print(f"no history for {item_id}")
    for day, price, title, url in rows:
        print(f"{day}  ${price:>9,.2f}  {title[:70]}  {url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pricewatch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--item", action="append", help="only this item id (repeatable)")
    r.add_argument("--no-notify", action="store_true", help="report only; no Discord/toast, alerts not marked sent")
    sub.add_parser("list")
    a = sub.add_parser("add")
    a.add_argument("query")
    a.add_argument("--id")
    a.add_argument("--target", type=float)
    a.add_argument("--price-min", type=float)
    a.add_argument("--price-max", type=float)
    a.add_argument("--drop-pct", type=float)
    a.add_argument("--condition", choices=["any", "new", "used"], default="any")
    a.add_argument("--sources", nargs="+")
    a.add_argument("--include", action="append", help='required keyword; "a|b" = either (repeatable)')
    a.add_argument("--exclude", action="append", help="reject titles containing this (repeatable)")
    a.add_argument("--notes", default="")
    for name in ("remove", "enable", "disable"):
        sub.add_parser(name).add_argument("id")
    h = sub.add_parser("history")
    h.add_argument("id")
    h.add_argument("--days", type=int, default=30)
    args = p.parse_args(argv)
    _setup_logging(args.verbose)

    if args.cmd == "run":
        return asyncio.run(_run(args.item, notify=not args.no_notify))
    if args.cmd == "list":
        return _cmd_list()
    if args.cmd == "add":
        return _cmd_add(args)
    if args.cmd in ("remove", "enable", "disable"):
        return _cmd_toggle(args.id, args.cmd)
    return _cmd_history(args.id, args.days)


if __name__ == "__main__":
    sys.exit(main())
