"""Price-sorted second pass for the person-to-person sources.

The shopping-deals sources read each marketplace's default order (Facebook: relevance, Craigslist:
newest, OfferUp: relevance) and a small page, so the cheapest nearby listings can be missed - the same
problem amazon_cheap.py solves for Amazon. Probed 2026-10-06 (power bank query):
  * Craigslist  `sort=priceasc`                                   -> strictly ascending
  * OfferUp     `SORT=price`                                      -> ascending
  * Facebook    GraphQL `commerce_search_sort_by: PRICE_ASCEND`   -> ascending, up to 24 results
Each fetcher returns deals in the find_best_deals() shape. Results are price-band-filtered here
(Facebook ignores the band server-side); relevance and distance are applied later by fetch_item.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import quote, urlencode

import httpx

log = logging.getLogger("price_watch.local_cheap")

LOCAL_SORT_SOURCES = ("craigslist", "offerup", "facebook_marketplace")
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126 Safari/537.36")


def craigslist_url(site: str, query: str, price_min: float | None, price_max: float | None) -> str:
    params = {"query": query, "sort": "priceasc"}
    if price_min is not None:
        params["min_price"] = str(int(price_min))
    if price_max is not None:
        params["max_price"] = str(int(price_max))
    return f"https://{site}.craigslist.org/search/sss?{urlencode(params)}"


def offerup_url(query: str, radius_miles: int) -> str:
    return f"https://offerup.com/search?{urlencode({'q': query, 'radius': radius_miles, 'SORT': 'price'})}"


def in_band(price: float | None, price_min: float | None, price_max: float | None) -> bool:
    if price is None:
        return False
    return (price_min is None or price >= price_min) and (price_max is None or price <= price_max)


def facebook_sort_variables(variables: dict) -> dict:
    """Return Facebook search variables with the price-ascending sort switched on."""
    out = json.loads(json.dumps(variables))
    out["params"]["browse_request_params"]["commerce_search_sort_by"] = "PRICE_ASCEND"
    return out


async def _craigslist(query, price_min, price_max, limit):
    from shopping_deals_mcp.config import settings
    from shopping_deals_mcp.sources.craigslist import _parse_html

    out = []
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=True,
                                 headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}) as client:
        for site in settings.craigslist_sites:
            resp = await client.get(craigslist_url(site, query, price_min, price_max))
            resp.raise_for_status()
            out += _parse_html(site, resp.text, limit)
    return out


async def _offerup(query, price_min, price_max, limit):
    from shopping_deals_mcp.config import settings
    from shopping_deals_mcp.sources.offerup import _parse_offerup_html, _resolve_offerup_location

    cookie = quote(json.dumps(_resolve_offerup_location(None, settings), separators=(",", ":")))
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=True,
                                 headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9",
                                          "Cookie": f"ou.location={cookie}"}) as client:
        resp = await client.get(offerup_url(query, settings.offerup_radius_miles))
        resp.raise_for_status()
    return _parse_offerup_html(resp.text, limit, price_min, price_max)


async def _facebook(query, price_min, price_max, limit):
    from shopping_deals_mcp.config import settings
    from shopping_deals_mcp.sources import facebook_marketplace as fb

    coords = fb._resolve_coordinates(None, settings)
    if not coords:
        return []
    headers = fb._facebook_headers()
    async with httpx.AsyncClient(timeout=max(settings.http_timeout_seconds, 20.0), follow_redirects=True,
                                 headers=headers) as client:
        token, referer = await fb._fetch_lsd_token(client, query)
        payload = fb._search_payload(query=query, latitude=coords[0], longitude=coords[1],
                                     radius_km=settings.facebook_marketplace_radius_km,
                                     max_results=limit, lsd=token)
        payload["variables"] = json.dumps(facebook_sort_variables(json.loads(payload["variables"])),
                                          separators=(",", ":"))
        resp = await client.post(fb.FACEBOOK_GRAPHQL_URL, data=payload, headers={
            **headers, "Accept": "*/*", "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.facebook.com", "Referer": referer, "x-fb-lsd": token})
        resp.raise_for_status()
    return fb._parse_facebook_marketplace_results(fb._loads_graphql_response(resp.text), max_results=limit,
                                                  price_min=price_min, price_max=price_max)


_FETCHERS = {"craigslist": _craigslist, "offerup": _offerup, "facebook_marketplace": _facebook}


async def fetch_cheapest_local(source: str, query: str, price_min: float | None, price_max: float | None,
                               limit: int) -> list[dict]:
    listings = await _FETCHERS[source](query, price_min, price_max, limit)
    kept = [x for x in listings if in_band(x.price, price_min, price_max)]
    log.info("%s cheapest-first query=%r got=%d in_band=%d", source, query, len(listings), len(kept))
    return [{"listing": x.model_dump(), "deal_score": None} for x in kept]
