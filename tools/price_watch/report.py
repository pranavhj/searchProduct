"""Markdown report for one run + short text for notifications."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from price_watch.analyze import Alert, ItemSummary

log = logging.getLogger("price_watch.report")

KIND_LABEL = {"target_hit": "🎯 Target hit", "big_drop": "📉 Big drop", "listing_drop": "✂️ Price cut"}


def _money(v: float | None) -> str:
    return f"${v:,.2f}" if v is not None else "—"


def _cell(text: str | None) -> str:
    return (text or "").replace("|", "/").replace("\n", " ")


def alert_line(a: Alert, md_links: bool = True) -> str:
    link = f"[link]({a.obs.url})" if md_links else f"<{a.obs.url}>"
    return f"{KIND_LABEL.get(a.kind, a.kind)} — **{a.item_id}**: {a.message} — {_cell(a.obs.title)[:80]} ({a.obs.source}) {link}"


def render(summaries: list[ItemSummary], run_at: datetime, top_n: int) -> str:
    alerts = [a for s in summaries for a in s.alerts]
    out = [f"# Price watch — {run_at:%Y-%m-%d %H:%M}", ""]
    out.append(f"{len(summaries)} items checked · {len(alerts)} alert(s)")
    out.append("")
    out.append("Prices are landed totals (price + shipping + est. tax where the source provides them); "
               "Amazon prices exclude clip-on coupons. Used listings: verify condition before buying.")
    out.append("")
    if alerts:
        out += ["## Alerts", ""] + [f"- {alert_line(a)}" for a in alerts] + [""]

    out += ["## Summary", "", "| Item | Best today | Prev day | Change | Median (days) | Target | Listings | New |",
            "|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        change = f"{s.change_pct:+.0f}%" if s.change_pct is not None else "—"
        base = f"{_money(s.baseline)} ({s.baseline_days})" if s.baseline else f"— ({s.baseline_days})"
        best = f"[{_money(s.best.price)}]({s.best.url})" if s.best else "none"
        new = "first run" if s.first_run else str(len(s.new_listings))
        out.append(f"| {s.item.id} | {best} | {_money(s.prev_best)} | {change} | {base} | "
                   f"{_money(s.item.target_price)} | {len(s.fetch.observations)} | {new} |")
    out.append("")

    for s in summaries:
        out += [f"## {s.item.id}", "", f"Query: `{s.item.query}`"]
        if s.fetch.source_errors:
            errs = "; ".join(f"{k}: {_cell(v)[:120]}" for k, v in s.fetch.source_errors.items())
            out.append(f"Source errors: {errs}")
        if s.fetch.dropped:
            out.append(f"Filtered out: {', '.join(f'{k} {v}' for k, v in s.fetch.dropped.items())}")
        out.append("")
        obs = sorted(s.fetch.observations, key=lambda o: o.price)[:top_n]
        if not obs:
            out += ["_No relevant listings this run._", ""]
            continue
        new_keys = {o.key for o in s.new_listings}
        out += ["| Price | Source | Condition | Location | Title | |", "|---|---|---|---|---|---|"]
        for o in obs:
            tag = " 🆕" if o.key in new_keys else ""
            out.append(f"| {_money(o.price)} | {o.source} | {_cell(o.condition)} | {_cell(o.location)} | "
                       f"{_cell(o.title)[:90]}{tag} | [open]({o.url}) |")
        out.append("")
    return "\n".join(out)


def write(text: str, reports_dir: Path, run_at: datetime) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{run_at:%Y-%m-%d_%H%M}.md"
    path.write_text(text, encoding="utf-8")
    (reports_dir / "latest.md").write_text(text, encoding="utf-8")
    log.info("report written %s", path)
    return path


def digest(summaries: list[ItemSummary], report_path: Path) -> str:
    """Short plain-text digest for Discord (one line per item + alerts)."""
    alerts = [a for s in summaries for a in s.alerts]
    lines = [f"**Price watch** — {len(alerts)} alert(s)"]
    lines += [alert_line(a, md_links=False) for a in alerts]
    lines.append("")
    for s in summaries:
        if s.best:
            chg = f" ({s.change_pct:+.0f}%)" if s.change_pct is not None else ""
            lines.append(f"• {s.item.id}: best {_money(s.best.price)}{chg} — {s.best.source} <{s.best.url}>")
        else:
            lines.append(f"• {s.item.id}: no relevant listings")
    lines.append(f"Report: {report_path}")
    return "\n".join(lines)
