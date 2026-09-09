# Post-v7 discovery, scheduling, and retained human benchmark audit

Frozen production interval: **2026-09-09 05:48:21.021586–14:12:11.215980 UTC**, 8.397 hours, deployed commit `55eae149626188ce381d3399711fa9cc0b8b97fb`. All metrics below are observations from read-only production SQLite. The unchanged scanner ran naturally; no forced search, detail request, or alert was used for this audit.

The v7 process has only 8.4 hours of recorded operation at this cutoff. References to user observations over several days cannot be assigned entirely to this build. The audit does not claim an overnight/day-long window beyond its timestamps.

## Sustained operation and coverage

Every one of the 42 configured families received a successful scan in **each** of three equal 2h48 slices. The sustained discovery failure from older checkpoints is not demonstrated here.

| Equal slice | Scans | Cards | Lifetime-first | Fresh-card share | Duplicate share | Stage B | Finals | Alerts | Successful families |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 05:48–08:36 | 156 | 8,074 | 3,011 | 37.29% | 24.20% | 186 | 4 | 2 | 42 |
| 08:36–11:24 | 156 | 9,580 | 2,178 | 22.73% | 34.35% | 217 | 6 | 5 | 42 |
| 11:24–14:12 | 124 | 6,158 | 1,600 | 25.98% | 15.22% | 177 | 6 | 5 | 42 |

The final slice had less scan throughput but preserved final/alert output. Tee detail observations increased 19 → 20 → 33; denim 3 → 4 → 12. Footwear stayed 15 → 15 → 14. Five most repeated strategies accounted for 13.46%, 10.90%, and 11.29% of scans, respectively. Fresh inventory declines after initial sampling, while final output persists. This supports preserving scheduler allocation.

## Lane and strategy utilization

| Lane | Scans | Cards | First-ever IDs | Stage B | Finals | Alerts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Broad | 154 | 14,246 | 6,018 | 294 | 7 | 6 |
| Medium | 61 | 4,204 | 578 | 158 | 2 | 1 |
| Surgical | 20 | 439 | 36 | 21 | 3 | 3 |
| Market structure | 201 | 4,923 | 157 | 107 | 4 | 2 |

Observed scan shares were 35.3% broad, 14.0% medium, 4.6% surgical, and 46.1% market, close to existing 35/15/5/45 targets. All 20 surgical scans serviced different strategies: narrow discovery was not monopolized by repeated collection searches.

| Market strategy | Scans | Cards | First-ever IDs | Stage B | Finals | Alerts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Newly posted buyout | 71 | 714 | 26 | 27 | 0 | 0 |
| Ending | 50 | 2,563 | 123 | 40 | 2 | 1 |
| Closing hour | 41 | 167 | 0 | 8 | 0 | 0 |
| Persistent exploration | 25 | 978 | 8 | 20 | 1 | 1 |
| Price band | 14 | 501 | 0 | 12 | 1 | 0 |

Market inventory mostly revisits known listings. Revisits can still generate useful new maturity/price decisions: three of four market finals came from ending/price-band scans and one from persistent Rebuild inventory. Neither novelty alone nor a zero-alert 8-hour sample justifies deleting these lanes.

257/448 current strategies were serviced: broad 110/149, medium 37/50, surgical 20/133, market 90/116. The query-state snapshot had 25 never-run strategies: ten recently added Celine/Louboutin surgical searches and 15 market backfills. These counters were read shortly after the cutoff; event totals above remain the exact frozen metric.

At about 52 scans/hour, a 5% surgical allocation yields roughly 2.6 scans/hour for 133 strategies—over 51 hours for one fully equal pass. The 51 explore strategies get approximately 2.34 scans/hour—about 22 hours for one pass. Configured cooldown is earliest eligibility, not a guaranteed service interval. Unserved narrow/backfill strategies after 8.4 hours are not by themselves proof of a scheduler fault.

## Family attribution

This is attribution to the **scheduled search family**, including genuine neighboring results. The main family-funnel audit separately reports detected item identity. Processable is a scan-level pass count; lifetime-first is the new stable marketplace ID count.

| Scheduled family | Scans | Failed | Cards | First-ever | Stage A eligible | Budgeted | Stage B | Detail success | Finals | Alerts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| alexander_mcqueen | 12 | 1 | 548 | 3 | 94 | 86 | 8 | 5 | 0 | 0 |
| alyx | 5 | 1 | 223 | 8 | 104 | 96 | 8 | 4 | 0 | 0 |
| balenciaga | 11 | 0 | 478 | 3 | 104 | 93 | 11 | 9 | 0 | 0 |
| celine | 42 | 1 | 1839 | 834 | 302 | 233 | 69 | 63 | 1 | 1 |
| christian_louboutin | 33 | 2 | 1605 | 402 | 44 | 23 | 21 | 13 | 0 | 0 |
| chrome_hearts | 10 | 0 | 814 | 170 | 120 | 95 | 25 | 22 | 0 | 0 |
| comme_des_garcons_homme_plus | 5 | 1 | 483 | 174 | 172 | 164 | 8 | 7 | 0 | 0 |
| dior_homme | 12 | 0 | 602 | 55 | 120 | 97 | 23 | 19 | 0 | 0 |
| dolce_gabbana | 5 | 0 | 542 | 427 | 165 | 156 | 9 | 9 | 0 | 0 |
| enfants_riches_deprimes | 4 | 0 | 65 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| final_home | 4 | 0 | 320 | 0 | 72 | 65 | 7 | 4 | 0 | 0 |
| gaboratory | 6 | 0 | 102 | 2 | 6 | 2 | 4 | 4 | 0 | 0 |
| gucci | 5 | 1 | 371 | 289 | 109 | 102 | 7 | 6 | 1 | 1 |
| helmut_lang | 5 | 1 | 428 | 62 | 61 | 55 | 6 | 6 | 0 | 0 |
| hysteric_glamour | 4 | 1 | 400 | 372 | 260 | 254 | 6 | 6 | 0 | 0 |
| if_six_was_nine | 10 | 0 | 318 | 1 | 5 | 1 | 4 | 2 | 0 | 0 |
| issey_miyake | 5 | 0 | 513 | 489 | 235 | 225 | 10 | 9 | 0 | 0 |
| jean_paul_gaultier | 6 | 2 | 315 | 50 | 117 | 111 | 6 | 6 | 0 | 0 |
| junya_watanabe | 6 | 2 | 491 | 291 | 199 | 190 | 9 | 8 | 0 | 0 |
| kapital | 5 | 1 | 497 | 243 | 197 | 190 | 7 | 4 | 0 | 0 |
| king_baby | 6 | 0 | 50 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| lgb | 4 | 0 | 585 | 208 | 80 | 73 | 7 | 6 | 0 | 0 |
| margiela | 31 | 0 | 1394 | 121 | 220 | 167 | 53 | 47 | 0 | 0 |
| mastermind_japan | 5 | 0 | 378 | 15 | 124 | 116 | 8 | 7 | 0 | 0 |
| miu_miu | 5 | 1 | 302 | 218 | 75 | 68 | 7 | 7 | 0 | 0 |
| needles | 26 | 0 | 600 | 127 | 71 | 62 | 9 | 7 | 1 | 0 |
| number_nine | 36 | 0 | 1156 | 68 | 346 | 277 | 69 | 60 | 3 | 1 |
| oakley | 4 | 1 | 400 | 390 | 313 | 307 | 6 | 6 | 3 | 3 |
| prada | 9 | 0 | 745 | 181 | 118 | 93 | 25 | 23 | 0 | 0 |
| raf_simons | 5 | 2 | 320 | 122 | 78 | 74 | 4 | 3 | 0 | 0 |
| rebuild_by_needles | 5 | 0 | 277 | 1 | 43 | 36 | 7 | 6 | 3 | 3 |
| rick_owens | 15 | 0 | 707 | 76 | 48 | 35 | 13 | 10 | 0 | 0 |
| roberto_cavalli | 4 | 0 | 500 | 9 | 109 | 102 | 7 | 7 | 0 | 0 |
| roen | 7 | 0 | 452 | 1 | 202 | 193 | 9 | 7 | 2 | 2 |
| saint_laurent | 33 | 2 | 991 | 261 | 349 | 309 | 40 | 33 | 0 | 0 |
| takahiromiyashita_thesoloist | 4 | 0 | 500 | 75 | 233 | 224 | 9 | 9 | 0 | 0 |
| undercover | 12 | 1 | 1297 | 230 | 621 | 598 | 23 | 19 | 1 | 1 |
| vetements | 8 | 0 | 243 | 1 | 69 | 59 | 10 | 7 | 0 | 0 |
| vintage_band_tees | 8 | 1 | 713 | 81 | 269 | 263 | 6 | 3 | 0 | 0 |
| vivienne_westwood | 4 | 0 | 500 | 267 | 263 | 255 | 8 | 6 | 1 | 0 |
| y_project | 6 | 2 | 348 | 122 | 38 | 33 | 5 | 4 | 0 | 0 |
| yohji_yamamoto | 4 | 1 | 400 | 337 | 36 | 29 | 7 | 6 | 0 | 0 |

Celine consumed 9.6% of scans and 11.9% of Stage B but only one final/alert. Oakley and Rebuild each generated three alerts from four/five scans respectively. Alert concentration therefore cannot be explained solely by discovery allocation: product-scope and final taste/value evaluation materially drive it.

## Celine, Louboutin, and footwear

Celine: 42 scans, 1,839 cards, 834 first-ever IDs, 69 Stage B, 63 successful details, one final and one alert. Louboutin: 33 scans, 1,605 cards, 402 first-ever IDs, 21 Stage B, 13 details, zero finals. Both received sustained service. Celine collection priority warrants attention-quality evaluation; removing the family is unsupported.

The 18 dedicated men's-footwear market strategies together produced **42 scans, 341 cards, 31 first-ever IDs, 25 Stage B, 17 details, zero finals**. Balenciaga, legacy YSL, and Louboutin shoe backfills had not yet run, within the new-backfill exploration queue.

All seven Celine HOMME men's-shoe scans (three buyout, three ending, one backfill) returned zero cards. Other brands returned inventory from the same category. This flags brand/alias formulation or current filtered inventory; it does not establish a broken category filter. No automatic query/category expansion is justified by these seven zeroes alone.

Three Balenciaga sneaker benchmark IDs and the vintage YSL boots remain unseen. Cheap footwear reached Stage B elsewhere, so the misses cannot be attributed universally to a footwear veto or shipping cost without item-level evidence.

## Depth and seller concentration

New page-one IDs: 5,359, of which 183 have a retained first-inspection timestamp in the interval and six a latest final decision. New page-two IDs: 1,430, of which 28 were inspected and zero have a latest final decision. Page two contributed 21.1% of first-ever inventory and found one real human benchmark object. Preserve current bounded depth; page three has no demonstrated marginal final yield.

Latest detail-cache seller samples by equal third:

| Slice | Cached details with seller | Distinct seller labels | Top seller share | Top five share |
| --- | ---: | ---: | ---: | ---: |
| First | 147 | 89 | 11.56% | 24.49% |
| Middle | 167 | 106 | 7.19% | 24.55% |
| Last | 127 | 91 | 6.30% | 20.47% |

The cache overwrites older observations and omits uncached failures, so this is not a complete seller transaction history. It shows no rising late-run seller concentration in this sample. No seller penalty is justified.

## Human benchmark

The existing read-only diagnostic functions were used on all 18 retained IDs and prior annotations. **Nine of 18 have now been seen**, versus eight previously; none has alerted. Eight Yahoo/JDirect objects and one PayPay object remain unseen. The supported-source misses cannot be assigned to current availability because no live detail requests were made.

Number (N)ine 5,000 JPY cargos `u1225470712` were first discovered under v7 at 13:46 UTC, through broad Number (N)ine page two/rank 77. They were eligible for closet inspection and unknown-interesting, score 97, but candidate-budgeted.

Vetements layered hoodie `d1215306825` reappeared at 10:24 UTC, 25,000 JPY current and two hours remaining. It qualified through ending-auction, score 83.75, but was budgeted.

The 9,900 JPY Number (N)ine cargos `q1240677210` also have a recent budgeted trace, but an older detail-cache observation proves they were historically inspected. Its current replay remains directionally weak. Latest-outcome labels must not erase the historical inspection.

The other six previously seen probes did not acquire new detail evidence under v7. Current hypothetical replays are not historical production outcomes and do not reconstruct unavailable sold comps. PayPay remains diagnostic-only.

| ID | Retained object / annotation | Observed outcome |
| --- | --- | --- |
| b1157261953 | Balenciaga red sneakers | Never seen |
| n1204220076 | Balenciaga beige sneakers | Never seen |
| k1222800544 | Balenciaga sneakers | Never seen |
| d1215306825 | Vetements layered hoodie, ~25k JPY | Re-seen v7, eligible mature auction, budgeted; no detail |
| d1239615529 | Vetements cartoon tee, ~15.4k JPY | Old broad rejection; no v7 sighting/detail |
| j1237781532 | Vetements printed tee, ~14.96k JPY | Old broad rejection; no v7 sighting/detail |
| g1242293701 | Hysteric graphic tee; too expensive but cool | Never seen |
| o1221701770 | Undercover 23AW hoodie | Old insufficient-card rejection; no v7 sighting/detail |
| w1228214899 | Undercover hoodie; too expensive but cool | Never seen |
| q1240677210 | Number (N)ine 9.9k JPY cargos; too expensive but cool | Historical detail, weak directional value; latest v7 trace budgeted |
| u1225470712 | Number (N)ine 5k JPY cargos | Newly seen v7 page two; eligible but budgeted; no detail |
| w1219596150 | Number (N)ine fleece, ~6k JPY | Never seen |
| d1214836782 | Rick DRKSHDW cargos, ~33.66k JPY | Legacy truncated identity, no v7 sighting/detail |
| c1226698756 | Saint Laurent hoodie, ~20.46k JPY | Old insufficient-card rejection; no v7 sighting/detail |
| p1222030094 | Vintage YSL jacket; might buy | Never seen |
| l1174555515 | Vintage YSL Chelsea boots; possible buy | Never seen |
| n1190713130 | King Baby braided silver/leather bracelet | Old insufficient-card rejection; auth evidence incomplete |
| z609539002 | PayPay King Baby cross chain | Unsupported acquisition-source miss |

## Minimum supported changes

1. Query hygiene only: omit designer augmentation when designer equals the complete canonical brand, removing `Rick Owens Rick Owens` and `Christian Louboutin Christian Louboutin`. Fix the Louboutin bags term so the generated query contains one メンズ. The duplicate Louboutin query consumed two scans, one failed, 100 cards, one Stage B, and no final. This removes two redundant specifications, reducing 448 → 446, without removing any family, collection, layer, or category. Preserve valid repeated names such as Miu Miu and Prada Miuccia Prada.

2. Keep scheduling and page limits unchanged. All families recur, finals persist, and query concentration remains bounded. Zero service for some narrow/new backfill specs is consistent with available capacity.

3. Reserve at most one **existing** detail-inspection slot for an already-eligible exact-family, category-valid, confidently typed garment/footwear card at actionable acquisition ≤10,000 JPY. The existing heat-heavy ranking and one-slot closet path can prioritize expensive cards over cheap plausible goods. Compute ordinary selection first; only when it contains no qualifying inexpensive card, replace its lowest-ranked compatible card. Preserve all global, market-rate, and per-path caps, including path cap zero. Explicit and structurally generic products, incomplete type, early bids, auth-sensitive categories, scope mismatches, and severe risk cannot claim this reservation. This changes neither Stage A eligibility nor final scoring/routing.

Feature tests cover retained query coverage, self-named designer cleanup, unchanged valid names, one compatible inspection replacement, no extra fetches, disabled reserve and paths, unchanged selection when an inexpensive card already won, market-rate limit, early-bid/buyout distinction, expensive price boundary, cheap footwear, and ordinary dress shoes versus distinctive Chelsea boots.

## Limits and follow-up evidence

No forced inspections or benchmark alerts were generated. Strong low-cost discoveries surviving the replacement rule must be validated through the next natural production interval. The rule can allocate an inspection to a cheap item later rejected for weak taste; it does not call that item a bargain. The longer-run balance of 446 searches still needs more than one 8.4-hour window. Exact final-quality, replacement-component, and status-incident analyses are handled in the companion production audit.
