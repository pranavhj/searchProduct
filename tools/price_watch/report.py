"""Markdown report for one run + short text for notifications."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from price_watch.analyze import Alert, ItemSummary

log = logging.getLogger("price_watch.report")

KIND_LABEL = {"target_hit": "🎯 Target hit", "big_drop": "📉 Big drop", "listing_drop": "✂️ Price cut"}
MISSING_REQUIREMENTS = "needs the new-item questions (no requirements yet): ask Claude to run the item interview so listings are judged against what you actually want"
VERDICT_LABEL = {"buy": "✅ buy", "ok": "🟡 ok", "avoid": "⛔ avoid", "unvetted": "❔ unvetted"}


def verdict_tag(s: ItemSummary, key: str) -> str:
    v = s.vettings.get(key)
    return VERDICT_LABEL.get(v.verdict, v.verdict) if v else ""


def vetting_note(v: object, short: bool = False) -> str:
    """One line: summary (+ comparison with the standard product and better pick unless short)."""
    text = v.summary
    rules = getattr(v, "rules_applied", None)
    if rules:
        text = f"[{'; '.join(rules)}] {text}"
    if short:
        return text[:160] + ("…" if len(text) > 160 else "")
    if v.red_flags:
        text += f" Flags: {'; '.join(v.red_flags)}."
    if v.standard_product:
        text += f" vs standard ({v.standard_product}, {v.standard_price or 'price unknown'}): {v.vs_standard}"
    if v.better_pick:
        text += f" Better pick: {v.better_pick}"
    return text


def _money(v: float | None) -> str:
    return f"${v:,.2f}" if v is not None else "—"


def _cell(text: str | None) -> str:
    return (text or "").replace("|", "/").replace("\n", " ")


def alert_line(a: Alert, md_links: bool = True, vetting: object = None) -> str:
    link = f"[link]({a.obs.url})" if md_links else f"<{a.obs.url}>"
    where = f", {a.obs.distance_mi:.0f} mi" if a.obs.distance_mi is not None else ""
    stars = f", {a.obs.rating:.1f}★" if a.obs.rating is not None else ""
    line = (f"{KIND_LABEL.get(a.kind, a.kind)} — **{a.item_id}**: {a.message} — {_cell(a.obs.title)[:80]} "
            f"({a.obs.source}{where}{stars}) {link}")
    if vetting is not None:
        line += f" — {VERDICT_LABEL.get(vetting.verdict, vetting.verdict)}: {vetting_note(vetting, short=True)}"
    return line


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
        out += [f"- {alert_line(a, vetting=s.vettings.get(a.obs.key))}" for s in summaries for a in s.alerts] + [""]
    skipped = [(s, a) for s in summaries for a in s.skipped_alerts]
    if skipped:
        out += ["## Withheld alerts (vetted avoid)", ""]
        out += [f"- {alert_line(a, vetting=s.vettings.get(a.obs.key))}" for s, a in skipped] + [""]

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
        if not s.item.requirements:
            out.append(f"⚠️ {MISSING_REQUIREMENTS}")
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
        out += ["| Price | Source | Condition | Location | Dist | ★ | Verdict | Title | |",
                "|---|---|---|---|---|---|---|---|---|"]
        for o in obs:
            tag = " 🆕" if o.key in new_keys else ""
            dist = f"{o.distance_mi:.0f} mi" if o.distance_mi is not None else "—"
            stars = f"{o.rating:.1f}" if o.rating is not None else "—"
            out.append(f"| {_money(o.price)} | {o.source} | {_cell(o.condition)} | {_cell(o.location)} | {dist} | {stars} | "
                       f"{verdict_tag(s, o.key) or '—'} | {_cell(o.title)[:90]}{tag} | [open]({o.url}) |")
        out.append("")
        vetted = [(o, s.vettings[o.key]) for o in obs if o.key in s.vettings]
        if vetted:
            out += ["**Vetting**", ""]
            out += [f"- {_money(o.price)} {_cell(o.title)[:60]} — {verdict_tag(s, o.key)}: {_cell(vetting_note(v))}"
                    for o, v in vetted]
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
    lines += [alert_line(a, md_links=False, vetting=s.vettings.get(a.obs.key)) for s in summaries for a in s.alerts]
    withheld = sum(len(s.skipped_alerts) for s in summaries)
    if withheld:
        lines.append(f"({withheld} alert(s) withheld: listing vetted ⛔ avoid — see report)")
    lines.append("")
    for s in summaries:
        if s.best:
            chg = f" ({s.change_pct:+.0f}%)" if s.change_pct is not None else ""
            where = f", {s.best.distance_mi:.0f} mi" if s.best.distance_mi is not None else ""
            lines.append(f"• {s.item.id}: best {_money(s.best.price)}{chg} — {s.best.source}{where} <{s.best.url}>"
                         + (f" {verdict_tag(s, s.best.key)}" if s.best.key in s.vettings else ""))
            good = s.best_vetted() if s.vettings else None
            if good is not None and good.key != s.best.key:
                v = s.vettings.get(good.key)
                lines.append(f"  best not-avoid: {_money(good.price)} — {_cell(good.title)[:60]} <{good.url}>"
                             + (f" {verdict_tag(s, good.key)}: {vetting_note(v, short=True)}" if v else ""))
        else:
            lines.append(f"• {s.item.id}: no relevant listings")
    missing = [s.item.id for s in summaries if not s.item.requirements]
    if missing:
        lines.append(f"⚠️ {', '.join(missing)}: {MISSING_REQUIREMENTS}")
    lines.append(f"Report: {report_path}")
    return "\n".join(lines)
