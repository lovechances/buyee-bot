# Post-v10 Ultra: sourcing correctness and bounded inspection repair

## Assessment and evidence boundary

This checkpoint implements targeted repairs; it does **not** establish that BuyEye beats the user's manual hunt. Operational continuity and sourcing quality are different conclusions. The original deployment was `eb12ea370446e8c88df8ff8e2aed80cf359a7c87`, decision family `ultra-refinement-v10`, Railway deployment `61812e70-4c46-44b5-8af7-81501e38d4fc`, one production scanner/profile with `/data` persistence. Infrastructure, source scope, source budgets, auction maturity, and same-listing material-event dedupe remain intact.

Frozen natural baseline: **2026-09-10 22:43:40.573 UTC → 2026-09-13 23:41:32.471821 UTC**, **72.9644 hours**. A single consistent SQLite backup was streamed from ephemeral `/tmp`, verified locally with SHA-256 `ad60636f8924b85798912e6cd8fb8b241c926043370eaae18d576fc0966e5788` and `integrity_check=ok`; no extra full backup was placed on `/data`. Raw private extracts remain ignored under `data/audits/post_v11`. The older 272,576,512-byte `/data/audit_snapshots/post_v10_baseline_20260910.db` was verified present and **not deleted**. No local or production authenticated profile was modified by diagnostics.

| Frozen natural measure | Count | Per hour |
| --- | ---: | ---: |
| Query attempts / pages | 4,318 / 4,646 | 59.2 / 63.7 |
| Cards | 269,136 | 3,688.6 |
| Processable listing passes | 173,589 | 2,379.1 |
| Lifetime-first IDs | 27,955 | 383.1 |
| Duplicate card passes | 95,547 (35.50%) | 1,309.5 |
| Eligible / budget-capped | 69,572 / 63,731 | — |
| Stage B / successful / failed | 5,841 / 4,821 / 1,020 | 80.05 / 66.07 / 13.98 |
| Post-detail rejects | 4,610 | 63.18 |
| Final candidates | 211 | 2.89 |
| Successful sends / distinct offers | 102 / 93 | 1.40 / 1.27 |

Processable passes are not distinct-ever listings. Sends were 44 buyouts, 31 ending, 23 grails, four review; 99 normal and three quiet. Events were 86 initial, 13 entering ending-soon, two price drops, and one newly available buyout. No blanket claim that repeated screenshots or similar stock are duplicate deliveries is supported. The post-v10 early Number Nine near-$1 bid was an `archive_watch`, with no actionable price in its send ledger; it was not a cheap purchase or proof that maturity regressed.

## Evidence → cause → repair → tradeoff

| Demonstrated evidence | Mechanism and correction | Positive/regression protection |
| --- | --- | --- |
| Manual-purchased Vetements hat reached page 1/rank 88, then capped; cheap GU × Undercover jeans reached 36 sightings without inspection | Extend the existing cheap first-inspection reservation to complete accessories; prioritize eligible uninspected candidates previously capped and first seen at least six hours earlier | Same existing reserved slot, total caps and retry protection; a fresh eligible card may receive less priority. No named-ID override or extra requests. Age is time since first seen, not proof of continuously waiting eligibility |
| GU/Undercover waffle title classified core and outranked the jeans | Recognize explicit Japanese GU collaboration wording without borrowing Undercover core identity | Actual ¥200 GU jeans, Puma/McQueen and Stüssy/mastermind remain positive controls; H&M's weak value is not a blanket collaboration ban |
| Hangers, postcard, nose pads, lens replacements and earring catches sent as complete fashion objects | Shared offered-object/completeness parsing before all routing; depictions and included accessories remain distinct from what is sold | Garment with hanger, complete eyewear with spares, genuine jewelry, and graphic tee depicting sunglasses remain valid |
| UNFINISHED/goa metadata and keyword piles supplied unsupported target-maker attribution | Parse a narrowly associated primary visible Brand row; keep observed maker, explicit collaborator and style-reference roles separate | Raw fields retained; duplicate/unbound labels not imported; genuine late-title Needles and compatible house/subline metadata preserved |
| Silver-colored footwear inherited precious-metal context; Chrome hair-tie subtype repair initially removed jewelry risk treatment | Scope material to the actual object and preserve sensitive hair-hardware auth handling | Silver jewelry remains silver; complete hair accessory is not a repair part, but seller/photos/name do not authenticate it |
| AIDS tees and ordinary motifs repeatedly qualified through title/context accumulation | Collapse synonymous design claims; common motif-only tees require very low attainable price or credible matched market support; one genuine construction/material can still suffice | Configurable narrow ¥5,000 weak-motif support threshold is not a brand/category ceiling or FMV. ¥1,500 graphic tee, Roen velour, D&G flared/leather details survive without U.S. comps |
| Near-equivalent AIDS tees / Soloist–OOFOS flip-flops repeatedly consume attention | Persist narrow known-model offer context; comparable secondary offer can send quietly | Distinct IDs remain independent. Price improvement >5%, size, color, condition, sleeve, season, material and design changes remain meaningful; special editions/reversible or unknown comparison data are not silently grouped. Clogs are not flip-flops |
| D&G ¥9,888 shown mainly as ~$99 landed | Show known listing price/conversion separately from explicit estimated cost components; correct unsupported fee/duplicate insurance assumptions and omitted domestic shipping | No costs removed to manufacture value; guessed shipping alone cannot supply the new negative-economics veto |
| Three visible Grailed sold-page amounts treated as realized, exact comps for other designs | Separate sold status from amount evidence, tighten actual piece matching, retain broad/unknown amounts as context | No invented FMV; verified exact transactions still support cheap or expensive arbitrage. Negative verified economics cannot be rescued merely by a grail label |
| 24/24 exhausted source failures were slash-form Y/Project; space/Japanese spelling worked | Use already-successful `y project` transport spelling for those same strategy identities | Filters/cadence/query count unchanged; no claim to have proven source backend internals |

These repairs address both recall and precision. They do not fix a listing never returned by acquisition, infer visual taste from arbitrary words, or guarantee that a newly inspected candidate will qualify.

## Human search method versus BuyEye

The existing system already combines distinct current-bid versus buyout ceilings, ending/newly-listed sorts, men's clothing/shoes/accessories, source qualification, and older-inventory backfill. It does not stop a page simply because an unsupported-source card appears. Existing contrasting tests preserve missing-buyout handling, low-bid/high-buyout separation and product-bound extraction; there is no source/filter rewrite here.

The method helped localize two actual losses **after** acquisition: the hat and GU jeans. The same-slot fairness repair has zero planned additional source-load capacity; expected inspection latency improves only when an eligible listing is naturally encountered again. It may displace a fresh cheap candidate, not consume a new detail slot.

The Balenciaga jeans `n1238725647` still have no retained encounter despite broad/buyout/ending/backfill family service. Repeated manual Vetements tee `j1237781532` was seen once before v10 but not re-exposed during it. Historical rank, filter-time price, availability and source responses are unavailable; no universal third-page, 16-hour threshold or lower ceiling is justified by that absence. The separate Prada Shopping purchase is outside auction acquisition scope, not a Stage A failure; no auction-stock equivalent is established.

See the [source-aware benchmark](post-v10-benchmark.md) for all 26 later-hunt items, the separate earlier 18 and purchase controls. Later-hunt lifetime exposure moved 12→13/26, with four naturally re-observed, one fresh successful detail, and **zero new v10 sends**. Earlier 18: ten historically seen, two re-observed, zero fresh successful detail and sends. The purchased Number Nine shorts and friend's Stüssy/mastermind delivery precede this window; the GU ¥200 purchase is neither the cheap early auction nor the later GU jeans.

## Price, risk and compact cards

D&G `u1222047972` is a user consideration-positive, not purchased: retained buyout **¥9,888 / Buyee display US$67.54**, static configured conversion ~$66.74, old estimated total ~$98.61, corrected default planning scenario ~$97.70. Optional services, shipping, consolidation, payment FX and destination charges remain assumptions. The 8% duty allowance is not a verified current U.S. tariff. The [pricing report](post-v10-pricing.md) documents components and current official-source limitations.

The renderer now leads with the observed JPY offer and source/planning listing-only USD; estimated all-in cost is labeled, not presented as paid. Early auctions retain current-bid/remaining-time labels without fake multiples. Actual size contradictions such as title 34 versus category XS are visible, with measurements/fit left unresolved. Redundant internal identity/relationship/confidence fields were shortened. Weak sold-page amounts remain context rather than a U.S. resale range.

Authentication remains uncertain. A service badge/option is availability, not selection or a completed authentication result; [Buyee's auction authenticity option](https://media.buyee.jp/pr/authenticity_option/en/auction/) does not establish an outcome for the reported hat. No service was ordered. The LGB-labeled party hat and ambiguous footwear remain examples where retained metadata cannot establish all claimed maker/quality facts; there is no fabricated counterfeit finding just to match a desired rejection.

## Model/visual decision

No paid runtime inference or experiment was activated; added inference spend is zero. Development inspection used two genuinely product-bound public images: the D&G jeans and the party-hat listing. The latter's primary image shows a performer wearing a sequined police-style hat, not a standalone offered-stock/tag photograph. That supports caution about image evidence, not identification of the person, maker or authenticity. The jeans image supports visible silhouette/distressing, not authenticated manufacture or resale value.

A final-only model veto would not repair never-acquired or repeatedly capped positives. The strongest current causes have deterministic fixes in retained evidence; a new billed stage is not necessary to ship those corrections. Residual visual desirability and ambiguous maker evidence remain real limitations. No priced model activation is proposed in this checkpoint; any future bounded pre-final evaluation requires separate approval, current official pricing, cached product-bound inputs, measured volume/spend caps, safe failure behavior and tests on cheap positives and pre-final rejects—not only existing finalists. No untouched human-labeled holdout or demonstrated model-quality gain is claimed.

## ERD, storage and reliability

ERD was already active: four strategies, 42 actual scans, 206 cards, 79 processable passes, two successful details, zero finals/sends. Replace only the zero-yield Japanese query spelling with marketplace-supported `アンファンリッシュデプリメ`; retain the old spelling and add boundary-aware ERD/E.R.D. recognition without abbreviation queries. Total strategy count/capacity remain unchanged.

Retention is working: 2,848 slices, 99,388 processed rows, cards/traces at 100,000/50,000 targets, no age-eligible card/detail/trace backlog. Physical DB grew to 382.04 MB; 95.44 MB internal slack is not equivalent to globally reusable pages. Free space ~3.12 GB. At the measured continued net consumption of ~45.4 MB/day, the existing warning is reached around 63 days; at twice that rate, around 32 days. These are planning scenarios, not steady-state forecasts. No emergency infrastructure or destructive cleanup is justified. [Full runtime/storage evidence](post-v10-runtime.md).

No new sustained outage/auth incident appeared in the frozen window. The +105 persisted source/browser-attempt counter equals 81 retries plus 24 exhausted attempts, not 105 demonstrated Chromium crashes. Detail failures remain **17.46%**; generic retained error classes cannot reconstruct every cause. Product-bound extraction is not relaxed to hide that limitation.

## Validation and natural deployment

Local validation and final frozen replay are recorded below once source is frozen. Known calibration and user-labeled regressions are not a pristine holdout. The 102 send events/93 offers and 452 fixed stratified latest-state records were frozen before tuning; the latter are deliberately sampled by decision class, not a prevalence estimate. Current replay uses latest retained state, not necessarily the actual at-send state. A rejection in replay is not automatically a human-negative label or proof of a prevented production alert.

Final replay at **2026-09-14 00:35:43 UTC**: all 93 raw detail observations reparsed; 102 event replays produce 68 rejects / 17 buyouts / nine ending / five grails / three review. Per distinct offer: 61 rejects / 16 buyouts / eight ending / five grails / three review. Nine structural corrections are supported (five wrong objects, three observed-maker contradictions, one explicit pump archetype). The other 52 rejects include 11 changed immature-auction states, 36 weak-value results and five other reasons: **not 52 proven bad human recommendations**. D&G ¥9,888, Roen velour ¥4,000 and Kapital mohair socks ¥2,500 survive; sensitive Chrome hair hardware stays high-risk review, not buyout. Decimal-size/variant and exact-economics collaboration tests correct contrasts without new market evidence. A weak but actionable buyout now receives its actual quality decision rather than being mislabeled an immature current bid.

Full suite: **725 passed plus one separately passed offline Chromium fixture = 726 tests**; Ruff and compile checks passed. The browser fixture requires a macOS sandbox exception for its temporary local browser, not network acquisition or the authenticated profile. Development calibration remains **22/22 aligned**, including five positive and two purchased cases surfaced; existing tests with false collection dates, speculative transaction proof or ordinary-motif bargain assumptions were explicitly corrected rather than silently deleted. This is regression alignment, not measured generalization. No paid inference, credentials, runtime DBs or session files enter Git.

Deployment, bounded natural observations and the single verified release summary are **pending at this report's initial commit**. No diagnostic listing fetch is credited as natural recall; no production history is reset to create a burst. The final live appendix will name exact commit/deployment/cutoffs, process/session state, natural funnel, release-message evidence and remaining limitations.

For the next unchanged-build window, reuse `health --hours 72`, `storage-summary`, and the dated `benchmark-listings` manifests. Compare natural first IDs, inspection outcomes, actual send ledger and human usefulness separately. Investigate sustained source failure, increasing failed-detail share, persistent eligible-but-never-inspected positives, stalled retention or accelerating free-space loss. Operational maintenance can be routine; superiority to a manual sourcing session remains **unproved**.
