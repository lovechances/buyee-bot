# BuyEye intelligence model

BuyEye evaluates two independent questions:

1. **Closet heat:** Is the specific piece culturally, historically, or aesthetically interesting?
2. **Opportunity:** Is the listing unusually attractive after acquisition costs and evidence-backed resale value?

Neither a target-brand match nor a low asking price answers both questions.

Stage A asks a narrower third question: **is this card plausibly worth a detail-page fetch?**
The 40-point closet-heat gate remains the archive path, but it is no longer the only inspection
path. Card-only evidence can also admit a deliberately bounded market buyout, mature auction,
silver/jewelry candidate, or unusually cheap unknown when brand and fashion-category context are
credible and there is no explicit counterfeit veto. Discovery price bands only rank inspection
context; they never become FMV or alert eligibility.

Market Stage A work is capped both per query and as a fraction of valid cards. Uncertain identity
exploration and silver have their own caps. Globally deduped cards are normally processed once;
an ineligible card may be reconsidered under a genuinely different family/strategy context, and a
capped card remains eligible to rotate into the bounded detail budget. A selected card stays
globally deduped until its listing state changes or freshness expires.

## Evidence layers

- `config/fashion_taxonomy.yaml` is manually curated production knowledge.
- `config/taste_corpus_observed.yaml` records sourced observations and provenance. Observations do not auto-promote rules.
- Reseller catalogs are positive taste corpora. Their asking prices are explicitly excluded from fair-market value.
- Only sold records from approved sources (`grailed_sold`, `yahoo_ended`, `ebay_sold`, `local_sold`) enter comp evaluation.

## Explainable evaluation

Title enrichment identifies brand aliases, designer context, era, collection, season, item type, category, material, color, size, collaboration language, and positive/negative signals. Every inferred value carries confidence, provenance, and its matched term.

Closet heat rewards piece-specific evidence such as a recognized collection, era, notable material, collaboration, and distinctive motif. Generic/basic, irrelevant, or counterfeit-associated language reduces the score. Brand alone receives little weight.

Comp matching requires brand agreement and then checks category, item type, collection, and material. Category mismatches fail closed. Robust medians and trimmed ranges limit outlier influence. Fewer or weaker matches lower confidence.

Opportunity uses landed cost, sold-comp median, estimated resale fees, net resale, margins, and multiple. Missing sold comps produce an unscored opportunity rather than invented profit.

Directional sourcing value remains separate from exact resale value. A very low actionable
acquisition price can support an asymmetric thesis only when identity, item/category, and
substantive piece evidence are already present; the low-price path has its own stricter bounded
score and never creates FMV. Favorable new/unworn condition adds a small sourcing bonus only after
that same piece-quality floor is met. Explicitly poor condition cannot use the cheap-price escape,
and Stage B women's-category or non-fashion-object evidence stops a menswear route. Plain basics
receive none of these benefits. A vintage band tee needs at least two independent age/production
observations; “single stitch” alone is not sufficient.

Final archive discovery can also use the combination of a named era/collection and specific design
evidence after successful detail enrichment. A bare year or season shorthand is context, not a
named archive identity. Silver/jewelry likewise needs directional value, credible sold economics,
or exceptional grail-level piece heat; ordinary expensive dealer inventory does not route from a
medium heat score alone.

Authentication output is risk triage, never authentication. Observable counterfeit wording, implausible price, weak seller evidence, and missing photo coverage increase risk. Sparse listing data increases uncertainty.

## Promotion policy

Living intelligence follows `observed -> proposed -> testing -> promoted` (or `demoted`). Generated observations remain separate from manual production taxonomy, and small samples cannot silently change production behavior.
