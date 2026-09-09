# Post-v7 product scope and attention audit

This sub-audit examines `ultra-refinement-v7` from **2026-09-09 05:48:21 UTC to 14:09:42 UTC**, using production SQL, cached public listing observations, immutable scan decisions, and read-only diagnostic replay. It does not reconstruct missing historical sold comps or claim visual judgments from unseen images. The broader checkpoint report owns full-window funnel, runtime, and deployment results.

## Confirmed product-scope findings

| Listing | Alert time (UTC) | Observed offer and historical outcome | Current offline result |
|---|---|---|---|
| `j1230209084` | Sep 9 07:18:38 | Oakley Square Wire custom polarized replacement lenses; ¥1,800 micro buyout under v7 | `component_only`; reject `offer_scope_mismatch` |
| `d1231881378` | Sep 9 14:09:42 | Gucci velvet-look protective sunglasses case; ¥1 micro buyout under v7 | `protective_only`; reject `offer_scope_mismatch` |
| `g1205899781` | Sep 9 05:24:43 | Celine Teen Knight Poem wax-seal kit; v6 alert before the final v7 hotfix | Existing stationery exclusion still rejects |
| `w1193033133` | Sep 9 04:41:09 | Vehicle steering under-cover/trim panel mistaken for Undercover before v7 | Existing automotive exclusion still rejects |
| `j1242813580` | Sep 8 13:46:18 | Vehicle steering trim/garnish mistaken for Undercover before v7 | Existing automotive exclusion still rejects |

The lens listing's source category was `Sports & Recreation > Sports Sunglasses > Oakley > Metal Frame`. That category described the host product. The title explicitly offered compatible lenses, but v7 defaulted to a complete manufacturer product; `SILVER Color` then supplied silver-material evidence. The Gucci offer already had `offer_kind=container`, yet its price was still acquisition-attributable. Velvet-look wording and tiny price created its directional thesis.

The correction adds inspectable `product_completeness` evidence. Component-only and protective-only offers enter the existing non-fashion gate before detail budget or final valuation. It uses structured repair/protective categories, intrinsic component nouns, and replacement/compatibility relations. Complete objects bundled with spare lenses, laces, or cases remain eligible; complete objects needing repair remain products whose condition must be evaluated separately. A host name or category cannot supply the missing complete object. Bags, wallets, decorative wearable hardware, and garments depicting components remain in scope.

An independent adversarial probe found that named Geobasket **replacement laces** could borrow the shoe's iconic identity. English/Japanese shoelace cases now exercise the same component rule. Full sneakers supplied with replacement laces remain products. Feature fixtures contain no production listing IDs.

## Attention economics

| Listing | Historical v7 alert | Evidence | Current offline delivery |
|---|---|---|---|
| `1229400798` | Sep 9 06:31:42; Celine The Dancing Kid hoodie | ¥98,000; ~$848 landed; heat 62; one all-over-pattern concept; U.S. resale unverified | Grail card retained; quiet archive delivery |
| `v1224031776` | Sep 9 09:15:55; Undercover But Beautiful II reconstructed tee | ¥74,800 mature current bid; ~$651 landed; heat 67; one reconstruction concept; U.S. resale unverified | Grail card retained; quiet archive delivery |

Named collection plus one design concept established archive eligibility in v7. It did not establish exceptional price, replacement difficulty, or urgency. The attention layer preserves identity, route, card, history, and dedupe while suppressing push notifications for this narrow expensive, unverified archive class. Explicit iconic-piece evidence or multiple independent qualifying design concepts can retain ordinary delivery. Actionable value and ending routes keep ordinary delivery.

The two costly archive alerts are not proof that archive dominated the whole feed. Thirteen alert-state entries were present in this sub-window. Three Rebuild alerts involved distinct reconstructed/remade shirts and tees; the evidence did not justify family penalties, semantic dedupe, or a saturation system.

## Cheap-opportunity recall and limits

The examined 489 post-detail decisions included eight micro assessments for seven distinct items. Seven assessments reached a final route; the other was a Roen jacket before its ending-hour window, later alerted. Two distinct micro items were the product-scope defects above. This supports fixing completeness while preserving inexpensive garment eligibility.

The $65 micro threshold is **not a universal alert cap**. Existing exceptional-acquisition logic already let ¥9,460 Rebuild garments alert at ~$95 landed. Low-price footwear rejections inspected here were largely ordinary business shoes/loafers, weak specific-piece evidence, or auth/category uncertainty. Shipping alone was not demonstrated to be the footwear bottleneck.

| Recall probe | Current evidence and result |
|---|---|
| `t1242368776` Needles papillon patterned tee | ¥6,545/~$70, no obvious damage, seller 100%/256. Recognized pattern; directional 55. The $65 bonus discontinuity matters, but one uncertain probe does not justify global relaxation. It had an older Sep 3 alert; v7 rejected its latest evaluation. |
| `f1242355135` CDG Homme Plus hem-border tee | ¥5,280/~$59, mature auction. Japanese `ヘムボーダー` not recognized as design; inspected and rejected. Recognition probe, not a proven missed steal. |
| `o1238617434` Prada Banana FlamesTシャツ | ¥10,000 current/~$100. Mixed Latin/Japanese adjacency incorrectly produced `shirt`; structured T-Shirts category now yields `t_shirt`. It remains unalerted without enough piece/value evidence. |
| `f1235321357` Number (N)ine tee | ¥600/~$20, credible seller, no observed design evidence. Inspected and still suppressed; no graphic or resale value is invented. |
| `x1235758803` Margiela AIDS tee | ¥7,800/~$81, score 58; remains rejected. Older Sep 4 alert is separate from current v7 rejection. |
| `h1224795530` Undercover oversized purple long sleeve | ¥6,000/~$66, no independent design evidence beyond cut/color; remains rejected. |
| `t1238639508` N(N) ombre western shirt | ¥8,800/~$89, Japanese design vocabulary weakly recognized, but explicit damaged/dirty condition; not treated as a required positive. |

Low-cost positive controls remain intact: the Roen skull-embroidered track jacket `f1243452852` (~$61 landed) keeps ending delivery, and Rebuild `x1092313577` (~$64) and `r1238210408` (~$95) keep buyout delivery. Oakley golf trousers `c1216425893` (~$78) still qualify; their subjective usefulness remains a calibration risk rather than grounds to ban Oakley or skull graphics.

## Reproduction and verification

The ignored, sanitized local evidence cohort contains 18 public listing/cache snapshots; it is **different from the user's original 18-item manual benchmark**. Reproduce the offline scope/attention comparison from the repository root:

```bash
.venv/bin/python data/audits/post_v7/replay_scope_attention.py
```

The script reads only that JSON and local configuration, prints JSON, and opens no database/browser or network client. It performs no comp calls, sends no alerts, and writes no state. With the implementation at audit time, results were 12 rejections, two quiet grails, and four ordinary-delivery opportunities (three buyouts, one ending). All exact U.S. valuations remained unavailable.

Owned feature coverage passed 71 cases after independent component adversarial probes. Existing scope/semantic/Stage A/calibration tests also passed before integration. Full-suite, deployment, and natural-runtime proof belong to the checkpoint's final report; these offline results are not live alert claims.
