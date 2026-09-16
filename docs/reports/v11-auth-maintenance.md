# v11 authentication maintenance — September 15, 2026

Baseline: production `dd38c27`, deployment `0d92c305-4c51-4278-bc1c-b4cf9909c680`.
No sourcing/scoring, scheduling, query, inspection-budget or deployment redesign.

## Evidence before the patch

The uploaded portable cookie expired at **2026-09-14 23:04:24 UTC**. The first
recorded AuthExpiredError followed at **23:04:50 UTC**. The portable file still
existed at `/data/browser/buyee_state.json`; the Linux profile and database were
present. Profile cookie metadata showed a newer expiry, which does not prove
authentication (guest sessions also receive cookies). No cookie values were
printed, copied into reports, or changed during diagnosis.

Confirmed login reminders: Sep 14 23:20 UTC; Sep 15 01:44, 18:03, 20:52, 23:36.
Any successful scan previously reset the auth incident, including public cards
and empty results. Consequently the next two failures could repeat the same
reminder. `last_auth_success` was also updated without an authentication check.
The empty-page detector treated any Login word without exact-case Logout as
expiry, even alongside legitimate no-results text.

Each AuthExpiredError imposed an existing 15-minute pause. At the frozen
23:44 UTC check, 64 failed query events had accumulated since deployment;
the current acquisition gap was 24 minutes. No second production scanner or
browser was launched. A directed HTTP diagnostic returned 403; that does not
establish the headed browser's authentication state.

## Natural funnel (complete UTC hours; different inventory/time windows)

| Metric | v10 frozen 72.96h | v11 Sep 14 01–22 (22h) | Sep 15 00–22 (23h) |
|---|---:|---:|---:|
| Cards/hour | 3,689 | 3,766 | 1,539 |
| Inspections/hour | 80.05 | 79.68 | 38.39 |
| Enriched/hour | 66.07 | 65.36 | 31.65 |
| Finals/hour | 2.89 | 1.45 | 0.65 |
| Sends/hour | 1.40 | 0.50 | 0.17 |

Healthy v11: 82,860 cards → 53,164 processable passes → 19,367 eligible
→ 1,753 selected → 1,438 enriched → 32 finals → 11 sends. Processable passes
are not lifetime-distinct IDs. The impaired window had 35,392 cards, 883
inspections, 728 enriched, 15 finals and four sends.

The earlier reduction occurs after enrichment, not at acquisition/inspection:
1,406/1,438 enriched candidates were rejected (97.8%). Reasons included weak
directional value without US valuation (681), category mismatch (300), immature
auctions (180), identity mismatch (91), generic items (45), seller risk (31),
unverified high-ticket items (24), offered-object mismatch (18), diffusion value
(17), homage/secondary branding (14), and insufficient desirability (5).
These counts identify gates, not proof that every rejected item was correctly
judged. A bounded natural decision sample did not establish a specific useful
candidate regression. No thresholds were changed to raise alert volume.

Fifteen deliveries occurred before the snapshot (14 distinct IDs). The user's
McQ, Number (N)ine, Oakley, Roen, Hysteric Glamour and Needles examples are present.
The cohort does not establish improved sourcing quality. Remaining precision
questions include a Dior collar-stay/spare-button listing and a Chrome-referenced
zipper pull. They are not evidence of a new suppression regression, and no
speculative fashion patch was added to this health repair.

## Targeted repair

- Persistent auth incident remains open across public/empty/unknown successes.
- Recovery requires two consecutive explicitly authenticated, nonempty scans.
  Failures reset confirmation; genuine later incidents can notify again.
- Six-hour persistent notification cooldown also bounds failed delivery/flapping.
- Visible login/logout/password-page observations log booleans only, not account
  details. Health distinguishes public acquisition from authenticated recovery.
- Valid empty results plus a Login navigation link no longer mean an auth wall.
  Actual login-page/password-form redirects still fail safely.
- Expired portable cookies are never imported over newer profile cookies or sent
  by the HTTP path. No expiry extension, state-file rewrite or profile reset.
- Existing public search acquisition can continue, but never clears an unresolved
  auth incident or masquerades as verified authentication.

## Live validation and manual action

Maintenance `46d1643` deployed successfully as
`742b8fef-047f-44f5-ab30-0ace71b09265`. At 00:02:29 UTC Sep 16 the **normal**
headed scanner acquired a Needles result and observed Login=true, Logout=false,
login-page=false, password-form=false. This is logged-out public acquisition,
not a restored authenticated session. The new incident remained open rather
than being cleared by that successful public scan. Manual login is required.

The incident is therefore real expired authentication plus incorrect incident
resetting and overbroad empty-page classification. There is no evidence of lost
volume state, competing browsers, or a need to reset a profile. Cookie expiry
coincides with onset, and the live login controls independently confirm the
current logged-out state. The old cookie alone was not treated as proof.

Run from the local repository, with no local scanner/manual browser using this
profile. This intentionally opens a visible window; automated scans remain
off-screen/Xvfb. Keep the local backup private and outside Git:

```sh
cp -n data/browser/buyee_state.json data/browser/buyee_state.before-refresh-20260915.json
.venv/bin/python -m buyeeye setup-session
```

Log into Buyee normally. Verify the page is logged in before pressing Enter in
the terminal to export and close. Then upload **only** the refreshed portable
file to the existing volume:

```sh
railway volume files upload ./data/browser/buyee_state.json /browser/buyee_state.json
railway ssh -- python -m buyeeye health
```

The CLI upload path is volume-relative: the container sees
`/data/browser/buyee_state.json`. No profile/database deletion, new scanner,
full-profile copy or infrastructure change is needed. The next natural browser
launch imports the refreshed state; allow at least two nonempty authenticated
scans before expecting recovery. If the source still reports logged out, stop
and investigate rather than forcing a recovery notification.

Full suite including the manual-refresh visibility fix: **734 passed**, Ruff and compile checks
passed. Tests cover expired-cookie preservation, visible/hidden login controls,
public/empty results, incident persistence, interrupted recovery, distinct later
incidents, notification rate limits and credential-free diagnostics. Existing
v11 positive/negative fashion controls remain unchanged and passing.
