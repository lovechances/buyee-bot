# Post-v10 pricing and comp-evidence correction

This report uses the frozen `data/audits/post_v11/baseline_verified.db`, not a
reconstruction from a newly fetched Buyee listing. The regression extract is
`tests/fixtures/post_v10_price_evidence.json`. Public fee and Grailed-page checks
below were performed on September 13, 2026; they are diagnostics, not production
acquisition or additional comp-source coverage.

## D&G/Dolce & Gabbana jeans: observed price versus estimates

The matching record is auction-source `u1222047972`. Its observed title describes
early-2000s Dolce & Gabbana leather-trimmed, distressed/repaired, flared denim in
men's size 46. The user called this a consideration-positive, **not a purchase**.

The retained detail observation at **2026-09-13 16:59:06.655363 UTC** contains the
product-bound buyout label `9,888 YEN (Tax 0 yen) (US$67.54)`. Domestic shipping is
the winner's responsibility, but the amount is unavailable. Successful-send
ledger event **97**, at **16:59:11.776479 UTC**, records an actionable ¥9,888
buyout, asymmetric-value thesis, and normal delivery.

| Quantity | Amount | Evidence meaning |
| --- | ---: | --- |
| Observed buyout | ¥9,888 | Listing price, immediately purchasable at observation |
| Buyee's displayed listing-only conversion | US$67.54 | Source display at observation, not a payment-provider quote |
| BuyEye configured conversion | Approximately $66.74 | ¥9,888 × static 0.00675; no live FX timestamp |
| Previous estimated landed total | Approximately $98.61 | Old planning formula, not a purchase or invoice |
| Corrected default planning scenario | Approximately $97.70 | Explicit component assumptions below; not an assured all-in ceiling |

The old formula was `9888 × 1.26 + 350 + 1500 + 300 = ¥14,608.88`.
The 26% consisted of an unsupported 11% "Buyee fee", 2% insurance, 8% duty
allowance, and 5% reserve. It also added a plan, international shipping, and
packaging, while accidentally omitting the configured ¥800 domestic shipping.

The corrected default scenario is:

| Component | JPY | Status |
| --- | ---: | --- |
| Listing | 9,888 | Observed buyout |
| Service fee | 500 | Configurable fixed estimate |
| Guarantee plan | 500 | Optional budgeting assumption, not an observed selection |
| Domestic shipping | 800 | Estimate; responsibility observed, amount unknown |
| International shipping | 1,500 | Existing planning assumption; weight/method/consolidation unknown |
| Protective packaging / extra percentage insurance | 0 | No selection observed; no duplicate default insurance charge |
| Duty allowance | 791.04 | Legacy 8% planning assumption, **not a verified tariff** |
| Reserve | 494.40 | 5% uncertainty reserve, not a platform charge |
| Total | 14,473.44 | Estimated scenario |

No Japanese tax is added to the observed price. The source's explicit Tax ¥0
is distinct from estimated destination duties. Optional authentication was not
ordered, and its fee was not invented. Shipping/consolidation/payment-provider
FX remain unknown. The changes do not remove all costs to make this piece look
cheaper, or treat the estimated total as what the user paid.

## Fee guidance and limits

Buyee's own general/auction guidance describes a fixed ¥500 service fee and
explicitly includes JDirectItems Auction, rather than supporting an 11%
commission. That page carries an old December 2023 comparison footnote, so it
is not treated as a guaranteed current individual checkout quote. Its separate
paid-plan and protective-service descriptions do not establish that this user
selected those services. [Buyee general/auction fee guidance](https://media.buyee.jp/pr/brandnew_buyee/en/)

Buyee's May 2026 guide also describes a fixed ¥500 fee and shipping dependent on
parcel size, weight, and method. It discusses 15.5% U.S. DDP charges: therefore
the retained 8% allowance is explicitly **not asserted to be correct for a U.S.
shipment**. Applying 15.5% instead, with every other assumption unchanged, would
produce approximately $102.70—not an observed invoice either. The primary fee
guide could not be retrieved in this diagnostic, and no checkout was attempted.
[Buyee's 2026 cost and shipping guide](https://blog.buyee.com/buyee-japan-proxy-shopping-guide-en/)

The Buyee Cart-only fee page was not used to infer auction fees. There is no
live FX provider or new shipping model. `EconomicsResult.cost_estimate` exposes
the listing-only conversion, named components, estimated total, and assumptions;
the known JPY price remains the acquisition fact.

## The three historical Grailed inputs

All three original IDs, titles, URLs, USD amounts, and historical cache records
are retained. The frozen cache retrieved them on **2026-09-11 22:48:53.198030**;
it held no sold-at date, receipt, accepted-offer amount, or shipping inclusion
record. Retrieval time is not a sale date or proof of an independently verified
transaction amount. Their local U.S.-market designation is source metadata, not
verification of a particular buyer's location.

| Listing | Preserved amount | September 13 public-page diagnostic | Current use |
| --- | ---: | --- | --- |
| [4466597](https://www.grailed.com/listings/4466597-number-n-ine-number-n-ine-ss01-time-migration-t-shirt) | $120 | Explicit sold-page price label remains visible; settlement/accepted-offer details are not established | Sold-page context, not certified realized value |
| [37239749](https://www.grailed.com/listings/37239749-number-n-ine-vintage-number-n-ine-ss01-time-migration-montreal-t-s) | $70 | Returned sold-page representation did not expose the amount | Historical visible-price observation, uncertainty preserved |
| [79545908](https://www.grailed.com/listings/79545908-number-n-ine-ss01-time-migration-montreal-bullet-tee) | $154 | Returned sold-page representation did not expose the amount | Historical visible-price observation, uncertainty preserved |

This does not prove the historical amounts were wrong. It corrects the stronger
unsupported claim that these three observations are independently verified
realized prices for the same exact design. None is erased or silently replaced
with today's asking price.

`Comp.is_sold` and `price_evidence` are separate. Amount evidence defaults to
unknown. These three seeds explicitly retain `visible_sold_price`, while the
existing Yahoo-ended parser marks its explicit winning-price observations as
realized **Japan** evidence. Legacy U.S. cache rows without amount verification
do not regain verified status through a cache hit. Evidence fields round-trip
inside the existing cache JSON; no database technology or schema replacement
was introduced.

## Piece matching, economics, and successful contrasts

Same brand/category/season no longer creates exact valuation by accumulating
three weak matches. Explicit object/subtype/collection contradictions reject
the comparison; missing design specificity, material/season conflicts, and
known condition conflicts remain context rather than creating precise FMV.
Source-ID duplication and explicitly known mirrored-stock identities do not
inflate the number of transactions. A stronger amount-verified observation of
the same transaction is not lost behind an earlier unknown observation.

An explicitly documented `verified_piece` assertion may establish an otherwise
unrecognized exact model, but cannot override maker/line/object/collection/
material/season contradictions. No such assertion was added to the three
production Grailed seeds. Existing theoretical transaction tests now say when
they stipulate verified amounts or exact product matches; those fixtures are
not presented as new production sales.

Unknown accepted offers and merely related evidence retain their provenance in
`context_matches`; they cannot produce a resale median, net margin, or multiple.
Actual sufficiently matched realized transactions still support economic
opportunities, including higher-priced genuine value and ordinary useful
objects with independently established economics. Missing exact comps still
leaves the separate directional-opportunity path available.

No active-U.S. source, sold provider, paid inference, browser workaround, or
production scraping expansion was added. Yahoo-ended observations remain Japan
market evidence, not U.S. FMV. General design/variant interpretation remains
limited by available metadata; these repairs do not claim human visual taste.

## Validation

**18 focused comp/pricing tests passed**, and Ruff passed for the touched files.
They cover the actual frozen D&G fields and old arithmetic, domestic shipping
counted once, optional insurance not duplicated, static versus invalid/missing
FX, unknown accepted offers, cache round trips, conflicting designs/materials/
conditions, duplicate/mirrored observations, explicit exact-match provenance,
and genuine positive-economics contrasts. The root checkpoint report contains
the final integrated test count, deployment state, and natural live validation.
