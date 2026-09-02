# BuyEye

BuyEye is a lean archive-fashion sourcing intelligence daemon focused on Buyee and Yahoo! JAPAN Auctions. It is built to find interesting menswear and archive pieces, estimate value conservatively, suppress generic brand-only noise, and send only high-quality alerts to Discord.

Open-auction bids are treated as provisional until the configurable maturity window (12 hours by default). Fixed-price buyouts remain actionable immediately. Exceptional pieces discovered earlier may surface as `ARCHIVE WATCH`, but their current bid is excluded from landed-cost, margin, and resale-multiple claims until the auction matures. The separate 60-minute threshold controls ending-soon routing.

Discovery rotates through four complementary lanes: broad brand inventory, medium era/category
context, surgical collection or motif searches, and native marketplace slices. The latter uses
Buyee's category, current/buyout price, buy-it-now, newly-posted, and ending-soon controls configured
in `config/market_structure.yaml`. Those price bands only decide where BuyEye looks; they are never
used as fair-market value or injected into comp/economics decisions. Strategy identities and yield
are persisted while listing dedupe remains global across every lane.

## Quick start

1. Install Python 3.12+
2. Create a venv:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
3. Install the package:
   ```bash
   python -m pip install --upgrade pip
   python -m pip install -e .
   ```
4. Copy the example environment and config:
   ```bash
   cp .env.example .env
   cp config/settings.example.yaml config/settings.yaml
   ```
5. Configure your Discord webhooks in `.env`.
6. Run the setup-session flow:
   ```bash
   python -m buyeeye setup-session
   ```
7. Run a single scan:
   ```bash
   python -m buyeeye scan-once
   ```
8. Run the app:
   ```bash
   python -m buyeeye run
   ```

## Environment variables

Example values live in `.env.example`.

Required for alerting:
- `DISCORD_WEBHOOK_BUYOUTS`
- `DISCORD_WEBHOOK_ENDING`
- `DISCORD_WEBHOOK_GRAILS`
- `DISCORD_WEBHOOK_SILVER`
- `DISCORD_WEBHOOK_REVIEW`

Optional:
- `BUYEE_USERNAME` if you are using a manual prompt flow later
- `BUYEE_PASSWORD` if you are using a manual prompt flow later

## Local setup

macOS:
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m buyeeye status
```

Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m buyeeye status
```

## Running 24/7

On this Mac, use the included launchd job. It runs the canonical virtual environment, restarts
after failure, preserves the authenticated browser profile, and uses BuyEye's process lock to
prevent duplicate scanners. Runtime secrets are loaded from the repository-local `.env` without
being printed.

```bash
scripts/buyeeye-service start
scripts/buyeeye-service stop
scripts/buyeeye-service restart
scripts/buyeeye-service status
scripts/buyeeye-service logs
```

Logs rotate at 5 MB with five retained files under `data/logs/`. If health reports that manual login
is required, stop the service, run `.venv/bin/buyeeye setup-session`, then start it again. The
optional `DISCORD_WEBHOOK_STATUS` receives only startup, authentication-expiration,
sustained-outage, recovery, and fatal-shutdown notices.

For Railway-first Docker deployment, persistent `/data` layout, portable session bootstrap, normal
operations, and the Ubuntu VPS fallback, see [docs/deployment.md](docs/deployment.md).

## Intelligence review

```bash
python -m buyeeye intelligence status
python -m buyeeye intelligence proposals
python -m buyeeye intelligence approve 1
python -m buyeeye intelligence reject 1
python -m buyeeye intelligence report
```

## Feedback

```bash
python -m buyeeye feedback 123 fire
python -m buyeeye feedback 123 pass
python -m buyeeye feedback 123 generic
python -m buyeeye feedback 123 fake
```

## Test and lint

```bash
python -m pytest
python -m ruff check .
```

## Intended operating model

The app is intentionally conservative. It favors real value as judged by specific-piece characteristics, comp coverage, and risk. It does not auto-purchase or auto-bid.

## Live verification status

- Public fixture parsing is verified in tests.
- Live marketplace access may be blocked by anti-bot protection or login requirements.
- Any live source that cannot be verified is reported as `NOT VERIFIED` rather than treated as reliable.
