# Final cost-and-usefulness rescue — October 9, 2026

## Decision boundary

This is a final bounded repair and seven-day check, not another Ultra roadmap.
**Keeping BuyEye is not yet justified by demonstrated cost and usefulness.**
The owner reports no purchases or exceptional finds in the latest two–three
weeks. That is meaningful feedback, not a claim that every delivered item was
bad or that profitable inventory does not exist. No new manual hunt is required.

**Provisional recommendation: retirement recommended on current evidence.**
The requested final seven-day trial remains running to test whether the targeted
repairs change that decision. Neither the cost target nor better owner usefulness
is established; this is not permission for another automatic refinement cycle.

Baseline local, GitHub and production revision: `6c988dc`.
Application correction: `097cb8b`; initial successful deployment
`b832e882-28e9-4d9b-88b1-efcf4a2cf252`, normal process start
**2026-10-09 15:53:03 UTC**. A documentation-only follow-up records the live
observations without changing application behavior.

## What the evidence actually covers

One bounded, read-transaction-consistent private extract at **15:35:58 UTC**
contains compact aggregates, recent delivery metadata, benchmark traces and
selected listing/detail fields—not a whole DB transfer or session dump.
Raw scans cover approximately October 2–9; daily rollups extend the funnel to
September 18. Expired hourly detail cannot be reconstructed from those totals.
Private measurements/replays are under ignored `data/audits/rescue/`.

| Recorded processing | Sep 18–Oct 9 15:35 | Complete Oct 2–8 |
| --- | ---: | ---: |
| Query attempts | 31,606 | 10,166 |
| Cards / lifetime-first IDs | 2,104,318 / 60,361 | 673,608 / 15,446 |
| Eligible / selected inspections | 591,677 / 50,457 | 189,960 / 16,022 |
| Successful enrichments / failures | 50,422 / 35 | 16,022 / 0 |
| Final passes / successful sends | 2,135 / 211 | 668 / 45 |

Cards, inspections, enrichment and finals are **passes**, including revisits and
cache hits, not distinct opportunities or necessarily new browser detail loads.
The 211 deliveries concern 183 source-qualified IDs: 162 initial events and 49
material changes (20 price drops, 16 ending-window entries, 13 newly available
buyouts). Routes: 118 buyouts, 49 ending, 41 grails, two review, one silver;
201 normal deliveries and ten quiet. Last-week post-detail rejection was 95.8%.
The seven retained raw days record six source retries, no exhausted query
failures and no parser reloads; the lifetime restart counter is not a crash count.

The quality sample includes 27 route-balanced deliveries, 32 reason-balanced
post-detail rejects, and 24 oldest never-inspected candidates across 12 families.
An initial recent-only capped sample was all one Saint Laurent query and was
not treated as representative. Twelve retained failure histories all eventually
enriched; their failures were older, not twelve new production failures.
No owner-rated precision percentage is inferred from these samples.

## Cost: measured cause, limited repair

Railway confirms one project/service/active replica, Hobby, and the existing
5,000 MB `/data` volume. The September 9–October 9 bill was **$27.74**, estimated
**$27.78** at observation: RAM $21.06, CPU $4.24, egress $2.07, storage $0.315,
backup $0.055. No second live scanner or other project explains the bill.
The current uninterrupted deployment ran from September 16; the entire billing
period also contains earlier releases, so it is not a pure current-build trial.

Official service rates are $10/GB-month RAM, $20/vCPU-month, $0.05/GB egress,
and $0.15/GB-month volume. The $5 Hobby minimum counts toward usage, not another
$5 on top. [Railway pricing](https://docs.railway.com/pricing)

Last-week hourly metrics averaged **2.614 GB and 0.246 vCPU**. Egress was
approximately 9.80 GB across the returned seven-day bins; volume averaged
2.158 GB. Extrapolation is about **$33.5/month total**, including approximately
$2.09 egress, $0.32 volume and the observed $0.055 backup component. The current
deployment's 23-day RAM mean was 2.37 GB; October 9 was near 3.15 GB, so a
roughly **$33–39/month warm run rate** is more honest than assuming the old bill
is a stable ceiling. These are consumption extrapolations, not quotes.

A six-minute passive sample found roughly **3.06–3.09 GB idle**, with active
browser work near **3.6–3.98 GB**. Python was about 160 MB PSS; Xvfb retained
about 395 MB PSS, including a 356 MB heap. Approximately 1.9 GB file cache and
0.65 GB kernel slab remained after browsers exited. Chromium subprocesses were
ordinary single-browser children, not concurrent scanner owners. Shared RSS
was not added up as if it were unique memory. Search/detail contexts already
close in `finally`; no speculative browser-reuse/headless rewrite was applied.

The owner approved **one** DB clean-cache advisory test after the safety reviewer
required confirmation. At 15:46:31, a read-only file descriptor and
`POSIX_FADV_DONTNEED` released 579.7 MB resident DB pages; container usage fell
3.111→2.519 GB. DB residency returned to 34.9 MB after 90 seconds and 150.8 MB
after 120 seconds; normal authenticated acquisition continued. No data, profile,
limits or durability settings changed. This is diagnostic evidence, **not a
recurring production eviction policy or demonstrated monthly saving**. Cache
regrowth and disk-I/O tradeoffs remain unresolved.

One definite waste was fixed: every card repeatedly constructed SQLAlchemy
engines and rechecked the whole registered schema. A production-version-aligned
100-card temp-DB comparison went from **200 engines/connections, 4,600 SQL
statements and 4,200 PRAGMAs** to one warmed connection and 200 SQL statements.
Elapsed time was 0.566→0.284 seconds; RSS growth 15.6→2.9 MB. Engines now have
bounded ownership, explicit disposal, replacement/late-schema handling and
fork-safe inheritance. Native maintenance connections close explicitly.
**This does not by itself explain or fix the multi-GB cache footprint.**

The measured ORM/browser versions are constrained during rebuild to avoid an
unrelated upgrade. No scan slowdown, source/inspection-cap cut, new model charge,
memory-limit reduction, cache/profile deletion or infrastructure change was used.

## Sourcing repairs and contrasts

- **Shared identity/object gates:** English/Japanese explicit non-maker
  statements cannot escape through review. Loose zipper pulls, retail paper
  bags, obvious cosmetics/books and automotive under-covers no longer borrow
  complete-garment value or consume identity-recovery inspection slots.
  Complete goods with included hardware/packaging and genuine shopper bags
  remain eligible. Celine Dion merchandise does not inherit CELINE manufacture.
- **Material/type integrity:** brass, silver color and plating are distinct
  from precious-silver claims. Wallet chains receive jewelry/Chrome scrutiny.
  Bootcut is a silhouette, not footwear; full decimal size tokens survive.
  Stamps, ratings and authentication options still do not establish authenticity.
- **Demonstrated recall repair:** the ¥1,990 GU collaboration `x1184359198`
  reached **171 sightings** with one failed September 17 attempt and no successful
  enrichment. Overdue failed useful-price inspections now share the existing
  maximum-one reserved slot with overdue never-attempted candidates. A new
  failure resets its cooldown; successful enrichment removes retry priority.
  This reallocates one existing slot, not extra requests or lower eligibility.
  Natural re-encounter and remaining eligibility are still required.
- **Presentation only:** remove Buyee's exact appended `Original Japanese Name:`
  duplication from the visible title while retaining raw evidence. Equivalent
  decimal formatting is not a sizing conflict; different measurements/units
  remain unresolved. Rendering cannot trigger new notifications.

Actual retained sends include explicit non-Chrome `1244820350` (review), riri
zipper hardware `r1239508437` (buyout), brass bracelet `g1245043642` (silver),
Celine Dion `r1185787929`, and paper bag `b1207102458`. Latest-state replay is
not their historical send-time reconstruction: among **108 retained inputs**,
finals changed **16→15**, removing the erroneous silver route while preserving
all seven buyouts, three ending candidates and five grails. Other corrections
clarify existing rejects and reject junk earlier. No useful-recall gain is
claimed from that replay alone.

## Benchmarks and remaining usefulness limitations

[All retained benchmark rows](final-rescue-benchmark-2026-10.csv) preserve
source, owner labels, dates, attempts, surviving details and uncertainty.
The original later manifest has 27 references/26 unique IDs, with 13 retained sightings;
the earlier 18 include one unsupported Flea Market row, with ten auction IDs
retained. Overlap is not an independent test set. Directed old diagnostics are
not relabeled natural discovery. The CSV consolidates the repeated annotation:
26 later rows, 18 earlier rows and five purchase-control rows, 49 total.
Recorded timestamps are UTC.

The Vetements hat has no post-v11 natural encounter; its five sightings end
September 12. Balenciaga `n1238725647` and `g1223149511` remain absent; Vetements
`j1237781532` still has only its September 6 sighting. These are unresolved
coverage/availability boundaries, not proof that increasing depth would fix them.
Most stratified old capped items cost ¥11,700–374,900 and do not establish failure
of the existing ≤¥10,000 useful-price reservation.

Owner-purchased GU **`t1233238196`, ¥200**, purchased NN shorts `c1239443570`,
and friend-purchased Stüssy/mastermind `h1223997057` remain distinct positives.
Their later incomplete replay states do not reproduce old delivery-time evidence
or invalidate purchases. Synthetic cheap-collaboration positive routes and
existing regression controls pass. D&G `u1222047972` remains a buyout positive:
¥9,888 / historical $67.54 item display versus about $98 estimated landed, not
an owner purchase. Shopping Prada is out of auction scope; wrong-size Puma ×
McQueen remains taste-positive without inventing an ID.

Eight distinct Oasis On Tour IDs at ¥6,800 were normally delivered September
19–25. Different sizes and incomplete color/condition evidence prevent claiming
they were duplicate physical stock. Existing substitute-offer quieting is narrow;
no broad band-tee or collaboration ban was added. Expensive archive attention,
generic-but-cheap taste judgment, maker-keyword pollution and incomplete visual
evidence remain real limitations. No broad threshold adjustment, new AI stage,
comp source, FMV invention or discovery rewrite was justified by this sample.

## Persistence and verification

Before rollout: DB approximately 580 MB; 222,406 durable IDs; 100,025 cards;
4,910 details; 10,148 raw scans; 4,544 daily rollups; 48 explicit protections;
362 send-ledger events. Maintenance had completed 26,471 slices, with no low-space
warning and about **2.89 GB filesystem space free**. The net free-space decline
since September 13 is about 228 MB over 26 days (~9 MB/day), much slower than the
earlier warm-up rate. This is adequate immediate headroom, not a mathematical
storage bound: permanent IDs/protected history and profile files still grow.

**Offline:** 838 tests passed in total: 836 in the complete run plus the two
offline Chromium fixture tests rerun successfully with required macOS sandbox
permission. No tests were weakened/skipped. Ruff and compile checks passed.
Coverage includes scope contrasts, genuine cheap collaborations, price maturity,
matched comps, retry cooldown/caps, DB replacement/rollback, retention concurrency,
authentication, session persistence and notification dedupe.

**Naturally observed:** rollout retained `/data/browser/buyee_state.json`, all
48 protections and the 362-event ledger; one scanner remains active. Nonempty
normal searches and enrichment resumed with authenticated source observations.
An empty opening Number Nine closing-hour search was not counted as proof of
successful authenticated acquisition. No benchmark fetch or listing alert was
forced. The old deployment was removed by normal Railway handover.

From **15:53:03–16:04:49 UTC**, ordinary production completed 13 queries across
11 families, 14 pages, 717 cards, 608 processable passes, 109 duplicates and three
lifetime-first IDs. There were 178 eligible passes and nine successful enrichment
passes, including four newly retrieved detail-cache observations; no failed
queries/details, finals or listing sends occurred. Last authenticated acquisition
was **16:04:04 UTC**, with zero consecutive failures. These small numbers prove
the deployed path works, not that recall or owner usefulness improved.

The separate **15:54:39–16:04:24 UTC** passive resource sample contains 40 points
over 585.6 seconds. Container RAM averaged **1.048 GB**, ranging **0.516–1.638 GB**;
idle mean was 0.812 GB and active mean 1.486 GB. CPU averaged **0.206 vCPU**.
Final idle RAM was 0.911 GB, including 0.767 GB file cache; scanner PSS was about
90 MB, Xvfb 57 MB. Browsers and the Node driver disappeared between work phases;
the scanner retained one DB file descriptor. The diagnostic process contributed
approximately 12 MB and is now stopped. No new retry failure happened to occur
during this short sample; failure behavior remains regression-tested rather
than claimed as live-observed here.

Extrapolating this cold-start mean gives **about $17.1/month total**: RAM $10.48,
CPU $4.12, plus previous measured egress/storage/backup approximately $2.47.
Post-change egress has not been established. A defensible planning bracket is
therefore roughly **$17–39/month**, spanning this short cold sample and the prior
warm run rate—not a statistical confidence interval or promised saving.
RAM was already warming upward, and a restart resets Xvfb/cache history.
The **$8–15 target is not demonstrated**, and no causal monthly cost reduction
can be separated from the restart yet.

At 16:05, `/data` had 2.88 GB free, 48 protections and all 362 send-ledger events.
Maintenance advanced to 26,477 completed slices. The new read-only trial command
reproduced the frozen complete-week totals exactly: 673,608 cards, 16,022
enrichment passes and 45 sends/42 IDs, combining 9,181 raw and 985 rolled-up scans.

One requested release summary was confirmed in **#updates**, channel
`1543683950474559559`, message **`1558148336035299350`**, durable event key
`final-rescue-2026-10`. It explicitly states that lower costs and better finds
are not proven. No listing notification was forced.

## Seven-day decision, no new manual benchmark

Leave this application build unchanged for **October 10–16 UTC**. On October 17:

```sh
railway ssh -- python -m buyeeye trial-summary --start-day 2026-10-10 --days 7
railway ssh -- python -m buyeeye health
railway ssh -- python -m buyeeye storage-summary
railway metrics --since 2026-10-10T00:00:00Z --until 2026-10-17T00:00:00Z --memory --cpu --network --volume --raw --json
railway usage --period current --json
```

The read-only report combines disjoint raw rows and transactional daily rollups;
normal seven-day raw retention cannot erase the summary inputs. Rollups last
90 days. Save the compact JSON/metrics with this report when reviewing; there is
no need to retain every payload or copy the DB. Snapshot health is current, not
historical uptime. Compare average consumed RAM/CPU, egress, storage/backup cost,
auth failures, intervention burden, route/event counts and structural mistakes.

The owner need only decide whether ordinary pings contain genuinely worthwhile
pieces, including wrong-size finds—not perform another hunt or labeling project.
Keep only if total warm cost is roughly **$8–15/month**, operation is stable and finds
justify attention. Acceptable cost/stability with too little evidence permits
only a limited trial. Persistently ~$25–30+ cost with mediocre output supports
**retirement**, not another automatic major refinement. No manual-sourcing parity
or marketplace-wide recall has been demonstrated. Railway also warns that the
existing config-as-code format is deprecated December 1; it was not migrated here.
The October 17 decision remains the owner's: keep, explicitly bounded extension,
or retire. Metrics do not substitute for whether the ordinary pings were worthwhile.

## Safe retirement, only after owner confirmation

Nothing was paused, deleted or canceled. If retiring:

1. Preserve the private GitHub repository and this report. In Railway service
   **Settings → Source**, turn off auto-deploys so a future push cannot restart it.
   Immediately before stopping, use `railway status --json`,
   `railway deployment list --limit 2 --json` and `railway volume list --json`
   to re-confirm project `60c23fef-20c9-440c-bff4-89c826d15a02`, service
   `569a9eb6-1936-48d7-aaa0-c5472bef4f3c`, production environment
   `08f29a2d-dc8d-47bb-a7aa-21b89d612727`, the current deployment and `/data`
   volume `b92c43f9-fcc7-418c-9c83-0c83e5be10d0`. Stop if they differ.
2. Stop compute for the exact existing service (do not delete project/volume):
   `railway down --project 60c23fef-20c9-440c-bff4-89c826d15a02 --service buyee-bot --environment production`.
3. Confirm no active deployment or writer. The `/data` volume remains attached.
   List it with
   `railway volume files --volume b92c43f9-fcc7-418c-9c83-0c83e5be10d0 list / --json`.
   In a new empty private archive directory (mode 700, preferably encrypted), use
   `railway volume files --volume b92c43f9-fcc7-418c-9c83-0c83e5be10d0 download /buyeeye.db <private-archive>/buyeeye.db`.
   **Before opening the downloaded DB**, download any existing
   `buyeeye.db-journal`, `buyeeye.db-wal` and `buyeeye.db-shm` in the same manner,
   keeping their names together in that directory. Current journal mode is
   `delete`, but shutdown can leave a hot journal; a future WAL mode could retain
   committed rows outside the main file. A bare-DB `quick_check` is not sufficient
   evidence that all recent rows were copied.
   Download portable auth with
   `railway volume files --volume b92c43f9-fcc7-418c-9c83-0c83e5be10d0 download /browser/buyee_state.json <private-archive>/buyee_state.json`;
   restrict that file to mode 600. Open the complete local SQLite set for recovery,
   verify `PRAGMA quick_check` and protected/send counts against the final
   production snapshot. Do not publish auth or replace either browser profile.
4. Keep the volume until restore evidence is verified and deletion separately
   authorized. Idle stored volume/backups remain billable; Hobby's $5 minimum
   remains while subscribed (not $5 plus storage when below included usage).
   Canceling subscription or deleting retained state is a separate owner decision;
   already accrued resource charges remain due.

`railway down` removes the deployment, not the service or volume; this command
was checked with installed CLI help, not executed. Railway documents persistent
volume behavior and resource billing in its [volume reference](https://docs.railway.com/volumes/reference)
and [pricing FAQ](https://docs.railway.com/pricing/faqs).
