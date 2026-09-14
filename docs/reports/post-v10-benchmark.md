# Post-v10 source-aware human benchmark

## Frozen boundary and honest denominators

Frozen production snapshot: `data/audits/post_v11/baseline_verified.db`, **382,038,016 bytes**, SHA-256 `ad60636f8924b85798912e6cd8fb8b241c926043370eaae18d576fc0966e5788`; independently checked with SQLite `integrity_check=ok`. The v10 observation begins at the reported deployed process start **2026-09-10 22:43:40.573 UTC** and ends at the frozen last completed scan **2026-09-13 23:41:32.471821 UTC**. The natural-history tables exclude directed requests and offline replay; current-rule replay is labeled separately below.

The latest supplied hunt has **27 raw references / 26 unique source-qualified items**. `e1206936947` occurs twice; its later “another good possible purchase” annotation supersedes the earlier unranked interpretation without inventing a second discovery. The [new manifest](../benchmarks/manual-hunt-2026-09-13.csv) preserves both. Its date identifies the supplied-evidence revision, not another known hunt date. The earlier [18-item manifest](../benchmarks/prior-hunt-18-2026-09-13.csv) and [purchase controls](../benchmarks/purchase-controls-2026-09-13.csv) remain separate, overlapping datasets.

| Natural-history measure | Later 26 unique | Earlier 18 |
| --- | ---: | ---: |
| Historically seen at prior frozen audit | 12 | 10 |
| Historically seen at this frozen audit | **13** | **10** |
| Naturally observed during the exact v10 window | 4 | 2 |
| Newly successful cached enrichment during v10 | 1 | 0 |
| Actual v10 delivered listings | 0 | 0 |

The one newly observed later-hunt item is `c1157326782`, a Hysteric Glamour jacket. Neither retained-ID counts nor details prove that all benchmark objects remained available throughout this window. Missing IDs have no retained natural encounter; unknown historical ranks, availability, prices, and exact manual-hunt timing remain unknown. Finite pinned evidence was retained; no benchmark row has an explicit retention-expiry marker that explains these misses.

## Every unique item in the later hunt

“Legacy only” means seen before v10 but not naturally re-observed in this window. Old Stage A context is not relabeled as a current-code rejection. All rows have **zero v10 sends**.

| Raw row / ID | Supplied feedback | Frozen natural outcome |
| --- | --- | --- |
| 1 `o1219575641` | Unranked interest | No retained natural ID/card/detail |
| 2 `n1217516540` | A bit too expensive | No retained natural ID/card/detail |
| 3 `r1227256998` | Wrong size; a bit too expensive | No retained natural ID/card/detail |
| 4 `n1238725647` | Approximately $46 Balenciaga jeans; strong possible ping | Still no retained natural ID/card/detail; earliest acquisition loss unresolved |
| 5 `f1214226865` | Too expensive but cool | No retained natural ID/card/detail |
| 6 `t1222582674` | Unranked interest | No retained natural ID/card/detail |
| 7 `g1201770622` | Unranked interest | No retained natural ID/card/detail |
| 8 `c1211862596` | Authenticity uncertain | Legacy only, six sightings; last September 7; no detail |
| 9 `f1051997767` | Authenticity uncertain | Legacy only, six sightings; last September 5; no detail |
| 10 + 27 `e1206936947` | Latest: another good possible purchase | Legacy only, two sightings; last September 2; no detail; duplicate URL is one item |
| 11 `e1244072587` | Unranked interest | No retained natural ID/card/detail |
| 12 `j1237781532` | Repeated manual interest | Still one September 6 broad sighting; legacy ineligible context; never inspected or re-exposed under v10 |
| 13 `h1238476063` | Subsequently bought manually; auth option reported | Naturally re-observed; five lifetime sightings; eligible mature-auction hat, **inspection-budget loss**, no detail |
| 14 `c1157326782` | Dislike color; conditional design interest | Newly found and enriched September 10 23:17; rejected weak directional value; later capped revisits |
| 15 `g1228389581` | Too expensive but cool | No retained natural ID/card/detail |
| 16 `w1230036935` | Unranked interest | No retained natural ID/card/detail |
| 17 `x1184359198` | Decent; not the approximately ¥200 purchased GU jeans | 22→36 sightings; actionable ¥1,990 buyout; repeatedly eligible/capped; **never inspected** |
| 18 `u1234723893` | Unranked interest | No retained natural ID/card/detail |
| 19 `x1243943183` | Expensive; uninteresting design | No retained natural ID/card/detail |
| 20 `r1229681678` | Much too expensive, cool tee | Legacy only; September 9 Stage A insufficient-card-evidence state; no detail |
| 21 `k1236929442` | Good price, personally unwanted | Legacy only; September 6 ineligible context; no detail |
| 22 `c1239443570` | Bot-alerted Number Nine shorts; purchased | Prior real September 8 ending send preserved; naturally re-observed under v10, no new send; old successful detail retained |
| 23 `d1184054169` | Pricey but considerable | Legacy only; September 7 ineligible context; no detail |
| 24 `b1231732915` | Uncertain, decent price | Legacy only; September 5 capped context; no detail |
| 25 `t1228669967` | Too expensive but good piece | Last seen September 10 before cutover; capped, no detail |
| 26 `g1223149511` | Possible purchase | Still no retained natural ID/card/detail |

## Demonstrated losses, not guessed depth fixes

### Manual-purchased Vetements hat: reached, then starved of inspection

`h1238476063` was naturally acquired again on **September 12 05:21 UTC**, broad Vetements, **page 1 / rank 88**, at **¥5,000 current bid and nine hours remaining**, with no recorded buyout. Its Stage A assessment had confirmed brand/category, `semantic_closet_inspection`, score **78**, no hard veto, and no previous inspection. It lost the budget.

Scan **14139** contained 100 cards, 73 processable listings, 28 eligible, 23 capped, and five successful details. Three selected archive candidates had already been inspected (`n1150767453`, `v1216951420`, `u1076635103`); two were first-time inspections (`l1173573344`, a ¥16,900 white cap, and `h1128131443`, a “vetements homme × LoroPiana” suit). All five were rejected after enrichment. This demonstrates an inspection-allocation problem for a user-purchased object, not a missing search or a proven profitable hat. The old cheap-first-inspection reservation does not cover this accessory/mature-bid closet path.

The user reports purchasing the hat for approximately $35 and seeing an authentication option. The recorded ¥5,000 current bid is not independently verified as their final purchase charge. Service eligibility is not proof of selection, payment, completion, or authentication success.

### Cheap GU × Undercover jeans: acquired repeatedly, not promoted by v10 fairness

`x1184359198` was seen **36 times**, up from 22, but never inspected. Latest retained backfill encounter: **September 13 14:22 UTC, page 1 / rank 9**, ¥1,890 current / **¥1,990 actionable buyout**. Its Stage A score was **82.2**, collaboration identity, valid denim, no severe veto, and cheap-value inspection candidacy.

Scan **16103** had 100 cards, 75 processable listings, 48 eligible, 47 capped, and one detail. The single winner was a first-inspection ¥1,980 GU/Undercover waffle tee, `j1244410378`, classified **core** with Stage A score **110.21**; it later failed directional value (56, heat 20). This is not solely “previous winners take every slot”: relationship/quality ranking and persistent uninspected-candidate fairness also need examination. The named benchmark must not receive an ID-specific override.

### Repeated Vetements tee: old inventory not reached again

`j1237781532`, `VETEMENTS◆Tシャツ/M/コットン/BLK/プリント`, remains at its sole **September 6 23:32 UTC** broad observation, ¥14,960 buyout, legacy ineligible context, no detail. During v10, Vetements received **13 broad scans / 16 pages**, 26 buyout scans, 21 ending scans, and four older-inventory backfill scans. The family was serviced, but this listing did not appear in retained observations. Its historical rank, current availability, and filter eligibility at each service time are unavailable. A targeted lookup today would not establish historical natural recall.

### Balenciaga jeans: still an unresolved acquisition gap

`n1238725647` has no durable encounter despite Balenciaga receiving **12 broad scans / 17 pages**, 35 buyout scans, 35 ending scans, and eight backfill scans / ten pages. This excludes “no family service” as the simple explanation, not filter, ordering, depth, parser, or availability hypotheses. No current-card Stage A rejection exists to replay.

The user reports **tag 34 / flat waist 49 cm / marketplace XS**. Preserve that contradiction without claiming intended fit, code meaning, verified model identity, or independent comps from mirrored seller stock. The user-reported $46 price is not a verified historical buyout or landed amount.

## Earlier 18-item hunt, separately

| IDs | Frozen result |
| --- | --- |
| `b1157261953`, `n1204220076`, `k1222800544`, `g1242293701`, `w1228214899`, `p1222030094`, `l1174555515` | Seven supported-source items still have no natural encounter |
| `z609539002` | Separate PayPay/Flea Market source-coverage limitation, not an auction scoring rejection |
| `d1215306825` | Re-observed under v10; 139→165 sightings; latest insufficient-card-evidence rejection, no detail |
| `q1240677210` | Re-observed under v10; 150→179 sightings; last inspection attempt September 13 11:12, old September 4 successful detail remains; fresh success **not** established |
| `d1239615529`, `j1237781532`, `o1221701770`, `u1225470712`, `w1219596150`, `d1214836782`, `c1226698756`, `n1190713130` | Eight previously seen items not re-observed during v10; no new delivery |

The cargo retry's compact event history deduplicates identical repeated failures, so the event's old timestamp does not erase the newer `last_inspected_at`. Its latest “candidate_budget” classification likewise does not prove a completed negative assessment. Distinguish attempted detail, successful detail, and current selection state.

## Current offline diagnostic replay, per unique URL

Executed **2026-09-14 00:36:38 UTC**, decision version `ultra-refinement-v11`, through the actual `benchmark_listings` / `diagnose_listing` implementation against the read-only hydrated copy of the same frozen snapshot. No source requests, database writes, new inspections, comp retrieval, or Discord sends occurred. Complete output is retained locally in `data/audits/post_v11/benchmark_current_replay.json`.

This recomputes final eligibility using retained latest card fields and any retained detail. It **does not simulate current Stage A selection, rebuild historical comps, or prove present availability**. Old relative countdowns remain observations, not today's auction clock. It is neither natural recall nor a pristine holdout. Every replayable row returned **no final route**; that is not proof that the underlying item is uninteresting, particularly when richer evidence was never acquired.

Compact outcome key: **type unresolved** = `category_mismatch`; **weak directional** = `missing_us_valuation_but_directional_weak`; **early auction** = `immature_auction`; **diffusion/value** = `diffusion_low_value`; **auth risk** = `seller_risk`. **Unavailable** means no retained listing fields to replay, not a rule rejection. URLs and source-qualified identities are preserved in the linked manifests; all rows below are auction-source `buyee` except the explicitly separated PayPay row.

### Later hunt: 27 references / 26 unique URLs

| Raw row / unique ID | Retained identity in current replay | Current offline outcome |
| --- | --- | --- |
| 1 `o1219575641` | Unavailable | Unavailable |
| 2 `n1217516540` | Unavailable | Unavailable |
| 3 `r1227256998` | Unavailable | Unavailable |
| 4 `n1238725647` | Unavailable | Unavailable; no Balenciaga-card rejection can be reconstructed |
| 5 `f1214226865` | Unavailable | Unavailable |
| 6 `t1222582674` | Unavailable | Unavailable |
| 7 `g1201770622` | Unavailable | Unavailable |
| 8 `c1211862596` | Vetements; type unresolved | Type unresolved |
| 9 `f1051997767` | Vetements; type unresolved | Type unresolved |
| 10 + 27 `e1206936947` | Vetements tee | Early auction; retained countdown 24h |
| 11 `e1244072587` | Unavailable | Unavailable |
| 12 `j1237781532` | Vetements tee | Weak directional; fixed-price recognized |
| 13 `h1238476063` | Vetements accessory / cap | Weak directional; 9h mature bid recognized |
| 14 `c1157326782` | Hysteric Glamour jacket | Weak directional; retained detail used |
| 15 `g1228389581` | Unavailable | Unavailable |
| 16 `w1230036935` | Unavailable | Unavailable |
| 17 `x1184359198` | GU × Undercover denim | Weak directional; **actionable ¥1,990 buyout recognized** |
| 18 `u1234723893` | Unavailable | Unavailable |
| 19 `x1243943183` | Unavailable | Unavailable |
| 20 `r1229681678` | mastermind Japan tee | Early auction; retained countdown 13h |
| 21 `k1236929442` | Number Nine; type unresolved | Type unresolved |
| 22 `c1239443570` | Number Nine trousers / shorts | Weak directional on latest fields; prior purchased send remains valid |
| 23 `d1184054169` | Rick Owens diffusion hoodie | Diffusion/value |
| 24 `b1231732915` | Saint Laurent hoodie | Weak directional |
| 25 `t1228669967` | Raf Simons tee | Weak directional |
| 26 `g1223149511` | Unavailable | Unavailable |

**13 available / 13 unavailable; zero replay routes.** The hat and GU jeans remain inspection-recall cases even though their retained card-only evidence does not establish a final value thesis. For `x1184359198`, maturity explicitly reports `price_actionable=true` and `actionable_price_kind=buyout`; directional score is 77 but its thesis remains unset because substantive piece-quality evidence is insufficient. The first replay exposed a misleading `immature_auction` fallback for this weak-but-actionable buyout; the targeted branch correction now reports its actual weak-value rejection without lowering final standards. The original purchased `c1239443570` alert is historical evidence, not invalidated by replaying later fields without its original valuation inputs.

### Earlier hunt: 18 unique URLs, kept separate

| Unique ID | Retained identity in current replay | Current offline outcome |
| --- | --- | --- |
| `b1157261953` | Unavailable | Unavailable |
| `n1204220076` | Unavailable | Unavailable |
| `k1222800544` | Unavailable | Unavailable |
| `d1215306825` | Vetements hoodie | Early auction; retained countdown 16h |
| `d1239615529` | Vetements tee | Weak directional |
| `j1237781532` | Vetements tee | Weak directional; overlapping item, not another discovery |
| `g1242293701` | Unavailable | Unavailable |
| `o1221701770` | Undercover hoodie | Weak directional |
| `w1228214899` | Unavailable | Unavailable |
| `q1240677210` | Number Nine trousers / cargo | Weak directional; old successful detail, not a fresh successful inspection |
| `u1225470712` | Number Nine trousers | Weak directional |
| `w1219596150` | Number Nine; type unresolved | Type unresolved |
| `d1214836782` | Rick Owens diffusion; type unresolved | Type unresolved |
| `c1226698756` | Saint Laurent hoodie | Weak directional |
| `p1222030094` | Unavailable | Unavailable |
| `l1174555515` | Unavailable | Unavailable |
| `n1190713130` | King Baby jewelry | Auth risk; sensitive-category evidence, seller history and photos insufficient |
| `buyee_paypay:z609539002` | Unsupported acquisition source | Not replayed; source-scope limitation |

**10 available / 7 unavailable supported-source / 1 unsupported; zero replay routes.** Unsupported Shopping Prada remains a separate purchase control below, not silently inserted into either denominator. These results expose continuing evidence and subjective-value limitations; they do not justify lowering all final thresholds or treating every current rejection as correct.

## Purchase controls and source identity

- **GU × Undercover `t1233238196`: confirmed historical bot send September 3 19:08:09 UTC**, buyouts/asymmetric value, **¥200 fixed buyout**, detail condition “No obvious damages/dirt.” The user reports buying it. It is neither the early Number Nine provisional bid nor the later ¥1,990 jeans. No v10 conversion is claimed.
- **Number Nine shorts `c1239443570`:** prior September 8 actual ending send and user purchase remain positive; not a fresh v10 sale or demonstrated resale profit.
- **Stüssy × mastermind `h1223997057`:** prior September 10 ending delivery remains a friend-purchase report, not a verified transaction price.
- **Shopping Prada `buyee_shopping:ssol-shopping_2348621003856`:** no supported-auction encounter or cached product evidence exists. Its full Shopping ID is preserved. No auction-equivalent stock has been established; absence of an adapter is a source-scope limitation, not a price/taste rejection. No new Shopping acquisition is enabled.
- **Legacy Yahoo hat URL:** aliases to the same `buyee:h1238476063` history as the canonical JDirectItems auction URL. Previously the diagnostic rejected that alias; it no longer hides the real post-acquisition budget failure.

## Diagnostic-only repairs

Supported-host/path normalization now accepts the legacy auction alias and explicitly separates Shopping and PayPay identities. Source-column contradictions and foreign-host auction-shaped paths are rejected. Batch diagnostics perform one read-only join/replay per unique item while retaining raw-reference rows, unique denominators, and annotation history. Source-qualified durable send events are exposed separately from latest AlertState, with an explicit 100-event display limit and full count; absence of new ledger events does not erase legacy delivery evidence.

No benchmark ID receives production eligibility, scheduling, scoring, or delivery privileges. These manifests do not overwrite runtime feedback ownership. Historical observations above are frozen before repairs; current offline replay is explicitly separate, and post-deployment natural outcomes belong in a separately bounded validation window.

## Human search method versus BuyEye

The existing architecture already separates current-bid ceilings from buyout ceilings, auction source from unsupported inventory, ending sorting from newly listed fixed-price discovery, category slices, and slower backfill. Those methods did reach the manual-purchased hat and cheap GU jeans; **their demonstrated bottleneck is inspection survival**, not another query family or page count.

The repeated Vetements tee and unobserved Balenciaga jeans still expose an unresolved coverage/availability question despite meaningful family service. Their unknown historical ranks do not justify copying a universal third page, 16-hour rule, or lower price ceiling. Fixing diagnostic source aliases improves attribution at **zero source-load cost**; any selection repair should reuse existing inspection slots and report the displaced candidates. Better actual sourcing remains unproved until natural post-repair observation, not merely a cleaner report or fewer false positives.
