# Sold-market evidence

## Connected source

### Verified U.S. sold records

Resale economics use only completed U.S.-market sales. The initial connected evidence is a manually
verified set of public Grailed sold pages in `config/us_sold_comps.yaml`. Each record must expose an
explicit sold marker and USD sold price. Active listings and reseller asks are forbidden.

The collector stores:

- source and query provenance;
- stable marketplace listing ID and URL;
- original title;
- original USD sold price and normalized JPY value;
- observed end date when present;
- retrieval and cache-expiry timestamps;
- condition, size, and shipping only when observed (currently usually unavailable).

Yahoo Japan ended-auction data remains available as acquisition-market context, but it is not wired
into resale FMV, opportunity scoring, or routing.

## Sources not connected

- **Live Grailed search:** individual public sold pages expose genuine prices, but the sold browse
  endpoint did not provide a reliable keyword interface and raw HTTP was blocked. No protection is bypassed.
- **eBay:** public sold-result HTML returned 403, and eBay's public Browse API officially excludes
  sold listings. Marketplace Insights is restricted, so no collector is enabled without authorized access.
- **Reseller taste corpora:** asking prices remain categorically excluded from FMV.

## Query and cache behavior

Queries progress from collection/era + item/material identity to item-level and finally brand-level.
Every candidate retains the query that found it. Broad retrieval does not make a broad result a valid
comp; structured matching still rejects incompatible category/item evidence.

SQLite caches each source/query for 72 hours by default, including empty results. Cached sold records retain source listing identity and normalized/original values. This prevents repeated external requests during continuous scans.

## Valuation boundaries

Only accepted piece-aware sold matches enter the robust range. Five or more values are trimmed at both tails. Confidence depends on usable count and average structured similarity. Sparse or weak evidence remains low confidence; no evidence produces no FMV.
