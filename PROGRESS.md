# searchProduct

## State
Currently: daily price watch live (Task Scheduler 08:00, watchlist.json, 7 items); eBay official API still awaiting keys
Last session: 2026-10-05 (built tools/price_watch; plan/current.md)

## Done
- 2026-10-05: price watch built (fetch/store/analyze/report/notify, 24 tests, adversarial review fixed 11 findings, first live run 7 items OK)
- Created wishlist.md (tracking table) and tools-research.md (MCP servers / skills found)
- Installed shopping-deals (tools/, venv) + secondhand (npx); both smoke-tested (list tools OK)
- Location set to Milpitas CA (ZIP/coords in user env vars)

## Next
- Price watch: Discord set (channel id in env PRICEWATCH_DISCORD_TARGET, tested 2026-10-06); eBay needs user to create Production keyset + setx EBAY_APP_ID/EBAY_CERT_ID; tune targets
- Repo: github.com/pranavhj/searchProduct (private), branch price-watch
- SerpApi key set (user env var SERPAPI_API_KEY) 2026-10-04 — Google Shopping verified available
- **ASK USER (on/after 2026-10-05):** is eBay dev registration approved? Then setx EBAY_APP_ID, EBAY_CERT_ID, EBAY_CLIENT_ID, EBAY_CLIENT_SECRET
- First search done (air duster) 2026-10-04; standing desk searched 2026-10-04 (budget/size still unknown); 1470nm 15W laser searched 2026-10-04 (eBay via Playwright scratch script)
- Possibly a /deal-search skill that reads wishlist.md and updates best deals

## Key decisions
- Nothing installed without user approval (third-party scrapers, API keys)
- 2026-10-04: tried SHOPPING_ENABLE_AMAZON_SCRAPE — Amazon returns JS bot challenge (0 results); reverted. Use SerpApi amazon engine instead.
- 2026-10-04: Playwright headless Chromium gets Amazon search results (bypasses bot challenge) — scratchpad prototype works
- 2026-10-04: Amazon added to shopping-deals (Playwright fallback in sources/amazon.py, flag on in .mcp.json); 40 tests pass; needs Claude Code restart
- 2026-10-07 14:47: phase 2 started — per-listing Haiku vetting + new-item feature interview
- 2026-10-07 14:49: phase 2 plan written (gateway-based vetting, new-item interview); 3 open questions
- 2026-10-07 15:28: phase 2 done — gateway vetting live-tested, review fixed, power-bank interview recorded
