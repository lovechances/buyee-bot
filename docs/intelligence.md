# BuyEye intelligence model

BuyEye evaluates two independent questions:

1. **Closet heat:** Is the specific piece culturally, historically, or aesthetically interesting?
2. **Opportunity:** Is the listing unusually attractive after acquisition costs and evidence-backed resale value?

Neither a target-brand match nor a low asking price answers both questions.

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

Authentication output is risk triage, never authentication. Observable counterfeit wording, implausible price, weak seller evidence, and missing photo coverage increase risk. Sparse listing data increases uncertainty.

## Promotion policy

Living intelligence follows `observed -> proposed -> testing -> promoted` (or `demoted`). Generated observations remain separate from manual production taxonomy, and small samples cannot silently change production behavior.
