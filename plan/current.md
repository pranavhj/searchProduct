# Plan: daily price watch (tracked products + drop alerts)

Goal: a list of tracked products that is searched automatically every day; each run
writes a listing report and alerts (Discord / Windows toast) when a price drops sharply
or hits a target.

## What exists (assessment, 2026-10-05)
- `ShoppingDealsService.find_best_deals()` (tools/shopping-deals-mcp-server, venv py3.10)
  is importable directly — no MCP/Claude needed at run time. Sources: Amazon
  (Playwright fallback), eBay public, Google Shopping (SerpApi), Craigslist sfbay,
  OfferUp, FB Marketplace. Returns scored listings with price/shipping/tax.
- Location settings live only in `.mcp.json` env → must be injected before import.
- Discord sender: `D:\MyData\Software\openclaw-config\bin\discord-send.py --target <channel>`
  (used by flightchecker). No channel known for this project yet.
- Missing: watchlist file, price history store, drop detection, report, scheduler, notifier.

## Design
- `watchlist.json` (project root): items {id, query, sources?, condition, price_min,
  price_max, target_price, must_include[], exclude[], drop_pct, enabled}.
- Code: `tools/price_watch/` package, run with the shopping-deals venv via `pricewatch.cmd`.
- History: SQLite `data/price_watch.db` (stdlib, no new deps).
- Alerts: (a) best ≤ target_price, (b) best ≥ drop_pct below trailing 30-day median of
  daily bests, (c) a known listing's own price dropped ≥ drop_pct, (d) new listing ≤ target.
  Same listing+price never alerted twice.
- Schedule: Windows Task Scheduler, daily 08:00 (local machine needed for FB/Amazon
  Playwright + user env SERPAPI_API_KEY).

## Subtasks
- [x] 1. Config/watchlist loader + env injection from .mcp.json  (`config.py`)
- [x] 2. Fetch + relevance filter (include/exclude/price floor)    (`fetch.py`)
- [x] 3. SQLite store: runs, observations, alerts_sent            (`store.py`)
- [x] 4. Analyzer: baseline, drop + target + new-listing alerts    (`analyze.py`)
- [x] 5. Markdown report (dated + latest.md)                       (`report.py`)
- [x] 6. Notifier: Discord (if target set) + Windows toast         (`notify.py`)
- [x] 7. CLI: run / list / add / history / remove                  (`__main__.py`)
- [x] 8. Unit tests (fake service, temp DB)                        (`tests/`)
- [x] 9. Scheduler install/uninstall scripts + wrapper with logging
- [x] 10. Seed watchlist.json from wishlist.md items
- [x] 11. Live end-to-end run, inspect report + DB
- [x] 12. Adversarial review, fix findings
- [x] 13. Docs: CLAUDE.md automation section, PROGRESS.md

## Open assumptions
- Discord channel for alerts: unknown → set `notify.discord_target` in watchlist.json.
- Daily 08:00 run time chosen as default.

## Follow-ups (not done)
- [ ] eBay coverage: ebay_public scrape returns 403 every run → add `ebay` (official API) to sources once
      dev keys arrive, or a Playwright eBay fetcher. Laser item depends on eBay (0 results today).
- [ ] Set `notify.discord_target` (channel id) to get the daily digest + alerts on Discord.
- [ ] Review guessed targets/filters in watchlist.json (desk, laser, smart plug are guesses).
