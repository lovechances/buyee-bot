# Post-v9 sustainable storage

September 10, 2026. This report separates baseline measurements, copy/fixture proofs, and production validation. Retention never changes fashion eligibility.

## Capacity and evidence preservation

Railway's existing `/data` volume is configured at **5 GB**. The measured filesystem capacity is **4,685,873,152 bytes**. At 22:06:58 UTC, available space was **3,261,845,504 bytes**, including the explicitly preserved 272,576,512-byte temporary baseline backup. A plan upgrade alone was not assumed to resize the volume.

That remote audit backup remains present: Railway's CLI refused agent-driven deletion, and no alternate deletion path was used. After confirming the retained local baseline/restore proof, the user can reclaim those 272.58 MB with the supported command:

```text
railway volume files --volume buyee-bot-volume delete /audit_snapshots/post_v10_baseline_20260910.db --yes
```

The local verified baseline remains preserved. The projection below includes the remote backup until this explicit human cleanup occurs.

The SQLite-consistent baseline is 272,576,512 bytes, through scan 12,301 at approximately 21:48:53 UTC. Its verified SHA-256 is `7217e2977a74b6a2ccb59cb6f3cc0fef49f4df688fb9e098e8dd12adc15766cf`. Integrity checking succeeded. Local retention tests used a separate SQLite backup; the baseline hash remained unchanged. Sensitive raw databases remain ignored, outside Git. Code rollback must not overwrite newer production state with this baseline.

At 22:06:58, production DB size was 272,904,192 bytes; the browser profile used 833,390,096 logical / 854,036,480 allocated bytes; logs used 13,442,768 bytes. Later bounded profile inventory attributed approximately 602 MB to HTTP cache, 196 MB to code cache, and 25 MB to History. No profile, cookies, local storage, live WAL/SHM, or browser cache files were deleted or altered by this work. Chromium-managed eviction is not a demonstrated hard bound on the entire profile.

## Actual growth and table semantics

| Baseline object | Allocated MB | Meaning |
| --- | ---: | --- |
| SeenListing | 116.68 | One ID ledger row, latest optional trace and bounded per-item inspection history |
| DetailCache | 68.76 | One overwritten observation per source/ID; TTL previously controlled reuse, not removal |
| Listing | 56.01 | Latest card fields per source/ID, duplicating some identity/price fields for replay |
| ScanEvent | 15.20 | Append-only per-query telemetry, including bounded decision samples |
| Seen primary-key index | 9.08 | Durable ID lookup |
| Listing unique index | 3.14 | Source/ID uniqueness |

This was primarily new-ID working-set growth and retained expired observations, not a new full detail version for every query. Repeated sightings already increment one row; unchanged meaningful-history fingerprints do not append another history event. Query scheduler totals are compact and retained. Verified sold evidence is tiny and remains intact.

The old cleanup ran only from trace writes, used a process-local six-hour timer, and cleared at most 2,000 never-inspected traces after 14 days. It had no durable progress or truthful expiry marker, and its maximum 8,000 rows/day lagged recent new-ID arrival. The replacement executes independently of trace activity.

## Executed policy

Defaults live under `storage` in `config/settings.example.yaml`:

| Working data | Policy |
| --- | --- |
| Cheap-card traces | 3 days |
| Candidate trace/history | 14 days |
| Optional trace working set | 50,000 rows; at most 3 KB latest trace + 12 KB per-item machine history |
| Card records | 14 days or 100,000-row target; retain recent-active 24-hour grace |
| Expired detail observations | 7 days or 5,000-row / 100 MB target; never remove a fresh cache entry |
| Inactive ledger metadata | Compact after 30 days without deleting the ID, first/last-seen facts, signature, inspection-attempt fact, or classification |
| Raw scan events | 7 days or 20,000-row target, with 24-hour grace; atomically summarize before deleting |
| Daily aggregates | 90 days, by date/family/layer/strategy, with funnel and route totals |
| Runtime logs | Existing rotation: five 5 MB backups plus current file |

Protected pins, explicit feedback/purchases, alerted items and pending material alerts are excluded from optional evidence expiry. These intentionally retained exceptions mean the row targets are not mathematical bounds on all data. Recent-active grace can also temporarily exceed a target. The finite 48-ID manifest protects the benchmark/feedback cohort; it never forces eligibility or an alert. Unrated manual interest stays unrated; reported purchase is not an invented transaction price or authentication certificate.

`feedback` now actually persists human labels and provenance. All compact human outcome transitions are retained; unlike machine traces, they are not truncated at 12. Re-seeding the benchmark cannot overwrite later explicit user feedback. `pin-listing` persists a reason without creating fake seen/inspection evidence. A compact successful-send ledger preserves future send events/material state and quiet/normal delivery. Historical AlertState and existing histories remain preserved; earlier sends that were never recorded cannot be reconstructed.

Expired/omitted evidence receives explicit timestamps/reasons. `diagnose-listing` keeps `seen: true` for a retained ID while reporting partial/expired history, rather than calling it never seen or pre-instrumentation. An old ID's return does not become lifetime-first. The durable ledger itself still grows.

## Operational safety

The single existing daemon runs one phase before query selection, including idle/failure loops: default **200 rows**, **one-second SQL budget**, no more frequently than **60 seconds**. Phase/progress counters persist in SQLite. Aggregate addition and raw-event deletion share one transaction; interruption rolls both back. Busy/budget-limited maintenance defers safely. No second browser, full scan, full VACUUM, durability reduction, or journal-mode change is involved. New lookup indexes are additive and require initial space; free-page reuse does not imply physical file shrinkage.

Under critical free space (128 MB or 3%, whichever is greater), optional cheap trace rewrites and per-scan decision samples are omitted. Essential identities, alerts, human evidence and candidate evidence remain enabled. An actionable storage notice uses the existing status webhook at most once per 24 hours, including persistent attempt throttling on delivery failure. Warning thresholds are 256 MB or 5%. Whole-profile/site-storage growth remains visible through filesystem headroom; this patch does not promise to bound every Chromium file.

Commands actually implemented:

```text
python -m buyeeye storage-summary
python -m buyeeye health --hours 48
python -m buyeeye diagnose-listing LISTING_ID_OR_URL
python -m buyeeye feedback LISTING_ID_OR_URL LABEL [--purchased]
python -m buyeeye pin-listing LISTING_ID_OR_URL --reason REASON
```

`storage-summary` is read-only. Health accepts 1–72 hours and reports acquisition age; database existence alone no longer produces `ready`. Portable-state presence is explicitly not called a live authentication test.

## Copy and workload proofs

The final frozen-database copy completed **396 small phases / 66 cycles in 6.39 seconds**, maximum phase **0.188 seconds**, mean **0.015 seconds**, with **zero deferred phases** on the local machine. This is not a cloud latency guarantee.

- All **109,275 IDs**, **288 alert states**, **3 verified comps**, **451 scheduler states**, incident state and **48 pins** survived.
- Expired **12,900 traces**, **87 details**, and **9,280 card records**; cards reached the 100,000 target. Recent activity preserved the other old detail observations.
- Rolled **1,785 scans into 150 daily aggregates**.
- Physical DB grew from **272.58 to 280.51 MB** because of new indexes/additive state. **1,543 pages (6.32 MB)** became globally reusable; additional space is available inside existing table pages. No false physical-shrink claim.
- Integrity checks and baseline hash verification passed.

Focused tests cover repeated unique-item growth and retention cycles, row/byte caps, restart/resume, interrupted aggregate transactions, protected records, actual material-alert dedupe, verified comps, truthful expired history, low-space optional omission and warning throttling, durable human feedback, manifest idempotence, read-only diagnostics, and SQLite backup/restore on a copy. Natural maintenance execution and longer sustained behavior require the deployment burn-in; unit/replay success alone does not prove future production stability.

## 30/60/90-day capacity scenarios

The baseline deployment observed 15,825 new IDs in approximately 30.14 hours, about **12,602 IDs/day**. A separate 10,000-ID compact-ledger fixture measured **584.91 bytes per additional ID, including indexes**, with 20% previously inspected and conservative 18-character IDs. That implies **7.37 MB/day** of intentional durable ID growth, not zero. New IDs alone add approximately **221 / 442 / 663 MB** in 30 / 60 / 90 days.

For a conservative planning envelope, apply 25% ledger overhead, reserve **1.6 GB** for active payloads/aggregates/logs/page slack, include the measured **0.854 GB** browser allocation, **0.064 GB** existing compact ledger and **0.273 GB retained remote audit backup**, and allow **1.5 MB/day** of intentionally protected alerts/human/verified evidence growth:

| Horizon | Baseline arrival scenario | Twice arrivals and protected-evidence growth |
| --- | ---: | ---: |
| 30 days | ~3.11 GB | ~3.43 GB |
| 60 days | ~3.43 GB | ~4.08 GB |
| 90 days | ~3.76 GB | ~4.72 GB |

These are assumption-driven envelopes, not predicted file sizes or a guarantee. Existing browser storage could grow; protected evidence, actual payload sizes, traffic, and maintenance throughput can differ. The doubled 90-day scenario exceeds the usable 4.686 GB filesystem while the remote audit backup is retained; human removal of that verified temporary backup lowers every estimate by approximately 0.273 GB but still leaves that stress case near the warning threshold. Short burn-in can prove the policy executes, not establish three months of stability. The right ongoing signal is working-set/payload growth plus durable bytes per new ID and available filesystem headroom—not repeated emergency resizing or an unsupported claim of mathematically bounded total storage.
