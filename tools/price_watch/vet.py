"""Per-listing quality vetting by a headless Claude call (cheap model, WebSearch only).

"Cheapest" is not a recommendation: each reported listing is judged for brand track record, review
signals, knock-off / implausible-spec signs, and how it compares with the standard or most-recommended
product for the item's requirements. Verdicts are cached per listing+price (store.vettings).
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from price_watch.config import Defaults, WatchItem
from price_watch.fetch import Observation

log = logging.getLogger("price_watch.vet")

VERDICTS = ("buy", "ok", "avoid")
MAX_PARALLEL = 4
CALL_TIMEOUT_S = 240

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS),
                    "description": "buy = good product at a good price; ok = acceptable trade-off; avoid"},
        "summary": {"type": "string", "description": "one or two sentences: why this verdict"},
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "standard_product": {"type": "string",
                             "description": "the standard / most-recommended product for this need"},
        "standard_typical_price": {"type": "string",
                                   "description": "its typical price per web sources, e.g. '$20-25 (web)'"},
        "vs_standard": {"type": "string", "description": "what you lose or gain vs the standard product"},
        "better_pick": {"type": "string",
                        "description": "a better-value listing from TODAY'S LISTINGS (title + price + url), or ''"},
    },
    "required": ["verdict", "summary", "red_flags", "standard_product", "standard_typical_price",
                 "vs_standard", "better_pick"],
}

SYSTEM_PROMPT = (
    "You are a skeptical shopping analyst vetting one listing for a buyer in Milpitas, CA. "
    "Cheapest is not a recommendation. Judge whether the product is actually good, whether the price is "
    "suspiciously low (knock-off, rebrand, overstated specs, counterfeit), and how it compares with the "
    "standard or most-recommended product for the buyer's requirements. Use WebSearch (at most 3 searches) "
    "for brand reputation, known problems, and reviewer picks (Wirecutter, RTINGS, Stiftung Warentest, "
    "Reddit). Prices you find on the web are typical, not live: label them as such. For used/local "
    "listings, also check that the price covers the whole item (not one part) and look for scam signals. "
    "Do not invent facts; if something could not be checked, say so. Answer only via the JSON schema."
)


@dataclass
class Vetting:
    verdict: str  # buy | ok | avoid | unvetted
    summary: str
    red_flags: list[str] = field(default_factory=list)
    standard_product: str = ""
    standard_typical_price: str = ""
    vs_standard: str = ""
    better_pick: str = ""
    cost_usd: float = 0.0
    cached: bool = False

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Vetting:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        v = cls(**known)
        if v.verdict not in VERDICTS:
            raise ValueError(f"bad verdict {v.verdict!r}")
        return v


Runner = Callable[[str, Defaults], dict[str, Any]]  # prompt -> {"structured_output": {...}, "total_cost_usd": x}
DetailFn = Callable[[str], Awaitable[dict[str, str]]]


def claude_exe() -> str:
    """Real claude.exe behind the npm .cmd shim: avoids cmd.exe re-parsing quotes in our arguments."""
    found = shutil.which("claude")
    if not found:
        raise FileNotFoundError("claude CLI not on PATH")
    exe = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    return str(exe) if exe.exists() else found


def run_claude(prompt: str, defaults: Defaults) -> dict[str, Any]:
    cmd = [claude_exe(), "-p", "--model", defaults.vet_model, "--restricted", "--strict-mcp-config",
           "--tools", "WebSearch", "--allowedTools", "WebSearch", "--no-session-persistence",
           "--output-format", "json", "--max-budget-usd", str(defaults.vet_budget_usd),
           "--system-prompt", SYSTEM_PROMPT, "--json-schema", json.dumps(SCHEMA)]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=CALL_TIMEOUT_S, cwd=tempfile.gettempdir())
    if proc.returncode != 0:
        raise RuntimeError(f"claude rc={proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]}")
    out = json.loads(proc.stdout)
    if out.get("is_error") or not out.get("structured_output"):
        raise RuntimeError(f"claude returned no structured output: {str(out.get('result'))[:300]}")
    return out


def _line(o: Observation) -> str:
    stars = f", {o.rating:.1f}★" if o.rating is not None else ""
    where = f", {o.location}" if o.location else ""
    return f"${o.price:.2f} | {o.source} | {o.condition}{where}{stars} | {o.title} | {o.url}"


def build_prompt(item: WatchItem, obs: Observation, facts: dict[str, str], peers: list[Observation]) -> str:
    parts = [
        f"WHAT THE BUYER WANTS: {item.query}",
        f"Requirements: {item.requirements or '(none given - judge for a typical buyer of this item)'}",
    ]
    if item.notes:
        parts.append(f"Notes: {item.notes}")
    if item.target_price is not None:
        parts.append(f"Buyer's target price: ${item.target_price:.2f} (sticker + shipping, before tax)")
    parts += ["", "LISTING TO VET (price is live, fetched today, sticker + shipping before tax):", _line(obs)]
    if facts:
        parts.append("Product page facts (live today):")
        parts += [f"- {k}: {v}" for k, v in facts.items()]
    others = [p for p in peers if p.key != obs.key]
    if others:
        parts += ["", "TODAY'S LISTINGS for the same search (live prices; pick better_pick only from these):"]
        parts += [f"- {_line(p)}" for p in others]
    parts += ["", "Vet the listing. Return verdict, summary, red flags, the standard/most-recommended product "
                  "and its typical price, what is lost vs it, and a better-value pick from today's listings "
                  "if one is clearly better for a small price difference."]
    return "\n".join(parts)


async def vet_listings(store: Any, item: WatchItem, to_vet: list[Observation], peers: list[Observation],
                       defaults: Defaults, runner: Runner = run_claude,
                       detail: DetailFn | None = None) -> dict[str, Vetting]:
    """Vet each listing (cached ones reused). Failures become verdict 'unvetted', never raise."""
    results: dict[str, Vetting] = {}
    todo: list[Observation] = []
    for o in to_vet:
        cached = store.get_vetting(item.id, o.key, o.price, defaults.vet_cache_days)
        if cached:
            v = Vetting.from_json(json.loads(cached))
            v.cached, v.cost_usd = True, 0.0
            results[o.key] = v
        elif o.key not in {t.key for t in todo}:
            todo.append(o)
    log.info("item=%s vetting %d listing(s), %d cached", item.id, len(todo), len(results))

    facts: dict[str, dict[str, str]] = {}
    for o in todo:  # sequential: one shared headless browser
        if detail is not None and o.source == "amazon":
            try:
                facts[o.key] = await detail(o.url)
            except Exception as exc:  # vetting still runs on search-card data
                log.warning("item=%s detail fetch failed %s: %s", item.id, o.url, exc)

    sem = asyncio.Semaphore(MAX_PARALLEL)

    async def one(o: Observation) -> None:
        prompt = build_prompt(item, o, facts.get(o.key, {}), peers)
        async with sem:
            try:
                out = await asyncio.to_thread(runner, prompt, defaults)
                v = Vetting.from_json(out["structured_output"])
                v.cost_usd = float(out.get("total_cost_usd") or 0.0)
            except Exception as exc:  # one failed call must not sink the run
                log.warning("item=%s vet failed key=%s: %s: %s", item.id, o.key, type(exc).__name__, exc)
                results[o.key] = Vetting("unvetted", f"vetting failed: {type(exc).__name__}")
                return
        results[o.key] = v
        data = {k: val for k, val in asdict(v).items() if k not in ("cost_usd", "cached")}
        store.put_vetting(item.id, o.key, o.price, v.verdict, json.dumps(data))
        log.info("item=%s vetted key=%s price=%.2f verdict=%s cost=$%.4f flags=%s",
                 item.id, o.key, o.price, v.verdict, v.cost_usd, v.red_flags)

    await asyncio.gather(*(one(o) for o in todo))
    return results
