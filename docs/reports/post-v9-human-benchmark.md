# Post-v9 human benchmark: frozen history, not a forced alert test

## Boundary and datasets

Baseline: SQLite-consistent snapshot completed **2026-09-10 21:48:53 UTC**; last completed natural scan **12301 at 21:48:31 UTC**. Snapshot SHA-256: `7217e2977a74b6a2ccb59cb6f3cc0fef49f4df688fb9e098e8dd12adc15766cf`. The read-only diagnostic was captured at **21:52:57 UTC**, before semantic changes, using `d5acab827bbc1a2e0f35015ed0d5d657e88dab0e` / `ultra-refinement-v9`.

The reported 90-minute manual hunt has no supplied exact start/end. This audit cutoff is **not** an invented hunt-end timestamp. No browser/Discord/production write was used for these historical diagnostics. Root's three later isolated public HTTP lookups returned 403 and obtained no listing content; they are not scheduler discoveries.

[Exact 26-row input](../benchmarks/manual-hunt-2026-09-10.csv) preserves every raw URL and annotation, including query strings and eight blank ratings. [Detailed row-level output](post-v9-human-benchmark.csv) separately contains source/ID, title evidence, prices and timing, first/last observations, query/page/rank, retained Stage A state, inspection evidence, actual delivery, frozen replay, classification, and missing-history limits.

This is separate from the prior 18-item human benchmark, the 18-listing production replay, and calibration's 22 cases. `j1237781532` overlaps the two human hunts and is longitudinal, not an independent new item.

## Honest denominators

- **12/26 naturally observed (46.2%)**; 14 have no retained listing/encounter/detail evidence.
- **1/26 has successful cached detail evidence (3.8%; 1/12 of seen)** and an actual sent alert. The same item has the user's explicit bot-originated purchase report.
- Three seen objects retain candidate-budget evidence without a detail: GU × Undercover jeans, Raf Simons Printworks tee, and a legacy YSL sweatshirt.
- Six retain legacy Stage A ineligible outcomes without detail, one an explicit later Stage A rejection, and one lacks usable historical processing detail.
- Only three of the new benchmark's seen IDs were naturally re-observed under the latest v9 period: GU × Undercover jeans, the previously alerted Number (N)ine shorts, and the Raf tee. The other seen examples primarily test whether older inventory was reached again.

These are acquisition, inspection, and notification denominators—not a claim that the other 25 must alert or the 14 unobserved are verified bargains. Price objections, authenticity uncertainty, personally unwanted pieces, and blank annotations retain their original meaning.

## All 26 rows

“Offline replay” below uses frozen latest card/detail state without reconstructing historical comps. A legacy title or old relative countdown may be stale. Replay rejection is **not** asserted to be the historical reason the scanner rejected a listing.

| # / listing | User annotation (blank stays unrated) | Natural evidence through baseline | Frozen v9 offline replay |
| --- | --- | --- | --- |
| 1. [o1219575641](https://buyee.jp/item/jdirectitems/auction/o1219575641) | — (unrated) | No retained natural observation; pipeline-stage loss unknown | Unavailable: no cached listing |
| 2. [n1217516540](https://buyee.jp/item/jdirectitems/auction/n1217516540) | a bit too expensive | No retained natural observation; pipeline-stage loss unknown | Unavailable: no cached listing |
| 3. [r1227256998](https://buyee.jp/item/jdirectitems/auction/r1227256998) | not my size and just a bit too expensive | No retained natural observation; pipeline-stage loss unknown | Unavailable: no cached listing |
| 4. [n1238725647](https://buyee.jp/item/jdirectitems/auction/n1238725647) | $46 balenciaga jeans…cmon bro…would be great ping | No retained natural observation; pipeline-stage loss unknown | Unavailable: no cached listing |
| 5. [f1214226865](https://buyee.jp/item/jdirectitems/auction/f1214226865) | too expensive but cool piece | No retained natural observation; pipeline-stage loss unknown | Unavailable: no cached listing |
| 6. [t1222582674](https://buyee.jp/item/jdirectitems/auction/t1222582674) | — (unrated) | No retained natural observation; pipeline-stage loss unknown | Unavailable: no cached listing |
| 7. [g1201770622](https://buyee.jp/item/jdirectitems/auction/g1201770622) | — (unrated) | No retained natural observation; pipeline-stage loss unknown | Unavailable: no cached listing |
| 8. [c1211862596](https://buyee.jp/item/jdirectitems/auction/c1211862596) | not sure if authentic, would have to look | Seen 6×; last 2026-09-07 16:50 UTC. Legacy Stage A ineligible; no detail | type unresolved; category_mismatch |
| 9. [f1051997767](https://buyee.jp/item/jdirectitems/auction/f1051997767) | not sure if authentic, would have to look | Seen 6×; last 2026-09-05 11:21 UTC. Legacy Stage A ineligible; no detail | type unresolved; category_mismatch |
| 10. [e1206936947](https://buyee.jp/item/jdirectitems/auction/e1206936947) | — (unrated) | Seen 2×; last 2026-09-02 19:49 UTC. Seen; processing history unavailable | t_shirt; immature_auction |
| 11. [e1244072587](https://buyee.jp/item/jdirectitems/auction/e1244072587) | — (unrated) | No retained natural observation; pipeline-stage loss unknown | Unavailable: no cached listing |
| 12. [j1237781532](https://buyee.jp/item/jdirectitems/auction/j1237781532) | — (unrated) | Seen 1×; last 2026-09-06 23:32 UTC. Legacy Stage A ineligible; no detail | t_shirt; missing_us_valuation_but_directional_weak |
| 13. [h1238476063](https://buyee.jp/item/jdirectitems/auction/h1238476063) | low price, also includes authentication guarantee from Buyee | Seen 3×; last 2026-09-03 13:56 UTC. Legacy Stage A ineligible; no detail | accessory; immature_auction |
| 14. [c1157326782](https://buyee.jp/item/jdirectitems/auction/c1157326782) | hate color but if piece was black or other color its cool | No retained natural observation; pipeline-stage loss unknown | Unavailable: no cached listing |
| 15. [g1228389581](https://buyee.jp/item/jdirectitems/auction/g1228389581) | too expensive but cool | No retained natural observation; pipeline-stage loss unknown | Unavailable: no cached listing |
| 16. [w1230036935](https://buyee.jp/item/jdirectitems/auction/w1230036935) | — (unrated) | No retained natural observation; pipeline-stage loss unknown | Unavailable: no cached listing |
| 17. [x1184359198](https://buyee.jp/item/jdirectitems/auction/x1184359198) | not as good as the $1 the bot found but decent lol | Seen 22×; last 2026-09-10 19:16 UTC. Eligible/capped; no detail | denim; immature_auction |
| 18. [u1234723893](https://buyee.jp/item/jdirectitems/auction/u1234723893) | — (unrated) | No retained natural observation; pipeline-stage loss unknown | Unavailable: no cached listing |
| 19. [x1243943183](https://buyee.jp/item/jdirectitems/auction/x1243943183) | a bit too expensive, design isn’t anything cool | No retained natural observation; pipeline-stage loss unknown | Unavailable: no cached listing |
| 20. [r1229681678](https://buyee.jp/item/jdirectitems/auction/r1229681678) | way too expensive, but cool tee | Seen 1×; last 2026-09-09 00:34 UTC. Stage A ineligible at recorded auction state | t_shirt; immature_auction |
| 21. [k1236929442](https://buyee.jp/item/jdirectitems/auction/k1236929442) | good price, wouldn’t buy piece | Seen 1×; last 2026-09-06 13:47 UTC. Legacy Stage A ineligible; no detail | type unresolved; category_mismatch |
| 22. [c1239443570](https://buyee.jp/item/jdirectitems/auction/c1239443570) | bot sent this before, bought them just now | Seen 71×; last 2026-09-10 17:06 UTC. Actual ending alert + user-reported purchase | denim; immature_auction |
| 23. [d1184054169](https://buyee.jp/item/jdirectitems/auction/d1184054169) | a bit pricey but considerable purchase | Seen 4×; last 2026-09-07 03:53 UTC. Legacy Stage A ineligible; no detail | hoodie; diffusion_low_value |
| 24. [b1231732915](https://buyee.jp/item/jdirectitems/auction/b1231732915) | iffy but decent price | Seen 1×; last 2026-09-05 04:15 UTC. Eligible/capped; no detail | hoodie; missing_us_valuation_but_directional_weak |
| 25. [t1228669967](https://buyee.jp/item/jdirectitems/auction/t1228669967?conversionType=jdirectitems_auction_DirectSearch) | quite a bit too much but good piece | Seen 3×; last 2026-09-10 20:20 UTC. Eligible/capped; no detail | t_shirt; missing_us_valuation_but_directional_weak |
| 26. [g1223149511](https://buyee.jp/item/jdirectitems/auction/g1223149511?conversionType=jdirectitems_auction_DirectSearch) | may buy these | No retained natural observation; pipeline-stage loss unknown | Unavailable: no cached listing |

## Five anchors

### n1238725647 — user-highlighted “$46 Balenciaga jeans”

No retained natural listing, encounter, detail, or alert exists at the baseline. The user's strong alert-interest and price observation are preserved; the line, condition, garment details, current/buyout price basis, and actual landed cost could not be resolved from the snapshot. An isolated post-baseline public lookup returned 403. This is an unresolved acquisition/content gap, **not a demonstrated Stage A rejection and not a verified arbitrage claim**.

### c1239443570 — actual bot-originated purchase

The resolved item is **Number (N)ine distressed denim shorts**, titled `Number nine ショーツ ハーフパンツ デニム ナンバーナイン ダメージ加工`. It is not the earlier GU purchase.

Natural first observation: September 5, 13:32 UTC, Number (N)ine market-ending search. Actual initial delivery: **September 8, 13:00:02 UTC**, `ending / asymmetric_value`, at **¥5,500 current bid**. The retained event history, cached detail, and persistent alert state corroborate the send. Detail category: Men's > Jeans > Other > W35; condition “A little damaged/dirty”; seller Nico, 99.6% across 265 feedback.

The user reports buying the shorts after the bot surfaced them. No confirmed purchase price is supplied. Later cards showed ¥4,900 and a day remaining, producing an immature-auction Stage A rejection. That latest observation does not erase the earlier delivery/purchase positive; neither the old auction end nor the user's purchase establishes current source availability.

### j1237781532 — repeated across both hunts

`VETEMENTS◆Tシャツ/M/コットン/BLK/プリント`, recorded ¥14,960 buyout. Exactly **one natural sighting**, September 6, 23:32:26 UTC, broad Vetements. The retained `quality-recall-v2` context says ineligible; no detail, later natural reinspection, or alert occurred through this baseline.

The v9 frozen offline replay has high brand/garment identity but weak directional evidence. That is not the old production decision. The observed failure to re-encounter the listing after upgrades cannot be repaired by claiming an offline pass; old inventory exposure/rank/availability remains unproven without a coherent current listing capture.

### h1238476063 — authentication-service observation

Retained title is a truncated **Vetements baseball cap**, last naturally seen September 3 at ¥5,000 current bid and 18 hours remaining. No cached detail or appraisal-service record exists. The user’s observation of a Buyee authentication guarantee is preserved as a report, not completed authentication.

Buyee's official guide describes an optional service that requires eligibility and selection; an available badge is not proof an order selected it, paid for it, or passed a completed check. Some products can still be ineligible. This object's selected/completed service status and applicable transaction protection remain unverified; a post-baseline public request returned 403. [Buyee authenticity-option guide](https://media.buyee.jp/pr/authenticity_option/en/auction/)

### g1223149511 — “may buy these”

No retained natural record or detail exists. Product identity, acquisition price, size, condition, and availability remain unknown; the isolated public lookup returned 403. Preserve purchase consideration without guessing from neighboring screenshots.

## Selection failure and bounded repair

GU × Undercover zip-pocket jeans `x1184359198` were naturally seen **22 times**, yet had no detail. Latest backfill scan **12163**, September 10, 19:16 UTC: 153 processable cards, 96 eligible, 94 capped, two details. The jeans were correctly brand/category-confirmed, actionable at **¥1,990 buyout**, eligible score **86.2**, and marked a cheap-value inspection candidate; they still lost selection.

Both actual winners had already been inspected: a ¥6,000 Undercover 21AW long-sleeve tee (score 97.6) and a ¥4,001 GU/Undercover open-collar shirt (95.4). The former was assessed weak again; the latter's refresh failed. The old reservation merely ensured a cheap item was selected. Once the winners were cheap, it did nothing for unseen inspection candidates.

The targeted change lets the **same single reserved slot** favor the highest-existing-ranked, already-eligible cheap garment/footwear with no recorded prior inspection over a selected previously attempted card. It changes no eligibility, heat, final rule, rate/global/path cap, source concurrency, or total fetch budget. Unknown history does not count as a proven previous attempt. Failed attempts are not labeled successful assessments; they can still win ordinary slots under existing retry timing.

An already-selected failed retry becomes protected from **both** reservation policies after the existing fully-evaluated freshness window (default 24 hours). This uses its existing last-attempt timestamp and the latest meaningful failed inspection event in bounded history, not its latest classification alone: a later capped decision can overwrite `detail_failed`. No new retry ledger or scan capacity is added. This prevents a one-slot reservation repeatedly stealing an overdue retry that already won ordinary ranking; it does **not** guarantee promotion of every lower-ranked retry. Recent failures may still yield a slot, and unknown/expired failure history is not invented.

A fixed-input replay retained 81 assessments from 135/153 card traces. Both old and new selectors request **two** details. The repaired selector replaces the previously attempted open-collar shirt with a not-yet-inspected ¥2,390 GU/Undercover tee (score 94.04). **It does not select the benchmark jeans next**; many other uninspected candidates rank above 86.2. Eighteen overwritten card traces preclude a complete historical counterfactual claim. This proves a bounded coverage change on a comparable retained cohort, not a useful new live alert or guaranteed time-to-inspection.

Focused coverage includes one-slot rotation, highest-ranked uninspected choice, ordinary failed-attempt eligibility, path/rate/global caps, disabled reservation/path, unknown history, severe veto, unchanged card eligibility/priority, and aged failed-retry protection across later capped classifications. Combined Stage A/pipeline/runtime/dedupe/storage run: **110 passed**; value-inspection tests alone: **32 passed**. Ruff and compile checks passed for the owned changes.

## Earlier 18-item longitudinal result

The prior set is now **10/18 seen, zero alerts**, versus 9/18 at the September 9 audit. The additional natural object is Number (N)ine fleece `w1219596150`, September 10, 12:27 UTC, broad Number (N)ine **page two/rank 91**, ¥6,600 current bid with one hour remaining. It qualified for identity recovery but was candidate-budgeted, not enriched.

Number (N)ine ¥9,900 cargos `q1240677210` finally received a natural selected attempt on September 10, 09:22 UTC, market-buyout, but **detail failed**. Their older September 4 cached detail survives. Later capped traces must not make this a successful reassessment. Failure does not enter the 24-hour fully-assessed dedupe class: normal six-hour revisit applies, and the listing later became processable but lost selection again.

The ¥5,000 cargo `u1225470712` still has only its September 9 page-two/rank-77 observation and no detail. The repeated Vetements tee remains unchanged as above. Seven supported-source items remain unobserved; the PayPay `z609539002` is a separate source-coverage limitation, not a Yahoo parser failure.

## Feedback-record binding and protections

Exact cached/alert matches support these retained controls:

| ID | Actual record | Feedback meaning |
| --- | --- | --- |
| k1236463450 | Orange Needles 20SS velour pants, ¥9,561, September 10 18:53:59 UTC alert | Unrated; title/image association contradiction, not a taste label |
| g1243492543 | Chrome-claimed fleur pendant/ball chain, ¥9,800 | User-reported replica, not independent authentication |
| r1242271213 | Silver-coated fleur bracelet/S925/audience-reference wording, ¥5,680 | User-visible replica feedback, not a global Chrome verdict |
| h1223997057 | Stüssy × mastermind Shadow Skull WT tee, ¥5,401 current | Friend purchase reported; current bid is not confirmed final purchase price |
| n1224167979 | Prada Linea Rossa sandals, size 24.0cm, ¥5,700 | Interest constrained by fit, not negative quality |
| n1053672229 | Patterned sheer shirt, ¥2,980, late ニードルズ title token | Strongest unique match to corrected genuine-Needles discussion; manufacturer clarification must not become a blanket label on all comparison-title shirts |

Puma × McQueen has two prior sent candidates (q1217152509, 26cm/¥6,500; s759344141, 27cm/¥6,880). The supplied text does not uniquely map the user's fit-positive feedback to one, so it is not silently assigned to both.

`config/retention_pins.yaml` initially protects the 43 unique real IDs across both human benchmarks plus five uniquely bound feedback/association records. Only c1239443570 is marked purchased from the explicit user report. No fake calibration IDs, forced alert overrides, or invented transaction prices are introduced.

## Human search method versus BuyEye

The user's added method—ending/current-price ceilings across two or three pages, then newly listed/buyout ceilings and clothing/shoe/accessory categories, sometimes mixed Flea Market inventory—is not equivalent to searching rare collection names. Existing discovery already supports most filter concepts, but its **actual depth and service are narrower than that manual session**.

Native ending slices use `aucminprice`/`aucmaxprice`, `sort=end&order=a`; closing adds `one_hour_to_finish=1`. Buyout slices use independent `aucmin_bidorbuy_price`/`aucmax_bidorbuy_price` and `buynow=1`; hot buyout discovery adds `new=1`. Slower persistent backfill omits `new=1` and sorts `bidorbuy` ascending. Categories include Men's Fashion `23176`, Men's Shoes `23200`, and explicit men's goods/bag/accessory IDs. These are exploration controls, not FMV or automatic alert conditions.

Hot ending and buyout strategies normally fetch **one page**; canonical broad anchors and backfill can fetch **two**, with a global ceiling of two. There is no demonstrated third-page coverage. Starting cooldowns are 75 minutes buyout, 45 ending, 360 backfill; Number (N)ine closing uses 15 minutes and ultra-cheap buyout 60. Broad Vetements uses 180 minutes and Balenciaga 240. A cooldown is eligibility, not a service guarantee across 446 strategies.

In the actual current-process window, **September 9 15:40:38–September 10 21:48:53 UTC**:

| Family | Broad attempts / successes / pages | Buyout scans | Ending scans | Backfill scans / pages |
| --- | --- | ---: | ---: | --- |
| Balenciaga | 3 / 3 / 4 | 17 | 13 | 4 / 5 |
| Vetements | 5 / 3 / 4 | 9 | 7 | 1 / 1 |
| Number (N)ine | 28 / 23 / 28 | 13 | 5 | 1 / 1 |

Number (N)ine also had 47 closing-hour searches. The purchased shorts' successful ending alert predates this process window; it remains a real positive for that sourcing method, not a new v9 alert claim.

These records establish searches, **not the unseen Balenciaga jeans' result rank or eligibility**, and do not show the older Vetements tee was reached again. The latter's one old sighting and sparse later backfill show an exposure limitation worth retaining, but no current capture establishes whether it was beyond the page limit, omitted by a filter, or unavailable at scan time. No additional searches, depth, or scheduling changes were made from an unknown rank/403.

All 116 scheduled market strategies currently request `item_type=auction`. The filter type accepts `fleamarket`, but no corresponding strategy is scheduled. This is distinct from the earlier PayPay-source limitation; mixed manual inventory must not be relabeled as naturally covered Yahoo inventory. Parser/source handling is evaluated separately, without adding a new acquisition lane here.

A low current bid is not substituted for an absent or over-ceiling buyout. Existing maturity/discovery checks and added focused tests preserve missing buyout as `None`, not zero; missing/¥40,000 buyout with a ¥1,000 early current bid cannot claim the ¥25,000 buyout-band or cheap-value path. Market/value inspection tests: **44 passed**.

## Post-change replay and natural validation

Post-change `ultra-refinement-v10` working-tree replay was captured at **September 10, 22:06:46 UTC** against the same immutable baseline. It produced **no changed final-route outcome among the 26**. The purchase-positive shorts now retain the shorts subtype separately from denim material (broad type trousers); their latest stale one-day auction observation still does not replay as actionable. Historical delivery and purchase evidence remain unchanged.

The fourteen uncaptured objects still have no replayable identity. Recomputing the known records did not manufacture detail evidence, simulate production selection, or resolve their source availability. The bounded selector improvement is demonstrated separately on an identical retained card cohort above; it is not credited with a new benchmark discovery or useful alert.

Natural post-deployment observations remain pending. Unit tests, development calibration, and offline replay do not establish better recommendation quality or maintenance readiness by themselves.
