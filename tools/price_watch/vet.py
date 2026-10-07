"""Per-listing quality vetting: price_watch gathers the evidence, the LLM Gateway (Haiku) judges it.

"Cheapest" is not a recommendation. Each reported listing gets a verdict (buy / ok / avoid) on brand
track record, review signals, knock-off / implausible-spec signs, and how it compares with the
standard or most-recommended product for the item's requirements.

The gateway's Claude has no tools (user decision 2026-10-07: gather all data ourselves), so the
evidence is assembled here:
  - live listing data + Amazon product-page facts (brand, review count, star histogram, bullets)
  - today's other listings for the item (live) -> better-value pick
  - live Amazon results for the item's `reference_queries` (standard / name-brand products)
  - web-search snippets: brand reputation per listing, reviewer picks per item (cached 7 days)
Verdicts are cached per listing+price (store.vettings), so only new or re-priced listings cost a call.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

from price_watch import gateway, websearch
from price_watch.config import Defaults, WatchItem
from price_watch.fetch import Observation

log = logging.getLogger("price_watch.vet")

VERDICTS = ("buy", "ok", "avoid")
RUN_BUDGET_MIN = 20  # all vetting in one run; the scheduled task is killed at 1 h
MAX_CONSECUTIVE_FAILURES = 2  # then stop calling the gateway for the rest of the run (down / hung)
PEER_LIMIT = 20  # cheapest other listings shown to the model
MAX_PROMPT_CHARS = 20000  # gateway passes the message as a Windows command-line argument (32k limit)

RUBRIC = """You are a skeptical shopping analyst vetting ONE listing for a buyer in Milpitas, CA.
Cheapest is not a recommendation. Decide:
- Is the product actually good? (brand track record, review count, 1-star share, complaints in the evidence)
- Is the price suspiciously low? (no-name/rebrand, specs implausible for the weight or price, overstated
  capacity, counterfeit, fake-review patterns)
- How does it compare with the standard / most-recommended product for the buyer's requirements?
  What does the buyer lose or gain, and what is the price gap?
- Used/local listings: does the price cover the whole item (not one part)? Any scam signals?
Use ONLY the evidence below. Listing and reference prices are live (fetched today); web snippets are
not prices - call any price from them "typical (web)". If something could not be checked, say so.
Verdicts: buy = good product at a good price for these requirements; ok = acceptable trade-off;
avoid = likely knock-off, poor quality, misleading, or clearly worse value than an alternative.
An established brand (track record, warranty, many reviews) costing only a little more (roughly <=15%
or a couple of dollars) is usually the better pick over a no-name listing - name it in better_pick.

Reply with ONLY one JSON object, no prose, no code fence:
{"verdict": "buy|ok|avoid", "summary": "1-2 sentences why", "red_flags": ["..."],
 "standard_product": "the standard / most-recommended product for this need",
 "standard_price": "its price: live reference price if listed below, else 'typical $X (web)' or 'unknown'",
 "vs_standard": "what is lost or gained vs it",
 "better_pick": "title + price + url of a clearly better-value listing from TODAY'S LISTINGS or REFERENCE PRODUCTS, or ''"}"""


@dataclass
class Vetting:
    verdict: str  # buy | ok | avoid | unvetted
    summary: str
    red_flags: list[str] = field(default_factory=list)
    standard_product: str = ""
    standard_price: str = ""
    vs_standard: str = ""
    better_pick: str = ""
    cached: bool = False

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Vetting:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "cached"}
        v = cls(**known)
        v.verdict = str(v.verdict).strip().lower()
        if v.verdict not in VERDICTS:
            raise ValueError(f"bad verdict {v.verdict!r}")
        if not isinstance(v.red_flags, list):
            v.red_flags = [str(v.red_flags)]
        return v


@dataclass
class ItemEvidence:
    """Shared by every listing of one item in one run."""
    references: list[dict] = field(default_factory=list)  # live Amazon listings for reference_queries
    reviewer_picks: list[dict] = field(default_factory=list)  # web snippets: best-of / reddit


class Budget:
    """Run-wide limits so a slow or dead gateway cannot eat the scheduled run."""

    def __init__(self, minutes: float = RUN_BUDGET_MIN, max_failures: int = MAX_CONSECUTIVE_FAILURES):
        self.deadline = time.monotonic() + minutes * 60
        self.max_failures = max_failures
        self.failures = 0

    def exhausted(self) -> str | None:
        if self.failures >= self.max_failures:
            return f"{self.failures} consecutive gateway failures"
        if time.monotonic() >= self.deadline:
            return "run vetting time budget used up"
        return None

    def record(self, ok: bool) -> None:
        self.failures = 0 if ok else self.failures + 1


def context_hash(item: WatchItem) -> str:
    """Changes when what a verdict depends on changes (requirements, references, rubric) -> re-vet."""
    basis = json.dumps([item.query, item.requirements, item.reference_queries, item.target_price, RUBRIC])
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


Runner = Callable[[str], str]  # prompt -> reply text
DetailFn = Callable[[str], Awaitable[dict[str, str]]]
ReferenceFn = Callable[[str], Awaitable[list[dict]]]
WebFn = Callable[..., list[dict]]


def gateway_runner(project: str) -> Runner:
    return lambda prompt: gateway.ask(project, prompt)


def brand_of(obs: Observation, facts: dict[str, str]) -> str:
    """Brand from the product page; else the title's leading capitalised word (often the brand)."""
    if facts.get("brand"):
        return facts["brand"]
    m = re.match(r"\s*([A-Z][A-Za-z0-9&+-]{1,20})\b", obs.title)
    return m.group(1) if m else ""


def _line(o: Observation) -> str:
    stars = f", {o.rating:.1f}★" if o.rating is not None else ""
    where = f", {o.location}" if o.location else ""
    return f"${o.price:.2f} | {o.source} | {o.condition}{where}{stars} | {o.title} | {o.url}"


def _web_lines(results: list[dict]) -> list[str]:
    return [f"- {r['title']}: {r['snippet']} ({r['url']})" for r in results]


def build_prompt(item: WatchItem, obs: Observation, facts: dict[str, str], brand_web: list[dict],
                 peers: list[Observation], evidence: ItemEvidence) -> str:
    parts = [RUBRIC, "", f"WHAT THE BUYER WANTS: {item.query}",
             f"Requirements: {item.requirements or '(none given - judge for a typical buyer of this item)'}"]
    if item.notes:
        parts.append(f"Notes: {item.notes}")
    if item.target_price is not None:
        parts.append(f"Buyer's target price: ${item.target_price:.2f} (sticker + shipping, before tax)")
    parts += ["", "LISTING TO VET (live today; sticker + shipping, before tax):", _line(obs)]
    if facts:
        parts.append("Product page facts (live today):")
        parts += [f"- {k}: {v}" for k, v in facts.items()]
    if brand_web:
        parts += ["", "WEB SEARCH - brand/product reputation:"] + _web_lines(brand_web)
    if evidence.reviewer_picks:
        parts += ["", "WEB SEARCH - reviewer / community picks for this item:"] + _web_lines(evidence.reviewer_picks)
    if evidence.references:
        parts += ["", "REFERENCE PRODUCTS (standard / name-brand, live Amazon today):"]
        parts += [f"- ${float(r['price']):.2f} | {r.get('seller_rating') or '-'}★ | {str(r.get('title', ''))[:120]} | "
                  f"{r.get('url', '')}" for r in evidence.references if r.get("price")]
    others = sorted((p for p in peers if p.key != obs.key), key=lambda p: p.price)[:PEER_LIMIT]
    if others:
        parts += ["", "TODAY'S LISTINGS for the same search (live, cheapest first):"] + [f"- {_line(p)}" for p in others]
    prompt = "\n".join(parts)
    if len(prompt) > MAX_PROMPT_CHARS:
        log.warning("item=%s prompt %d chars truncated to %d", item.id, len(prompt), MAX_PROMPT_CHARS)
        prompt = prompt[:MAX_PROMPT_CHARS]
    return prompt


async def gather_item_evidence(item: WatchItem, store: Any, defaults: Defaults, reference: ReferenceFn | None,
                               web: WebFn = websearch.search) -> ItemEvidence:
    ev = ItemEvidence()
    for q in item.reference_queries:
        if reference is None:
            break
        try:
            ev.references += await reference(q)
        except Exception as exc:  # vetting still runs without the live baseline
            log.warning("item=%s reference search %r failed: %s", item.id, q, exc)
    for q in (f"best {item.query} reddit", f"best {item.query} review wirecutter OR rtings"):
        ev.reviewer_picks += web(q, 4, store, defaults.vet_cache_days)  # same thread: store is sqlite
    log.info("item=%s evidence: %d reference listings, %d reviewer snippets",
             item.id, len(ev.references), len(ev.reviewer_picks))
    return ev


async def vet_listings(store: Any, item: WatchItem, to_vet: list[Observation], peers: list[Observation],
                       defaults: Defaults, runner: Runner, detail: DetailFn | None = None,
                       reference: ReferenceFn | None = None, web: WebFn = websearch.search,
                       budget: Budget | None = None) -> dict[str, Vetting]:
    """Vet each listing (cached verdicts reused). Failures become verdict 'unvetted', never raise."""
    budget = budget or Budget()
    ctx = context_hash(item)
    results: dict[str, Vetting] = {}
    todo: list[Observation] = []
    for o in to_vet:
        try:
            cached = store.get_vetting(item.id, o.key, o.price, defaults.vet_cache_days)
            data = json.loads(cached) if cached else None
            if data and data.get("ctx") == ctx:
                v = Vetting.from_json(data)
                v.cached = True
                results[o.key] = v
                continue
        except (ValueError, TypeError) as exc:  # bad cache row: just re-vet
            log.warning("item=%s bad cached vetting key=%s: %s", item.id, o.key, exc)
        if o.key not in {t.key for t in todo}:
            todo.append(o)
    log.info("item=%s vetting %d listing(s), %d cached", item.id, len(todo), len(results))
    if not todo:
        return results

    try:
        evidence = await gather_item_evidence(item, store, defaults, reference, web)
    except Exception as exc:  # evidence is best-effort
        log.warning("item=%s evidence gathering failed: %s", item.id, exc)
        evidence = ItemEvidence()
    # Serial on purpose: one shared headless browser, and the gateway runs one call per project at a time.
    for o in todo:
        stop = budget.exhausted()
        if stop:
            log.warning("item=%s skip vetting key=%s: %s", item.id, o.key, stop)
            results[o.key] = Vetting("unvetted", f"vetting skipped: {stop}")
            continue
        try:
            facts: dict[str, str] = {}
            if detail is not None and o.source == "amazon":
                try:
                    facts = await detail(o.url)
                except Exception as exc:  # vetting still runs on search-card data
                    log.warning("item=%s detail fetch failed %s: %s", item.id, o.url, exc)
            brand = brand_of(o, facts)
            brand_web = web(f"{brand} {item.query} review", 4, store, defaults.vet_cache_days) if brand else []
            prompt = build_prompt(item, o, facts, brand_web, peers, evidence)
            reply = await asyncio.to_thread(runner, prompt)
            v = Vetting.from_json(gateway.parse_json_reply(reply))
        except Exception as exc:  # one failed listing must not sink the run
            budget.record(ok=False)
            log.warning("item=%s vet failed key=%s: %s: %s", item.id, o.key, type(exc).__name__, exc)
            results[o.key] = Vetting("unvetted", f"vetting failed: {type(exc).__name__}")
            continue
        budget.record(ok=True)
        results[o.key] = v
        data = {k: val for k, val in asdict(v).items() if k != "cached"}
        data["ctx"] = ctx
        store.put_vetting(item.id, o.key, o.price, v.verdict, json.dumps(data))
        log.info("item=%s vetted key=%s price=%.2f brand=%r verdict=%s flags=%s",
                 item.id, o.key, o.price, brand, v.verdict, v.red_flags)
    return results
