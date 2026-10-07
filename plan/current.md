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

## Phase 2: quality vetting + new-item interview (user 2026-10-07)

### Goal
1. Never recommend a listing just because it is cheap. Every listing the daily run reports (top-N per
   item + every alert) gets a Claude verdict — buy / ok / avoid — with reasons, red flags, the
   standard/most-recommended product it is compared to, and a better-value pick if one is close.
2. When an item is added (to watchlist or wishlist), Claude first asks product-specific questions
   (e.g. power bank: wattage, wireless/MagSafe, built-in cable, capacity, size) and shows what each
   feature typically adds to the price, from live searches. Answers are stored on the item and drive
   both the search filters and the vetting.

### Constraints (from user)
- Cheaper model (Haiku). No direct Claude API usage: go through the LLM Gateway
  (`http://100.122.101.27:18789`, `POST /ask`, Bearer token from `~/.openclaw/openclaw.json`).
- Prices quoted must stay live (CLAUDE.md rule). Web "typical" prices are labelled as such.

### Gateway facts that shape the design (verified 2026-10-07 in gateway-delegate.py)
- Gateway's Claude runs Haiku with the instruction "Do NOT use any tools" -> **no web search**.
  So all evidence must be gathered by price_watch first and put in the prompt.
- Calls `claude --continue` in the project dir -> every call resumes the same conversation
  (context grows run over run; earlier listings can leak into later verdicts).
- One session per project at a time (409 if busy), 3 projects max, 120 s timeout; plain-text reply.

### Design
- Gateway project `searchproduct_vet` (instructions.md = vetting rubric; `context: "none"`).
  Reply format: one JSON object, parsed leniently (first {...} block); failure -> verdict `unvetted`.
- Evidence gathered by price_watch (no LLM tools needed):
  a. Amazon product page: brand, review count, star histogram, bought/month, bullets (amazon_detail.py, done).
  b. Today's other listings for the item (live) -> lets Claude name a better pick from real prices.
  c. Per-item reference products (`reference_queries`, e.g. "Anker power bank 10000mAh"): live Amazon
     search each run -> name-brand baseline price for the "vs standard" comparison.
  d. Web search snippets (D2): "<brand> <product> review" + "best <item> reddit/wirecutter", fetched by
     price_watch (websearch.py), cached per brand/query for 7 days.
- Cache verdict per listing+price for 7 days (store.vettings, done) -> only new/changed listings cost a call.
- Calls are serial (gateway per-project lock), ~10–20 s each; 2 items x 5 listings ~ 3 min worst case.
- Report: verdict column + per-item "Vetting" section. Digest: verdict on each alert line; best line
  shows the best non-avoid listing. Alerts on `avoid` listings are not sent; listed as "skipped".
- Interview: done by me (interactive Claude) when the user asks to add an item — rule in CLAUDE.md.
  I run live Amazon searches per feature tier, present "feature -> typical price add", ask, then write
  `requirements` (+ include/exclude/target/reference_queries) on the item.

### Subtasks
- [x] 14. config: item `requirements`; defaults vet / vet_model / vet_cache_days / vet_budget_usd
- [x] 15. amazon_detail.py: product-page facts parser + fetch
- [~] 16. vet.py + store.vettings cache (written for direct `claude -p`; runner must move to gateway)
- [ ] 17. Gateway runner: create `searchproduct_vet` project + instructions.md; `gateway.py` client
         (token, 409 retry, timeout, lenient JSON parse); drop claude-direct runner and vet_budget_usd
- [ ] 17b. Gateway `fresh` flag (D1) in openclaw-config llm-gateway.py + gateway-delegate.py
- [ ] 17c. websearch.py: web search snippets for evidence (D2), cached
- [ ] 18. `reference_queries` per item: live Amazon search, passed to vetting + shown in report
- [ ] 19. Wire into run_items; report/digest changes; avoid-verdict alerts skipped
- [ ] 20. Tests (fake gateway runner, parser, cache, report) + live run + adversarial review
- [ ] 21. CLAUDE.md: new-item interview rule; run the interview for power-bank + desktop-pc now
- [ ] 22. Docs: CLAUDE.md automation section, PROGRESS.md

### Decisions (user 2026-10-07)
- D1: add a per-request `fresh: true` option to the LLM Gateway (openclaw-config) -> no `--continue`.
- D2: price_watch gathers ALL evidence itself, including web searches (brand reputation, reviewer
  picks); the gateway is used only to turn evidence into a recommendation.
- D3: route through the gateway (not direct `claude -p`).

## Open assumptions
- Discord channel for alerts: unknown → set `notify.discord_target` in watchlist.json.
- Daily 08:00 run time chosen as default.

## Follow-ups (not done)
- [ ] eBay coverage: ebay_public scrape returns 403 every run → add `ebay` (official API) to sources once
      dev keys arrive, or a Playwright eBay fetcher. Laser item depends on eBay (0 results today).
- [ ] Set `notify.discord_target` (channel id) to get the daily digest + alerts on Discord.
- [ ] Review guessed targets/filters in watchlist.json (desk, laser, smart plug are guesses).
