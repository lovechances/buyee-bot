# Post-v7 status and storage audit

Audit date: September 9, 2026. Production baseline: `55eae149626188ce381d3399711fa9cc0b8b97fb`, logic `ultra-refinement-v7`.

Production evidence was read through SQLite `mode=ro` with `PRAGMA query_only=ON`, Railway logs/deployment metadata, and filesystem sizes. No production history, database records, cache, profile, authentication state, or volume configuration was changed by this audit.

## Latest status incident

The outage/recovery pair corresponded to sustained failed acquisition, rather than a short restart or a broken failure counter.

| UTC | Result | Consecutive failed scans |
| --- | --- | --- |
| 11:17:26 | Yohji Yamamoto failed | 1 |
| 11:18:18 | Needles returned 5 cards and enriched a candidate | reset to 0 |
| 11:21:37 | Gucci failed | 1 |
| 11:23:37 | vintage band t-shirt failed | 2 |
| 11:27:08 | Raf Simons failed | 3 |
| 11:30:07 | ラフシモンズ failed | 4 |
| 11:35:09 | Hysteric Glamour failed | 5 |
| 11:39:08 | ジュンヤワタナベ failed; outage announced | 6 |
| 11:45:16 | Helmut Lang failed | 7 |
| 11:51:51 | Miu Miu failed | 8 |
| 11:58:56 | Junya Watanabe failed | 9 |
| 12:03:42 | IF SIX WAS NINE returned 16 cards | recovery confirmation 1 |
| 12:04:20 | Celine returned a validated empty result; recovery announced | recovery confirmation 2 |

Each of the nine failed scans exhausted three `ParserAnomalyError` attempts. The preceding Yohji failure was correctly reset by Needles. There were no successful acquisitions during the nine-scan episode.

The persisted outage time was **11:39:08.909107 UTC**, after **17 minutes 31.8 seconds** from the first exhausted failure. The last-success-to-first-recovery interval was **45 minutes 24.4 seconds**. The public incident lasted **25 minutes 11.6 seconds**.

The same Railway deployment and process start time remained active. Incident logs showed no authentication exception. No crash between webhook delivery and state commit, duplicate incident, or deployment-induced false outage was demonstrated. The counter, duration requirement, persistence, and two-success recovery behaved as configured. A validated zero-result search is an operational success; it does not mean that parsing failed.

### What remains unknown

All nine consecutive failures were broad searches with no marketplace filters. Nearby successful searches used market filters. At the audit cutoff, the deployment had:

| Lane | Failed / scans |
| --- | --- |
| Broad | 23 / 154 |
| Medium | 2 / 61 |
| Market structure | 0 / 200 |
| Surgical | 0 / 20 |

This identifies a difference between some semantic and filtered responses. Historical logs did not retain HTTP status or response structure, so they cannot establish whether the failed response was a delayed shell, different markup, redirect, or source error. Treating any one of those explanations as proven would be unsupported.

### Targeted correction

A real health-reporting bug obscured the incident: `last_parser_anomaly` previously recorded the third consecutive valid empty search, rather than actual `ParserAnomalyError`. Actual parser failures now update it.

Structured status-transition logs now retain scope, query, error type, failure streak, start/duration, reset, and recovery outcome. The next exhausted parser failure records only bounded structural diagnostics: HTTP status, sanitized URL path template, document readiness, primary container/list/card/link counts, and total auction-link count. Page content, HTML, account information, session data, and URL query parameters are excluded. Diagnostic failure cannot mask the original safe acquisition failure.

Outage thresholds remain **6 failures and 15 minutes**, cooldown **6 hours**, recovery **2 successful scans**, and authentication confirmation **2 failures**. No threshold increase or source-recovery redesign was justified.

Regression coverage reproduces the observed nine-failure sequence, the intervening success reset, persisted cooldown/recovery state, valid empty results, actual parser health timestamps, sanitized fixture-derived structural diagnostics, and diagnostic-failure safety.

## Storage and runway

Three exact read-only snapshots span the deployment restart:

| UTC | Database bytes | Filesystem available bytes |
| --- | --- | --- |
| 14:12:37.964 | 203,083,776 | 87,207,936 |
| 14:21:43.239 | 203,423,744 | 84,185,088 |
| 15:10:52.422 | 206,499,840 | 80,900,096 |

The database grew **3,416,064 bytes over 58 minutes 14.5 seconds**, equivalent to **84.46 MB/day** if sustained. The last two snapshots imply **90.12 MB/day** over 49 minutes 9.2 seconds. At **80.90 MB available**, those measured rates imply approximately **21.5–23 hours of database-only headroom**, before additional transient demand. These are capacity scenarios, not precise exhaustion forecasts; do not rely on the earlier nine-minute 53.87 MB/day extrapolation for an overnight/multi-day claim.

During the longer exact sample, 57 scans recorded 1,075 newly seen IDs:

- Latest-trace payload grew 972,582 bytes.
- Bounded event-history payload grew 14,212 bytes.
- Detail observations grew 666,186 bytes across 33 new cached details.
- Logs grew 75,217 bytes and remain bounded by rotation; final usage was 11.03 MB.
- Browser logical usage ended at 141.40 MB (145.20 MB allocated). Browser cache fluctuates naturally; this must not be projected as a persistent daily growth rate.

Largest SQLite tables were seen listings **82.67 MB**, detail cache **49.69 MB**, listing records **44.98 MB**, and scan events **12.38 MB**. The DB had no freelist pages and uses DELETE journaling.

### Retention and cleanup

Existing pruning clears at most 2,000 never-inspected trace payloads every six hours after 14 days, preserving listing identity, dedupe, alert state, inspected histories, and decision columns. There were **zero eligible rows**: the oldest listing was only seven days old. A maintenance-run timestamp was not historically persisted, so invocation counts cannot be reconstructed; current eligibility is zero regardless.

The eventual pruning limit of 8,000 rows/day is also below recent first-seen volumes. It cannot be assumed to stop all growth. Detail-cache TTL controls reuse, not deletion: 1,298 expired observations held approximately 25.61 MB of potentially important diagnostic evidence. Deleting them solely on expiry would discard information needed by benchmarks.

Comp-cache cleanup would yield negligible space. Runtime logs are already capped at 30 MB. Browser HTTP and JavaScript caches total roughly 110 MB but vary automatically. Authentication/profile storage was not touched. Reducing transient cache would provide temporary relief and could increase network work; it would not solve sustained database growth.

**The existing 500 MB Railway volume needs additional capacity before relying on multi-day unattended operation.** No safe currently eligible cleanup found in this audit substitutes for that capacity. No destructive VACUUM or history purge was performed.

Sanitized exact snapshots and the reusable read-only collector are retained locally under `data/audits/post_v7/` (ignored runtime artifacts).

## Post-deployment persistence and runtime check

At 15:12 UTC, Railway deployment `03a95c1d-08d6-4c79-b438-92901236981b` was SUCCESS, running commit `e8a2240b54a5e46b3fa1d9a208cde96fc713fd0b` with one configured replica and one running instance. The Dockerfile/default start command and `/data` mount were unchanged.

The same runtime started at **14:34:17.186563 UTC** and had one scanner under `tini`, Xvfb on `:99`, and one headed Chromium using `/data/browser`. The database remained `/data/buyeeye.db`; portable `/data/browser/buyee_state.json` remained present at 19,202 bytes. Only file metadata was inspected. Authentication succeeded naturally at 15:11:15 UTC, with zero consecutive source failures. The cumulative retry counter remained 494; it is not a count of new container crashes. No source-failure, parser-shape, or status-transition event had occurred during the new deployment at this cutoff.

The redeploy preserved database, authentication, dedupe history, and prior closed status-incident state. These checks establish continuity, not sufficient storage capacity for unattended multi-day operation.

### Natural parser failure exercised the new diagnostics

The existing daemon naturally encountered one exhausted broad-search failure for **メゾンマルジェラ** near the end of the first observation hour. No diagnostic source scan was launched.

| Attempt / UTC | Reload HTTP status | Document state | Primary card nodes | Supported auction links inside cards / elsewhere |
| --- | --- | --- | --- | --- |
| 1 / 15:29:31.861 | 200 | complete | 100 | 0 / 28 |
| 2 / 15:30:01.993 | 202 | complete | 100 | 0 / 28 |
| 3 / 15:30:34.465 | 200 | complete | 100 | 0 / 28 |

All three diagnostics reported four primary containers and two primary lists, with sanitized path `/item/search/query/:query`. Therefore the failed response was **not literally missing card DOM nodes**: the selected nodes had no supported auction-item links, although 28 such links existed elsewhere on the page. No page text, HTML, session information, or query parameters were collected. These counts narrow the failure shape but do not establish whether the cause is incomplete rendering, different card/link markup, or another source response. The existing diagnostic reason `no_primary_cards_after_reload` means no usable parsed cards, not zero matching DOM nodes.

At **15:30:34.643 UTC**, the structured status transition recorded `ParserAnomalyError`, broad scope, failure streak **1**, `transient_degraded`, and **no notice emitted**. At **15:31:24.659 UTC**, the next Needles market-structure search returned a validated empty result and reset the episode with `healthy_no_incident`; no recovery notice was necessary. Subsequent natural searches returned 99 Raf Simons cards at 15:32:50, 100 Gaboratory cards at 15:33:40, and 200 Vintage Band Tee cards at 15:34:50. A same-context reload recovered the Raf scan without exhausting a source attempt.

A read-only SQLite check after these events confirmed the same runtime start, **zero consecutive failures**, `last_parser_anomaly = 2026-09-09 15:30:34.596396`, and cumulative retry count **497** (three attempts above the restart baseline of 494). The current failure-start field was cleared; the prior 11:39 outage and 12:04 recovery timestamps were unchanged. The actual-parser timestamp correction, bounded structural evidence, transient reset, and no-spam status behavior were thus exercised naturally in production. Thresholds, cooldown, browser/session handling, and recovery semantics remained unchanged.
