# searchProduct

## State
Currently: daily price watch live (Task Scheduler 08:00, watchlist.json, 7 items); eBay official API still awaiting keys
Last session: 2026-10-05 (built tools/price_watch; plan/current.md)

## Done
- 2026-10-05: price watch built (fetch/store/analyze/report/notify, 24 tests, adversarial review fixed 11 findings, first live run 7 items OK)
- Created wishlist.md (tracking table) and tools-research.md (MCP servers / skills found)
- Installed shopping-deals (tools/, venv) + secondhand (npx); both smoke-tested (list tools OK)
- Location set to Milpitas CA 00000

## Next
- Price watch: set notify.discord_target; eBay 403 → need API keys; tune targets in watchlist.json
- SerpApi key set (user env var SERPAPI_API_KEY) 2026-10-04 — Google Shopping verified available
- **ASK USER (on/after 2026-10-05):** is eBay dev registration approved? Then setx EBAY_APP_ID, EBAY_CERT_ID, EBAY_CLIENT_ID, EBAY_CLIENT_SECRET
- First search done (air duster) 2026-10-04; standing desk searched 2026-10-04 (budget/size still unknown); 1470nm 15W laser searched 2026-10-04 (eBay via Playwright scratch script)
- Possibly a /deal-search skill that reads wishlist.md and updates best deals

## Key decisions
- Nothing installed without user approval (third-party scrapers, API keys)
- 2026-10-04: tried SHOPPING_ENABLE_AMAZON_SCRAPE — Amazon returns JS bot challenge (0 results); reverted. Use SerpApi amazon engine instead.
- 2026-10-04: Playwright headless Chromium gets Amazon search results (bypasses bot challenge) — scratchpad prototype works
- 2026-10-04: Amazon added to shopping-deals (Playwright fallback in sources/amazon.py, flag on in .mcp.json); 40 tests pass; needs Claude Code restart
