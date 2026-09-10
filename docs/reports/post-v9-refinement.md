# Post-v9 integrity, recall and sustainable-storage checkpoint

Code-checkpoint report. Natural Railway deployment evidence is recorded separately in the local `data/audits/post_v10/live-validation.md` handoff after push; offline proofs here are not presented as live results.

## A. Starting state and evidence boundary

Verified local/remote main `d5acab827bbc1a2e0f35015ed0d5d657e88dab0e`; Railway deployment `57527a22-b085-4c45-b5bc-9a5d1a1fdd15`. At 2026-09-10 21:49:16 UTC there was one scanner, one headed Chromium main process, tini and Xvfb `:99`; `/data/browser` and its portable state existed. Last authenticated successful scan was 21:48:31 UTC, zero consecutive failures. No session contents were read or modified.

Railway reports the existing volume as Ready, 5,000 MB configured at `/data`. Runtime filesystem capacity was 4,685,873,152 bytes, with 3,491,495,936 available before the temporary audit backup. This is a verified resize, not an inference from the Hobby plan.

SQLite's backup API made a consistent baseline from 21:48:49–21:48:53 UTC, before any targeted marketplace diagnostics or retention. It contains scan IDs through 12,301, last completed at 21:48:31.791291. The private local copy is `data/audits/post_v10/baseline_verified.db`, 272,576,512 bytes; SHA256 `7217e2977a74b6a2ccb59cb6f3cc0fef49f4df688fb9e098e8dd12adc15766cf`. Remote quick-check and local full integrity-check returned `ok`. The actual 90-minute manual hunt's start/end were not supplied: the snapshot is an audit cutoff, not a fabricated hunt endpoint.

## B. Frozen production window

From v9's process start September 9 15:40:38 UTC through the snapshot (~30.13 hours):

| Measure | Count | Per elapsed hour, approximately |
| --- | ---: | ---: |
| Scans / failed scans | 1,568 / 142 | 52.0 / 4.7 |
| Cards | 81,337 | 2,699 |
| Processable passes / recent duplicates | 57,783 / 23,554 | 1,917 / 782 |
| Lifetime-first IDs | 15,825 | 525 |
| Stage B / enriched / failed | 1,913 / 1,620 / 293 | 63.5 / 53.8 / 9.7 |
| Finals / sent / deduped | 86 / 39 / 47 | 2.85 / 1.29 / 1.56 |

These are inventory and pipeline counters, not useful-alert precision or human purchase rates. `processable` is not lifetime novelty. Market structure contributed 730 scans, 19,931 cards, only 105 first-ever IDs, 333 B candidates and 5 sends; broad contributed 535 scans, 47,577 cards, 14,642 first IDs, 980 B and 23 sends. This shows exhausted/revisited market inventory, not evidence that all unseen manual finds were rejected by Stage A. Scheduling capacity and discovery configuration are unchanged.

## C. Human benchmark

The exact new 26 URLs and annotations are preserved in [the manifest](../benchmarks/manual-hunt-2026-09-10.csv). See [the per-listing audit](post-v9-human-benchmark.md). Baseline: 12/26 observed, 14 not observed, one cached detail/actual historical alert. Blank annotations are unranked interest, not guaranteed positives. The earlier 18 human finds, separate 18 production replay and 22 calibration cases remain different datasets.

`c1239443570` is the Number (N)ine distressed denim shorts the user reports purchasing; its September 8 ending/asymmetric alert is preserved. It is not the earlier GU purchase. `n1238725647` (highlighted Balenciaga jeans) and `g1223149511` have no baseline acquisition row; no Stage A failure is established. `j1237781532` had only one retained September 6 Vetements sighting, no inspection: later versions never reacquired it. `h1238476063` has a truncated Vetements cap card, not saved authentication-service proof.

After baseline, one plain unauthenticated request each for those last three key current pages returned HTTP 403. No evasion, extra authenticated scanner or session manipulation was attempted. Current availability/price and live diagnostic replay remain unverified for those pages.

## D. Needles field association: demonstrated cause

`k1236463450`, sent September 10 18:53:59 UTC, was the ¥9,561 orange 20SS Needles velour track pants. Its saved detail payload contained 96 whole-page images. The old parser kept 15 auction-image URLs, selecting the first: a black leather blazer from the recommendation-sized image group. Seven unrelated images preceded eight contiguous full-size trousers images. The selected URL exactly matches the persisted thumbnail. Two bounded public image downloads confirmed the black blazer and orange/gold trousers with purple stripes; hashes and URLs are in ignored `needles_image_association.json`.

This proves extraction/selection contamination, not a speculative Discord-cache fault. The old code also overstated detail-photo coverage. No historical final embed JSON or original card snapshot exists, and current downloaded image bytes cannot prove historical byte immutability.

The repair binds card images to the selected item anchor, supports lazy image attributes, validates detail source/ID/page identity and rejects cross-item cache/merge evidence. It excludes whole-document image arrays, retains only bound product-image evidence, strips known appended marketplace badges from normalized titles, and explicitly clears rejected persisted thumbnails. Unbound old caches remain stored but are refreshed only when their listing normally earns Stage B. Contradictory detail identity defers comps/final delivery instead of using the cheap card as a fallback. Gallery-wide photo counts remain unavailable without a verified primary-gallery selector; no selector is guessed.

## E. Semantics and risk

- Offered nouns beat fabric or pattern words: chain-pattern tie, denim hat/skirt, knit gloves; polo/shorts subtypes improve WHY without inventing design.
- Explicit known non-target makers and Japanese “for fans of” phrasing cannot become target manufacture. A late genuine Needles alias is not automatically a reference. Offered title outranks description keyword neighbors.
- Oakley SKULL collection naming alone no longer claims a depicted skull; explicit graphic/embroidery wording survives. WOMAN in an artwork name is not the plural women's audience marker.
- D&G is separated from mainline for relationship/comp matching. Unspecified vintage Celine/YSL no longer inherits Hedi designer context merely from the house name.
- Sensitive jewelry's item-level authentication remains unverified regardless of ratings, image count, price or service badge. It can receive useful review; those fields cannot certify it or force an economic route. Explicit fake, non-maker, scope and cross-item contradictions remain shared vetoes. All auth-sensitive cards disclose authenticity as unverified.

Buyee's [official authentication guide](https://media.buyee.jp/pr/authenticity_option/en/auction/) distinguishes eligibility, selecting a paid option and completed authentication; it also warns that some displayed options may not apply to the item. A badge alone proves neither enrollment nor a completed result. No order, service purchase or authentication claim is created here.

User corrections are retained as human evidence: the Stüssy/mastermind friend purchase, Prada fit-limited interest, confirmed genuine Needles discussion, and both Chrome user-reported replicas. Two Puma/McQueen shoe IDs fit the description, so a specific rating is not assigned to both. Unrated screenshots stay unrated; the Margiela/H&M negative does not become a blanket collaboration ban.

Initial natural v10 validation found a follow-on interaction: all 13 freshly bound details exposed one primary product image, while Chrome review expected two/four photos. Treating extracted primary coverage as a complete one-photo gallery would disable useful review. The narrow follow-on explicitly labels primary-only coverage as unknown gallery total; a high-identity, piece-specific Chrome candidate already qualifying on existing value/grail rules can remain reviewable with a source/ID-bound primary image. Known sparse-gallery, missing/unbound/mismatched image, generic and counterfeit cases remain suppressed. Risk stays high; even verified-comp economics cannot turn this exception into a buyout/silver route. No gallery count or authenticity is manufactured.

## F. Recall repair, without more browser slots

One existing cheap-item reservation previously could always go to an already-inspected candidate. `x1184359198`, ¥1,990 GU × Undercover zip jeans, had 22 sightings and no inspection despite cheap-value eligibility. In the retained partial Undercover cohort, both selected candidates had prior B attempts. The repair lets the same single slot replace an already-attempted candidate with the highest-ranked eligible never-attempted candidate, preserving global/path/market caps and existing scoring. Unknown history is not treated as proof of prior inspection.

The offline retained-card replay selects `o1229192852` at ¥2,390 instead of one revisit, still two detail slots. It does **not** force `x1184359198` next; higher-ranked uninspected candidates remain. This proves a coverage mechanism repair, not an extra useful alert or complete human-benchmark recall. Failed B remains retryable on existing freshness; retry candidates can use the other slots. Items never reacquired remain an unresolved discovery-depth limitation.

## G. Attention and September 10 incident

High-cost weak-value named archive pieces no longer gain normal notification attention solely by reaching the ending window. Their ending WHY preserves the piece thesis; actual value, buyout/ending semantics, meaningful-event dedupe and quiet payload flag 4096 remain separate. No claim is made about a user's phone settings or actual push delivery from a screenshot alone.

Known-to-known normalized identity rewrites (for example denim shorts changing from `denim` to `trousers`) do not by themselves trigger another alert. Newly resolved missing identity evidence, actual price events, meaningful timing and verified market evidence retain their own re-alert paths.

The reported 02:01–02:49 EDT incident matches retained UTC state/logs exactly. Last valid scan before the streak: 05:41:17 (empty Needles result); last nonempty 05:40:34. Yohji failed first at 05:42:10; its third attempt completed 05:44:19. Ten consecutive query failures followed across broad/medium families. The sixth, at 06:01:50, crossed the existing six-failure/15-minute rule and emitted one outage notice. Margiela resumed useful acquisition at 06:31:52 (61 cards), but intervening failures reset two-success recovery confirmation. Alyx 06:48:30 and Celine 06:49:44 confirmed recovery. Other query failures continued afterward.

Retained structural errors show HTTP 200/202, complete document, 100 primary card nodes but zero primary item links; 28 auction links elsewhere were deliberately not promoted as search results. ParserAnomalyError and bounded retries are demonstrated; anti-bot cause, exact hydration failure, memory pressure and disk/DB-lock causes are **not** established. The scanner/process continued. Retry count 930 is cumulative source/browser attempts, not 930 proven browser crashes. Recovery thresholds are unchanged; notices now describe acquisition degradation/resumed successful scans rather than claiming process death or universal source health.

**New live evidence, not a retrospective assumption:** September 10 22:21–22:38 diagnostics for Y/Project, Roen, Saint Laurent and Needles semantic failures show 99–100 primary cards, 198–200 Flea Market links, zero supported auction links and zero missing-href shells. Those sampled failures are unsupported-only primary inventory, not absent cards; the 28 auction links remain outside extraction boundaries. Semantic URLs omitted source qualification whereas all 116 market strategies already used native `item_type=auction`. A final bounded URL-builder correction applies that existing default whenever source is unspecified, preserving query identities, all other filters, cooldowns and page limits. This selects the auction **marketplace**, including its fixed-price/buyout offers; it does not restrict acquisition to bidding listings. It neither enables Flea Market acquisition nor declares unsupported-only responses healthy. Natural results of the corrected source request are reported separately; earlier unsampled failures retain their uncertainty.

## H. Sustainable storage

See [storage policy, restore rehearsal and projections](post-v9-storage.md). A real snapshot copy completed 396 bounded maintenance phases: every ID, alert state, verified comp and scheduler row survived. Maximum phase was 0.188 seconds locally; 12,900 traces, 87 expired details and 9,280 redundant card records expired, and 1,785 scans rolled into daily aggregates. New indexes initially increased physical DB size; 6.32 MB became reusable pages. No false shrink claim is made.

Default maintenance is 200 rows/one second, at most once per minute in the existing scanner. Optional traces/cards/details/scan history have explicit age and working-set targets; protected human evidence and alert state remain. Low space suppresses optional diagnostics and triggers a persistently rate-limited operational warning. No live WAL/browser-profile deletion or full VACUUM is used. The incomplete local transfer was removed after the local baseline hash/integrity and restore rehearsal were verified. Railway's file CLI refused agent deletion, so the 273 MB temporary remote audit backup remains; the guard was not bypassed. The private consistent local baseline survives.

The permanent ledger still grows: measured workload about 585 bytes/new ID including indexes, roughly 7.4 MB/day at observed arrivals. Including the retained audit backup, the conservative 90-day scenario is ~3.76 GB total; doubled sustained traffic/protected evidence reaches ~4.72 GB, beyond usable capacity. Human removal of the verified redundant remote backup would reclaim ~273 MB. Browser/site-storage and protected evidence remain explicit residual growth risks, not a promise of infinite maintenance-free storage.

## I. Validation

The complete suite passes **595 tests** (578 in the initial implementation, 12 follow-on coverage regressions, five native source-default cases); Ruff, compileall and diff checks pass. The existing calibration remains **22/22 aligned**. Fixture tests execute the actual extraction JavaScript in isolated headless Chromium with network blocked, without using authenticated local profiles. Independent review found and fixed coat/coating confusion, accessory-over-garment parsing, noun-less Stage B fallback, ending attention labels, low-space initialization ordering and the unknown-gallery/review interaction, then reviewed the shared native source-default fix. Natural deployment observations are supplied in the live handoff. Unit/offline evidence is not live quality improvement proof.

## J. Operations and rollback

Read-only operational commands through the existing service:

```sh
railway ssh -- python -m buyeeye health --hours 48
railway ssh -- python -m buyeeye storage-summary
railway ssh -- python -m buyeeye benchmark-listings docs/benchmarks/manual-hunt-2026-09-10.csv
railway ssh -- python -m buyeeye diagnose-listing n1238725647
```

Health evaluates recent successful acquisition, not DB existence alone. The 48–72-hour summary remains within the seven-day raw telemetry retention window. Benchmark/diagnose are offline evidence readers: they do not open a browser or send listing alerts.

Rollback is a normal Git revert of the code checkpoint followed by push to the existing service. Retain the newer additive `/data` database; do not restore the older audit snapshot just to undo code. No production model/vision API, paid service or second scanner was added. Browser inspection slots, source cadence and query universe are unchanged; small SQL retention work and optional reduced cache reuse are real costs, not zero-cost claims.

## K. Release assessment

The final handoff assigns release status only after deployment validation. Functional fixes do not prove sustained useful-alert quality or complete benchmark recall. A subsequent unchanged-build 48–72-hour window is needed; no FIRE quota, targeted query burst or forced alert applies.

## Human search method versus BuyEye

See the short comparison in [the existing benchmark report](post-v9-human-benchmark.md): current/buyout ceilings, ending/newly-listed sorting, category bands, market-lane auction-source filtering and older-inventory backfill already exist. Missing buyouts do not become zero. Hot one-page scans differ from the user's two-to-three-page manual ending pass, but the missing Balenciaga/Vetements historical ranks are unknown; no depth or price-setting change is justified from that uncertainty. Mixed-source fixtures keep later valid auction cards and exclude recommendations; new structural counts demonstrated unsupported-only semantic responses and justified the same native source qualification already used by market searches. No new price thresholds, queries, scheduler or source-load expansion was introduced by the addendum.
