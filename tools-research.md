# Product-search tools for Claude (researched 2026-10-04)

None installed yet. Verified = README/listing actually read; others are from search snippets only.

## MCP servers (give Claude live search tools)
| Name | Covers | Needs | Verified |
|------|--------|-------|----------|
| [jongan69/shopping-deals-mcp-server](https://github.com/jongan69/shopping-deals-mcp-server) | eBay, Amazon, Google Shopping (Walmart/Target/etc.), Craigslist, OfferUp, FB Marketplace; ranks cheapest, tax estimate | Python 3.10+; eBay dev keys; SerpApi key (optional, paid beyond free tier) for Google Shopping. Scraped sources may get blocked | yes (Glama listing; last update 2026-07-05) |
| [jlsookiki/secondhand-mcp](https://github.com/jlsookiki/secondhand-mcp) | FB Marketplace, eBay, Depop, Poshmark | `npx secondhand-mcp`; eBay keys optional; Chrome for Depop/Poshmark | yes (86 stars) |
| [ido6/facebook-marketplace-deals](https://github.com/ido6/facebook-marketplace-deals) | FB Marketplace + skill: like-for-like compare, scam flags, offer suggestions, price-drop watch | Node build, Playwright; login optional; Israel-focused but location configurable | yes (0 stars — very new) |
| [lulzasaur9192/marketplace-search-mcp](https://github.com/lulzasaur9192/marketplace-search-mcp) | Reverb, OfferUp, Craigslist, Swappa, Poshmark, StubHub, TCGPlayer, etc. | ? | no |
| BigGo MCP ([mcpmarket](https://mcpmarket.com/server/biggo)) | Price history across Amazon, eBay, Shopee | ? | no |
| Apify actors ([price-intelligence](https://apify.com/onetapstudio/price-intelligence-mcp), [Google Shopping](https://apify.com/oneary/google-shopping-price-comparison-engine-scraper/api/mcp)) | Amazon/Walmart/Target/eBay/Best Buy | Apify account, pay-per-result (~$1–7 / 1000) | no |

## Claude skills (instructions only; mostly use web search)
- [amazon-shopper-skill](https://github.com/shikhamishra379/amazon-shopper-skill) — Amazon search, compare, price history; stops before checkout
- [Price Comparator](https://mcpmarket.com/tools/skills/price-comparator) — Amazon/Walmart/eBay, landed cost incl. shipping, seller rating
- [Deal Hunter](https://mcpmarket.com/tools/skills/deal-hunter) — prices + coupon codes via Tavily API (key needed)
- [Price Hunter](https://mcpmarket.com/tools/skills/price-hunter) — global incl. JD/Pinduoduo

## DIY / reference
- [cyangjr/marketplace-scout](https://github.com/cyangjr/marketplace-scout) — polls FB Marketplace + Craigslist, filters, Discord alerts (good model for a price-drop watcher)
