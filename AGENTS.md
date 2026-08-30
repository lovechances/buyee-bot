# BuyEye AGENTS.md

- BuyEye is a lean Python sourcing daemon for archive-fashion, not a generic scraper.
- Prefer small, explainable heuristics over broad fake-AI behavior.
- Never treat reseller asking prices as transaction comps.
- Brand alone does not establish value; evaluate the specific piece.
- Sold-market evidence should drive valuation where possible.
- Authenticity is a risk assessment, never certainty.
- Never bypass anti-bot protections or alter browser fingerprints.
- Do not store Buyee credentials in Git, YAML, logs, SQLite, or Python files.
- Do not automate purchasing or bids in the MVP.
- Every parser change requires fixture-backed tests.
- Every production change must run tests and ruff.
- Manual config and generated intelligence must remain separate.
- DiscoveryIntelligence must be evidence-driven and reversible.
