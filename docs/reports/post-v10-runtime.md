# Post-v10 sustained runtime, storage, and ERD coverage

## Boundary and evidence

This is a read-only audit of the verified, SQLite-consistent snapshot with SHA-256 `ad60636f8924b85798912e6cd8fb8b241c926043370eaae18d576fc0966e5788`. Analysis uses the materialized private copy at `/private/tmp/buyeeye-v11-test.CiBKkI/baseline_verified.db`; old macOS/iCloud-placeholder databases were not used to infer new measurements. Generated measurements are in `data/audits/post_v11/runtime_storage.json`.

Natural v10 window: **September 10, 2026 22:43:40.573 UTC → September 13 23:41:32.471821 UTC**, **72.964 hours**. Prior size comparisons use the September 10 closing sample in `data/audits/post_v10/live-validation.md`, a few minutes after that cutover, so growth rates are approximate. No directed source request, cleanup, profile change, or restart was performed for this audit.

## Source and detail reliability

| Frozen-window measure | Result |
| --- | ---: |
| Completed query attempts | 4,318 |
| Exhausted source failures | **24 / 4,318 (0.56%)** |
| Search pages / cards | 4,646 / 269,136 |
| Lifetime-first IDs | 27,955 |
| Stage B attempts / successful / failed | 5,841 / 4,821 / **1,020 (17.46%)** |
| Actual successful send events | 102; quality is assessed separately |
| Same-context parser reload attempts / successes | 1,256 / 1,099 |
| Recorded outer source retries | 81 |

All **24 exhausted source failures** were the slash-form queries `Y/Project` (17/17 attempts failed) and `Y/Project メンズ` (7/7 failed). Contrasts already present in production succeeded: `y project` **16/16**, 1,552 cards, 13 details; `ワイプロジェクト` **17/17**, 1,685 cards, 20 details. This does not support a global session/authentication failure or absence of Y/Project inventory.

The targeted correction submits the already-successful space-separated brand spelling for the slash-form strategies while retaining their strategy identity, filters, scheduling, and request budget. This is a bounded mitigation supported by the contrast, **not proof of the exact source/backend failure mechanism**. No additional discovery lane or parallel scanner is introduced. Natural post-deployment success remains separate evidence.

The misleadingly named persisted `browser_restart_count` increased from the prior closing **972 to 1,077**, **105** recorded failed source attempts. That matches **81 retries + 24 exhausted attempts**; it is not proof of 105 Chromium crashes. Same-page reloads are separately counted.

No gap between recorded successful scan completions exceeded ten minutes; the largest interval between any consecutive completed attempts was 126.1 seconds. The persisted process-start field remains September 10 22:43:40.560634; last successful acquisition and authentication were September 13 23:41:32 UTC, with zero consecutive failures. These observations establish continued acquisition, not perfect uptime or successful acquisition of every query.

No new persistent outage/auth incident is open. Last announced outage/recovery remain **September 10 06:01:50 / 06:49:44 UTC**, before this v10 window. That earlier real incident is not reclassified as a false alarm. Outage thresholds were not changed.

Detail failures remain materially higher than source failures. Among 251 retained latest failed-detail traces from this window, 240 record `ValueError` and 11 record `Error`. Those generic classes do not distinguish unavailable/ended pages, parser incompleteness, or identity-binding rejection. They are a retained-latest-state subset, **not all 1,020 attempts**. In particular, a preserved old successful cache entry does not turn a newer failed attempt into successful enrichment. Further targeted diagnosis needs an actual failure reason rather than relaxing product-bound extraction or asking for a login solely from an aggregate count.

## Actual persistent footprint

The existing volume is configured at **5,000 MB**, with measured usable filesystem capacity **4,685,873,152 bytes**. The supplied current filesystem sample has **3,121,696,768 bytes free** (~3.12 GB); the immediately adjacent frozen maintenance sample recorded ~3.117 GB. Normal concurrent SQLite/browser activity explains why nearby samples are not byte-identical; no precise cross-sample reconciliation is claimed.

| Object | Prior September 10 closing/reference | Current frozen/live sample |
| --- | ---: | ---: |
| SQLite physical size | 280,850,432 bytes | **382,038,016 bytes** |
| Available filesystem space | 3,259,752,448 bytes | **3,121,696,768 bytes** |
| Durable seen IDs | 109,295 closing sample | **137,245** |
| Card rows | 108,900 | **100,000** |
| Detail-cache rows | 3,158 | **4,866** |
| Raw scans / daily aggregates | 11,940 / 126 | **9,834 / 417** |
| Alert-state rows | 288 | **374** |
| Successful-send ledger | 0 | **102** |
| Explicit protections | 48 | **48** |
| Comp query-cache / sold-comp records | — | **528 / 3** |
| Browser allocated bytes | 854,036,480 at earlier 22:06 sample | **879,685,632** (859,068 KiB) |
| Runtime logs | 13,442,768 at earlier 22:06 sample | **19,881,984** (19,416 KiB) |
| Preserved remote audit directory | Approximately 273 MB | **272,580,608 allocated bytes** (266,192 KiB) |

The exact cutover-to-cutoff new-ID count is **27,955**. The closing-sample subtraction is 27,950 because that prior sample already contained five first-seen IDs from the first few minutes of v10. Neither difference is manufactured lifetime-first traffic. Preserving three sold-comp records does not validate their transaction amounts; evidence-quality corrections are covered in the main report.

### SQLite allocation versus reusable space

| Table or index | Allocated MB | Internal unused MB |
| --- | ---: | ---: |
| SeenListing | **174.80** | **69.26** |
| DetailCache | 82.97 | 3.81 |
| Listing | 75.43 | 12.92 |
| ScanEvent | 15.57 | 3.07 |
| Seen primary-key index | 11.46 | 1.31 |
| Seen last-seen index | 6.61 | 1.82 |
| Seen last-processed index | 6.60 | 1.81 |
| Listing source-ID index | 3.30 | 0.71 |

Across all structures, SQLite reports **95.44 MB internal unused space**, but only **five globally free pages / 20,480 bytes** at the snapshot. Internal leaf-page slack is not interchangeable with a guaranteed pool of pages available to every future table or append. Trace retention can reduce payload while physical allocation remains high. The DB has grown ~101.19 MB; a plateau is **not** established.

Journal mode remains DELETE; auto-vacuum remains disabled. No live journal deletion, full VACUUM, durability reduction, or profile/session cleanup is proposed to make the size look smaller.

## Retention under real load

The frozen maintenance state records **2,848 completed slices / 99,388 rows processed**, versus 13 / 1,490 in the previous short closing sample. The slightly earlier live sample was 2,847 slices/phase 3; the frozen state had completed `cold_ledger` and advanced to phase 4. Last completion: **September 13 23:41:08.943737 UTC**. Low-space is false and no storage warning has been attempted or sent.

Approximately **97,898 additional rows** were processed over this observation (~32,200/day), while new IDs arrived at ~9,195/day. Those counts are different operations, not a direct one-for-one deletion/arrival balance; the stronger evidence is that the intended working sets reached their limits:

- **50,000 latest traces**, exactly the configured working-set target; ~40.53 MB trace bytes plus ~1.78 MB compact event-history bytes.
- **100,000 cards**, at target.
- **4,866 details**, below the 5,000-row target and below the 100 MB observation-payload target.
- Raw scan history begins September 6 23:34 UTC. Only **eight scans** are just beyond the seven-day cutoff, pending a subsequent scan-rollup slice.
- Applying the actual protection/grace predicates at the frozen cutoff finds **zero age-eligible trace, detail, or card backlog**. Recent-active/protected exceptions are not silently counted as cleanup failures.
- Explicit expiry markers exist for **52,200 histories**, **412 detail entries**, and **39,854 card entries**. IDs remain present; these are expiry/omission facts, not newly unobserved identities.
- The union of seen alerted/protected items is **395 IDs**, with 395 cards and 376 cached details retained. Their detail text totals ~5.65 million characters, plus ~0.73 million trace/history characters; protected data is currently a small part of the total footprint.

Retention is demonstrably running and preserving its bounded working sets. It is not a hard cap on permanent IDs, every protected observation, or the whole Chromium profile. Browser allocated growth was about **25.65 MB** relative to the earlier September 10 browser sample; native eviction of some caches does not prove a bound on all History/site-storage files.

## Measured headroom scenarios

Current free space already includes the preserved old remote backup. Neither that backup nor any active profile was deleted. The exact owner-only backup cleanup command remains in [the prior storage report](post-v9-storage.md); do not bypass Railway's previous refusal through another deletion mechanism.

The observed closing-to-current net free-space decrease is ~138.06 MB over roughly three days, approximately **45.4 MB/day**, of which the SQLite file accounts for ~33.3 MB/day. This includes warm-up, retained working data, browser/log changes and allocation slack. It is a conservative continued-observed-rate planning case, **not a prediction that those components must grow linearly forever**.

| Horizon from current sample | Observed net rate continues | Adverse: twice observed net rate |
| --- | ---: | ---: |
| 30 days | ~**1.76 GB free** | ~**0.40 GB free** |
| 60 days | ~**0.40 GB free** | Capacity exhausted before horizon |
| 90 days | Capacity exhausted before horizon | Capacity exhausted before horizon |
| Existing 256 MB warning threshold reached | ~**63 days** | ~**32 days** |

For contrast, applying the earlier measured **584.91-byte compact-ledger fixture** to the now-observed 9,195 new IDs/day implies ~**5.38 MB/day** of permanent ID growth alone. This is a conditional compact-ledger floor, not a measured total-production rate or proof the other 40 MB/day has disappeared. Protected evidence, page layout, larger IDs/metadata and profile growth can increase it. New bounded release records and approximately kilobyte-sized offer context on tens of sends/day are negligible relative to current DB/profile growth, but remain durable and should be included in later measurements.

The system has adequate immediate headroom; storage does not require an emergency volume change. Continue lightweight `storage-summary` checks rather than claiming 90-day unattended storage is proven. Investigate if targets/backlogs grow persistently, maintenance progress stops, free-space decline materially accelerates, or the existing warning fires. At the current measured rate, retaining every ID forever still requires eventual owner maintenance.

## ERD was already active; narrow alias correction only

Before this pass, generated and persisted scheduling already contained **four ERD strategies**: accented canonical English, unaccented English, the configured Japanese alias, and one existing men's-fashion buyout exploration slice. The latter used category `23176`, actual buyout **¥300–40,000**, `buynow=1`, auction source, `sort=bidorbuy&order=a`; those are discovery controls, not value/authentication evidence.

Actual v10 execution:

| Query | Scans | Outcome |
| --- | ---: | --- |
| `Enfants Riches Déprimés` | 14 | Existing canonical broad service |
| `enfants riches deprimes` | 12 | Existing unaccented broad service |
| `エンファン リッシュ デプリメ` | 14 | Zero returned cards |
| `Enfants Riches Deprimes` market exploration | 2 | Zero returned cards |

Across these 42 scans: **206 cards, 79 processable passes, zero lifetime-first IDs, two Stage B candidates, two successful details, zero finals, zero alerts, zero exhausted failures**. Existing unrestricted broad search can expose clothing, shoes and accessories; no dedicated new category matrix is justified by this audit.

The only new coverage correction replaces the zero-yield Japanese search spelling with the source-observed **`アンファンリッシュデプリメ`**, retaining the old spelling for recognition. Boundary-aware `ERD` / `E.R.D.` recognition is added without another abbreviation query or duplicate family. Yahoo's own indexed listing titles supply these alias observations; they are spelling evidence, not authentication or valuation inputs. [Yahoo marketplace title examples](https://auctions.yahoo.co.jp/closedsearch/closedsearch/enfants%2Briches%2Bdeprimes/0)

The ERD strategy count remains **four** and overall discovery capacity is unchanged. No new-source adapter, price ceiling reduction, or forced ERD alert is introduced. Improved yield from the corrected spelling remains unproved until normal post-deployment service reaches it.

## Assessment

Operational continuity, persistent identity/delivery state, and active retention have survived meaningful runtime. Targeted mitigation of the consistently failing slash query is warranted; a browser redesign or relaxed outage policy is not. The **17.46% detail-failure rate**, uncertain long-term physical growth and unresolved sourcing-quality benchmark remain material limitations. Operational readiness and useful sourcing quality must be reported separately.
