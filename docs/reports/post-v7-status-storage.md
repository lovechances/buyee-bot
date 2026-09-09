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

At **14:12:37 UTC**, the database was **203,083,776 bytes**. At **14:21:43 UTC**, it was **203,423,744 bytes**: growth of **339,968 bytes over 9 minutes 5 seconds**, across seven scans and 153 newly seen IDs.

This short sample extrapolates to **53.87 MB/day** of database growth. The available filesystem space at the second snapshot was **84.19 MB**, or **1.56 days** at that sample's rate. This is a capacity scenario, not a precise exhaustion forecast. The prior rounded “172 MB” database measurement suggests a broader approximate **70–100 MB/day** growth range; its ambiguous units prevent an exact comparison.

During the exact sample:

- Latest-trace payload grew 89,847 bytes, equivalent to approximately 14.24 MB/day if sustained.
- Bounded event-history payload grew 763 bytes, equivalent to approximately 0.12 MB/day.
- Detail observations grew 45,189 bytes.
- Scan-event payload grew 6,692 bytes.
- Logs grew 7,491 bytes and remain bounded by rotation.
- Browser files grew 2.62 MB, but other samples showed natural shrinkage. This fluctuation must not be projected as persistent daily growth.

Largest SQLite tables were seen listings **82.67 MB**, detail cache **49.69 MB**, listing records **44.98 MB**, and scan events **12.38 MB**. The DB had no freelist pages and uses DELETE journaling.

### Retention and cleanup

Existing pruning clears at most 2,000 never-inspected trace payloads every six hours after 14 days, preserving listing identity, dedupe, alert state, inspected histories, and decision columns. There were **zero eligible rows**: the oldest listing was only seven days old. A maintenance-run timestamp was not historically persisted, so invocation counts cannot be reconstructed; current eligibility is zero regardless.

The eventual pruning limit of 8,000 rows/day is also below recent first-seen volumes. It cannot be assumed to stop all growth. Detail-cache TTL controls reuse, not deletion: 1,298 expired observations held approximately 25.61 MB of potentially important diagnostic evidence. Deleting them solely on expiry would discard information needed by benchmarks.

Comp-cache cleanup would yield negligible space. Runtime logs are already capped at 30 MB. Browser HTTP and JavaScript caches total roughly 110 MB but vary automatically. Authentication/profile storage was not touched. Reducing transient cache would provide temporary relief and could increase network work; it would not solve sustained database growth.

**The existing 500 MB Railway volume needs additional capacity before relying on multi-day unattended operation.** No safe currently eligible cleanup found in this audit substitutes for that capacity. No destructive VACUUM or history purge was performed.

Sanitized exact snapshots and the reusable read-only collector are retained locally under `data/audits/post_v7/` (ignored runtime artifacts). Refresh them after deployment to report the latest headroom and a longer observed growth interval.
