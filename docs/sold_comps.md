# Sold-market evidence

## Connected source

### Yahoo Japan ended auctions

BuyEye queries Yahoo Japan's public `closedsearch` pages. These pages identify completed auctions and expose an explicit `落札` winning price for up to 180 days of history.

The collector stores:

- source and query provenance;
- stable auction ID and URL;
- original title;
- original JPY winning price and normalized JPY price;
- observed end date when present;
- retrieval and cache-expiry timestamps;
- condition, size, and shipping only when observed (currently usually unavailable).

Cards without an explicit `落札 ... 円` value are rejected. Active/current prices cannot enter the cache as sold evidence.

## Sources not connected

- **Grailed:** individual indexed listing pages can expose a sold marker and sold price, but no reliable public sold-search surface was verified. Search-engine discovery is not a production collector.
- **eBay:** a public completed-search request returned an interstitial without sold-result DOM. No collector is enabled.
- **Reseller taste corpora:** asking prices remain categorically excluded from FMV.

## Query and cache behavior

Queries progress from collection/era + item/material identity to item-level and finally brand-level. Every candidate retains the query that found it. Broad retrieval does not make a broad result a valid comp; structured matching still rejects incompatible category/item evidence.

SQLite caches each source/query for 72 hours by default, including empty results. Cached sold records retain source listing identity and normalized/original values. This prevents repeated external requests during continuous scans.

## Valuation boundaries

Only accepted piece-aware sold matches enter the robust range. Five or more values are trimmed at both tails. Confidence depends on usable count and average structured similarity. Sparse or weak evidence remains low confidence; no evidence produces no FMV.
