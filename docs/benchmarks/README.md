# Retained human benchmarks

`manual-hunt-2026-09-10.csv` preserves the user's exact 26 URLs and annotations from the September 10 post-v9 request. `source` and `marketplace_id` are separately derived stable identities; URL query parameters remain in `url_or_id`. Feedback tags are descriptive, not alert requirements or production overrides. All eight blank annotations remain blank and mean manual interest without an explicit quality rating.

The user reported a 90-minute hunt but supplied no exact start/end. The task's consistent production snapshot and its stated cutoff are an audit boundary, **not the inferred end of that hunt**. User prices, authentication-service observations, and purchases are user-reported unless independently established in retained records.

This set is distinct from the earlier 18-listing human hunt, the separate 18-listing production replay, and the 22-case calibration suite. `j1237781532` overlaps the earlier human hunt and is a longitudinal observation, not a new independent item.

The existing read-only batch diagnostic accepts the CSV directly:

```sh
python -m buyeeye benchmark-listings docs/benchmarks/manual-hunt-2026-09-10.csv
```

Production-history measurements for this checkpoint are instead run against the frozen snapshot through the existing Python diagnostic functions. A current offline replay is never relabeled as natural acquisition, inspection, or delivery.
