# searchProduct

Personal shopping tracker: `wishlist.md` lists what the user wants; Claude searches for the best deals and records them there.

## Location
User lives in **Milpitas, CA 00000**. Prefer local pickup / closest listings (South Bay / SF Bay Area), then ship-to-00000 offers. Estimate tax at 9.375% (Milpitas — verify if it matters).

## Search tools (MCP, configured in `.mcp.json`)
- `shopping-deals` — new + used: Amazon, eBay, Google Shopping (needs `SERPAPI_API_KEY`), Craigslist (`sfbay`), OfferUp, FB Marketplace. Location env already set to Milpitas. Key tools: `find_best_deals`, `compare_prices`, `compare_area_prices`, `get_ebay_sold_comps`. Ignore its resale/vehicle-flip tools. Amazon is enabled (`SHOPPING_ENABLE_AMAZON_SCRAPE`): plain HTTP first, headless Playwright fallback when Amazon serves its bot challenge (prices exclude clip-on coupons).
  Installed at `tools/shopping-deals-mcp-server/` (venv in `.venv`). API keys go in that folder's `.env`, never in `.mcp.json`.
- `secondhand` — FB Marketplace, eBay, Depop, Poshmark via `npx secondhand-mcp`. **Always pass `location: "milpitas"`** (defaults to San Francisco) and a `radiusMiles` (~25).
- Fallback: plain WebSearch.

## Rule: only live, verified prices
Never quote a price as current unless it was fetched live this session from the seller/listing itself (MCP tool result, SerpApi, or browser automation). If a source can't be read (blocked, not configured), get it another way — Playwright (`python tools/amazon_prices.py "<query>"` for Amazon) or the Playwriter extension — before reporting. Deal-site posts / review articles are history, not prices: label them "past deal, date X" or leave them out. If no live price could be obtained, say so explicitly.

## Workflow
1. Read `wishlist.md`; for each item in `want`/`searching`, search both servers.
2. Compare total landed cost (price + shipping + tax); flag scam signals on used listings.
3. **Vet every listing you recommend — cheapest is not a recommendation.** For each pick check:
   brand/maker track record, rating + review count + 1-star share, recent complaints;
   knock-off signals (unknown/rebranded seller, specs implausible for weight or price, fake-review
   patterns); and compare against the standard / most-recommended product (established-brand
   best-seller, reviewer picks like Wirecutter / RTINGS / Stiftung Warentest) — what is lost vs it,
   and the price gap. Verdict per listing: buy / acceptable trade-off / avoid; name the better-value
   pick when a reputable option is close in price. Applies to price-watch alerts too.
4. Update the item's row + notes with date, price, source, link.

See `tools-research.md` for other tools considered.

## Price watch (daily tracked products) — `tools/price_watch/`
Tracked products live in `watchlist.json` (separate from `wishlist.md`, which is the human notes).
A Windows scheduled task (`searchProduct-PriceWatch`, daily 08:00, catches up after sleep) runs
`pricewatch.cmd run`: searches each enabled item via `ShoppingDealsService` (no MCP/Claude needed),
stores every relevant listing in `data/price_watch.db`, writes `reports/price-watch/latest.md`,
and alerts on: price ≤ `target_price`, best ≥ `drop_pct` below the 30-day median (after 3 days),
or a seen listing cutting its price ≥ `drop_pct`. Alerts → Discord (if `notify.discord_target`
set) + Windows toast; each listing alerted once per price level.
- Prices are sticker + shipping, **before tax**; `target_price` compares against that.
- Local pickup listings (FB/Craigslist/OfferUp) farther than the item's `max_miles` (straight line from
  Milpitas, geocoded via OpenStreetMap Nominatim, cached in the DB) or with unknown location are dropped;
  shippable listings are exempt. Default 25 mi; power bank 10, PC 40.
- Amazon is searched twice: default featured sort + cheapest-first within the item's price band
  (`tools/price_watch/amazon_cheap.py`) — page 1 of the featured sort misses cheap listings.
- Watch for per-part pricing (parts sales, motherboards "supporting 64GB"): keep excludes on spec items.

## Available Automation
- `pricewatch list | add "query" --target 30 --include "a|b" --exclude x | remove/enable/disable ID | history ID`
- `pricewatch run [--item ID] [--no-notify]` — manual run (`--no-notify` = report only)
- `tests\run_tests.cmd` — price_watch unit tests (shopping-deals venv)
- `scripts\install_schedule.ps1 [-Time 08:00] [-Uninstall]` — (re)register the daily task
- Logs: `data/logs/price_watch.log`. eBay public scrape returns 403 → eBay coverage needs the official API keys.
