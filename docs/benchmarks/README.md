# Retained human benchmarks

`manual-hunt-2026-09-10.csv` preserves the user's exact 26 URLs and annotations from the September 10 post-v9 request. `source` and `marketplace_id` are separately derived stable identities; URL query parameters remain in `url_or_id`. Feedback tags are descriptive, not alert requirements or production overrides. All eight blank annotations remain blank and mean manual interest without an explicit quality rating.

The user reported a 90-minute hunt but supplied no exact start/end. The task's consistent production snapshot and its stated cutoff are an audit boundary, **not the inferred end of that hunt**. User prices, authentication-service observations, and purchases are user-reported unless independently established in retained records.

This set is distinct from the earlier 18-listing human hunt, the separate 18-listing production replay, and the 22-case calibration suite. `j1237781532` overlaps the earlier human hunt and is a longitudinal observation, not a new independent item.

The existing read-only batch diagnostic accepts the CSV directly:

```sh
python -m buyeeye benchmark-listings docs/benchmarks/manual-hunt-2026-09-10.csv
```

Production-history measurements for this checkpoint are instead run against the frozen snapshot through the existing Python diagnostic functions. A current offline replay is never relabeled as natural acquisition, inspection, or delivery.

## September 13 supplied evidence

`manual-hunt-2026-09-13.csv` retains the latest prompt's **27 raw references / 26 unique source-qualified listings**. Rows 10 and 27 reference the same `e1206936947`; the latter adds explicit purchase consideration. Both annotations remain available, and the latest explicit annotation governs interpretation. The dated manifest is a supplied-evidence revision, not a claim that another hunt occurred on September 13. The September 10 manifest remains unchanged.

`prior-hunt-18-2026-09-13.csv` keeps the earlier **18-item** hunt separate, including unsupported-source PayPay `z609539002`. Its five explicit price/purchase annotations are not generalized to other rows. `purchase-controls-2026-09-13.csv` is a separate overlapping set of user-reported outcomes, not another independent recall denominator. Shopping Prada keeps full ID `ssol-shopping_2348621003856` under `buyee_shopping`; the legacy Yahoo hat URL normalizes to the same supported auction identity as its JDirectItems alias. Recognizing a URL diagnostically does not enable that acquisition source.

The diagnostic's `total`, `seen`, and `outcomes` remain reference counts for compatibility; **use `unique_total`, `unique_seen`, and `unique_outcomes` for listing denominators**. Each unique item includes reference indices and annotation history. These outputs do not change production ratings, pin ownership, eligibility, or purchased status.

The user reports conflicting fit information for `n1238725647`: tag size 34, flat waist 49 cm, marketplace category XS. These are unresolved conflicting observations, not a verified intended fit or model-code interpretation. An authentication option on `h1238476063` is reported eligibility, not evidence that an order selected or passed a paid service.
