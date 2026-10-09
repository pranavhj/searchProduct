"""Watchlist loading/saving and runtime environment setup."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("price_watch.config")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WATCHLIST_PATH = PROJECT_ROOT / "watchlist.json"
MCP_CONFIG_PATH = PROJECT_ROOT / ".mcp.json"
DISCORD_TARGET_ENV = "PRICEWATCH_DISCORD_TARGET"  # Discord channel id (user env var, not in the repo)
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "price_watch.db"
REPORTS_DIR = PROJECT_ROOT / "reports" / "price-watch"

# serpapi_google_shopping is opt-in per item: each query spends a SerpApi credit (daily x N items adds up).
DEFAULT_SOURCES = ["amazon", "ebay_public", "craigslist", "offerup", "facebook_marketplace"]



@dataclass
class WatchItem:
    id: str
    query: str
    enabled: bool = True
    sources: list[str] | None = None  # None -> defaults.sources
    condition: str = "any"  # any | new | used
    price_min: float | None = None  # floor that drops accessories / junk
    price_max: float | None = None
    target_price: float | None = None  # alert when sticker price (+ shipping, before tax) <= this
    drop_pct: float | None = None  # None -> defaults.drop_pct
    max_miles: float | None = None  # pickup listings farther than this (straight line) are dropped; None -> defaults
    must_include: list[str] = field(default_factory=list)  # each entry: "a|b" = a OR b; all entries required
    exclude: list[str] = field(default_factory=list)
    notes: str = ""
    # What the user actually wants (features, must-haves, deal-breakers) - from the new-item interview.
    # Fed to the per-listing quality vetting so "cheap" is judged against real needs.
    requirements: str = ""
    # Live Amazon searches for the standard / name-brand product(s) the vetting compares against.
    reference_queries: list[str] = field(default_factory=list)


@dataclass
class Defaults:
    sources: list[str] = field(default_factory=lambda: list(DEFAULT_SOURCES))
    drop_pct: float = 20.0
    baseline_days: int = 30
    max_results_per_source: int = 15
    max_results_local: int = 40  # cap when pickup sources are searched; the distance filter runs after it
    top_n_report: int = 5
    max_miles: float = 25.0
    vet: bool = True  # quality-vet every reported listing + alert (one LLM Gateway call per listing)
    vet_gateway_project: str = "searchproduct_vet"  # LLM Gateway project (Haiku); see vet.py
    vet_cache_days: int = 7  # reuse a verdict (and web-search results) for the same listing+price this long


@dataclass
class NotifyConfig:
    discord_target: str | None = None  # Discord channel id; None -> no Discord
    discord_digest_always: bool = True  # False -> Discord only when there are alerts
    toast: bool = True  # Windows toast for alerts


@dataclass
class Watchlist:
    items: list[WatchItem]
    defaults: Defaults = field(default_factory=Defaults)
    notify: NotifyConfig = field(default_factory=NotifyConfig)

    def get(self, item_id: str) -> WatchItem | None:
        return next((i for i in self.items if i.id == item_id), None)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "item"


def _known(cls, data: dict, where: str) -> dict:
    allowed = set(cls.__dataclass_fields__)
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)} (allowed: {sorted(allowed)})")
    return data


def load_watchlist(path: Path = WATCHLIST_PATH) -> Watchlist:
    if not path.exists():
        log.info("no watchlist at %s; starting empty", path)
        return Watchlist(items=[])
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = [WatchItem(**_known(WatchItem, it, f"item #{n}")) for n, it in enumerate(raw.get("items", []))]
    ids = [i.id for i in items]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate watch item ids: {sorted(dupes)}")
    for item in items:
        if item.condition not in ("any", "new", "used"):
            raise ValueError(f"{item.id}: condition must be any|new|used, got {item.condition!r}")
    wl = Watchlist(
        items=items,
        defaults=Defaults(**_known(Defaults, raw.get("defaults", {}), "defaults")),
        notify=NotifyConfig(**_known(NotifyConfig, raw.get("notify", {}), "notify")),
    )
    target = env_value(DISCORD_TARGET_ENV)  # kept out of the repo; the file value is only a fallback
    if target:
        wl.notify.discord_target = target
    log.debug("loaded %d items from %s", len(items), path)
    return wl


def save_watchlist(wl: Watchlist, path: Path = WATCHLIST_PATH) -> None:
    data = {"defaults": asdict(wl.defaults), "notify": asdict(wl.notify), "items": [asdict(i) for i in wl.items]}
    if env_value(DISCORD_TARGET_ENV):  # never write the env-supplied channel id into the tracked file
        data["notify"]["discord_target"] = None
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    log.info("saved %d items to %s", len(wl.items), path)


def env_value(name: str) -> str | None:
    """A user setting from the process env, else the Windows user env (HKCU\Environment).

    The registry fallback matters for the scheduled task: it can start with an environment built before a
    `setx`, so a freshly set variable would otherwise be invisible until the next logon.
    """
    value = os.environ.get(name)
    if value:
        return value
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0]) or None
    except (ImportError, OSError):
        return None


_VAR_RE = re.compile(r"\$\{(\w+)\}")


def expand_vars(value: str) -> str:
    """Replace ${NAME} with env_value(NAME); an unset variable becomes '' (and is logged)."""
    def one(m: re.Match) -> str:
        found = env_value(m.group(1))
        if found is None:
            log.warning("environment variable %s is not set", m.group(1))
        return found or ""
    return _VAR_RE.sub(one, value)


def apply_mcp_env(path: Path = MCP_CONFIG_PATH) -> None:
    """Copy the shopping-deals env block from .mcp.json (Milpitas location, tax, Amazon flag).

    Must run before shopping_deals_mcp is imported: its settings are read at import time.
    Existing environment variables win, so a scheduled task can still override.
    """
    try:
        env = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["shopping-deals"].get("env", {})
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        log.warning("could not read shopping-deals env from %s: %s", path, exc)
        return
    for key, value in env.items():
        os.environ.setdefault(key, expand_vars(str(value)))
    log.debug("applied %d env vars from %s", len(env), path)
