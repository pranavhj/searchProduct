"""Markdown report for one run + short text for notifications."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from price_watch.analyze import Alert, ItemSummary
from price_watch.fetch import Observation

log = logging.getLogger("price_watch.report")

KIND_LABEL = {"target_hit": "🎯 Target hit", "big_drop": "📉 Big drop", "listing_drop": "✂️ Price cut"}


def _money(v: float | None) -> str:
    return f"${v:,.2f}" if v is not None else "—"


def _cell(text: str | None) -> str:
    return (text or "").replace("|", "/").replace("\n", " ")


def alert_line(a: Alert, md_links: bool = True) -> str:
    link = f"[link]({a.obs.url})" if md_links else f"<{a.obs.url}>"
    where = f", {a.obs.distance_mi:.0f} mi" if a.obs.distance_mi is not None else ""
    stars = f", {a.obs.rating:.1f}★{_reviews(a.obs)}" if a.obs.rating is not None else ""
    return (f"{KIND_LABEL.get(a.kind, a.kind)} — **{a.item_id}**: {a.message} — {_cell(a.obs.title)[:80]} "
            f"({a.obs.source}{where}{stars}) {link}")


def _reviews(o: Observation) -> str:
    return f" ({o.review_count:,})" if o.review_count is not None else ""


def vet_lines(o: Observation, brief: bool = False) -> list[str]:
    """Plain-text evidence lines for one listing: verdict, flags, complaint themes."""
    v = o.vet
    if v is None:
        return []
    icon = {"worth it": "✅", "no red flags found": "➖", "risky": "⚠️", "probably a trap": "🚩"}.get(v.verdict, "")
    out = [f"{icon} **{v.verdict}** (confidence: {v.confidence}) — based on: {v.basis}"]
    if v.flags:
        out.append("Flags: " + "; ".join(v.flag_texts()))
    if brief:  # Discord: verdict + flags + top complaint only
        if v.themes:
            out.append("Top complaint: " + _cell(v.themes[0])[:120])
        return out
    if v.themes:
        out.append("Complaints: " + " | ".join(_cell(t)[:200] for t in v.themes))
    if v.customers_say:
        out.append("Customers say: " + _cell(v.customers_say)[:350])
    if v.page is not None and v.page.seller:
        out.append(f"Sold by {_cell(v.page.seller)}" + (f", ships from {_cell(v.page.ships_from)}" if v.page.ships_from else ""))
    out.append("Would change my mind: " + v.would_change)
    return out


def render(summaries: list[ItemSummary], run_at: datetime, top_n: int) -> str:
    alerts = [a for s in summaries for a in s.alerts]
    out = [f"# Price watch — {run_at:%Y-%m-%d %H:%M}", ""]
    out.append(f"{len(summaries)} items checked · {len(alerts)} alert(s)")
    out.append("")
    out.append("Prices are sticker price + shipping, before sales tax (~9.4% on store purchases; none on private "
               "pickup). Amazon prices exclude clip-on coupons. Distance is straight-line from home; pickup listings "
               "beyond each item's max miles are dropped. Used listings: verify condition — and that the price is "
               "for the whole item, not one part — before buying.")
    out.append("")
    if alerts:
        out += ["## Alerts", ""]
        for a in alerts:
            out.append(f"- {alert_line(a)}")
            out += [f"  - {line}" for line in vet_lines(a.obs)]
        out.append("")

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
        out += ["| Price | Source | Condition | Location | Dist | ★ (n) | Verdict | Flags | Title | |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for o in obs:
            tag = " 🆕" if o.key in new_keys else ""
            dist = f"{o.distance_mi:.0f} mi" if o.distance_mi is not None else "—"
            stars = f"{o.rating:.1f}{_reviews(o)}" if o.rating is not None else "—"
            verdict = o.vet.verdict if o.vet else "—"
            flags = _cell("; ".join(o.vet.flag_texts()))[:160] if o.vet else ""
            out.append(f"| {_money(o.price)} | {o.source} | {_cell(o.condition)} | {_cell(o.location)} | {dist} | {stars} | "
                       f"{verdict} | {flags} | {_cell(o.title)[:90]}{tag} | [open]({o.url}) |")
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
    for a in alerts:
        lines.append(alert_line(a, md_links=False))
        lines += [f"    {line}" for line in vet_lines(a.obs, brief=True)]
    lines.append("")
    for s in summaries:
        if s.best:
            chg = f" ({s.change_pct:+.0f}%)" if s.change_pct is not None else ""
            where = f", {s.best.distance_mi:.0f} mi" if s.best.distance_mi is not None else ""
            lines.append(f"• {s.item.id}: best {_money(s.best.price)}{chg} — {s.best.source}{where} <{s.best.url}>")
        else:
            lines.append(f"• {s.item.id}: no relevant listings")
    lines.append(f"Report: {report_path}")
    return "\n".join(lines)
