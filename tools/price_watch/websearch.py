"""Web-search snippets as vetting evidence (brand reputation, known problems, reviewer picks).

The LLM Gateway's Claude has no tools, so price_watch gathers this itself (user decision 2026-10-07).
DuckDuckGo's HTML endpoint works over plain HTTP (no browser, no key). Results are cached per query
(store.web_cache): reputation changes slowly, and repeated queries would get rate-limited.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("price_watch.websearch")

DDG_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")
MIN_INTERVAL_S = 1.5  # be polite: DDG starts serving challenges to rapid-fire queries
_last_call = 0.0


def parse_results(html: str, limit: int) -> list[dict[str, str]]:
    out = []
    for res in BeautifulSoup(html, "html.parser").select(".result"):
        a = res.select_one(".result__a")
        if a is None:
            continue
        snippet = res.select_one(".result__snippet")
        out.append({"title": a.get_text(" ", strip=True),
                    "url": a.get("href", ""),
                    "snippet": (snippet.get_text(" ", strip=True) if snippet else "")[:300]})
        if len(out) >= limit:
            break
    return out


def search(query: str, limit: int = 5, store: Any = None, max_age_days: int = 7) -> list[dict[str, str]]:
    """Top results for query; [] on failure (evidence is best-effort, the vetting says what's missing)."""
    global _last_call
    if store is not None:
        cached = store.get_web(query, max_age_days)
        if cached is not None:
            log.debug("web cache hit %r", query)
            return json.loads(cached)[:limit]
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()
    try:
        r = httpx.post(DDG_URL, data={"q": query}, headers={"User-Agent": USER_AGENT}, timeout=20,
                       follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("web search failed %r: %s", query, exc)
        return []
    results = parse_results(r.text, limit)
    if not results:  # challenge page or layout change: don't cache an empty answer
        log.warning("web search %r returned 0 results (status %d, %d bytes)", query, r.status_code, len(r.text))
        return []
    if store is not None:
        store.put_web(query, json.dumps(results))
    log.info("web search %r -> %d results", query, len(results))
    return results
