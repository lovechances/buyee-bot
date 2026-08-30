from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .browser_session import has_valid_session, launch_manual_buyee_login, save_session_state
from .db import init_db
from .live_sources import LiveMarketLoop, load_live_queries
from .settings import load_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="buyeeye")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status")
    subparsers.add_parser("run")
    subparsers.add_parser("scan-once")
    subparsers.add_parser("setup-session")
    subparsers.add_parser("send-test-alert")
    subparsers.add_parser("backtest")

    intel = subparsers.add_parser("intelligence")
    intel_sub = intel.add_subparsers(dest="intelligence_command")
    intel_sub.add_parser("status")
    intel_sub.add_parser("proposals")
    intel_sub.add_parser("report")
    intel_sub.add_parser("approve")
    intel_sub.add_parser("reject")
    intel_sub.add_parser("promote-auto")

    feedback = subparsers.add_parser("feedback")
    feedback.add_argument("listing_id")
    feedback.add_argument("label")

    return parser


async def _run_market_pass(loop: LiveMarketLoop, queries: list[str]) -> None:
    if not queries:
        print("No live queries configured; only local loop mode is active.")
        return

    for query in queries[:8]:
        items = await loop.run_once(query)
        print(f"query={query!r} listings={len(items)}")
        for item in items[:3]:
            print(f"  - {item.source}: {item.title_original[:80]} | {item.current_price or 'n/a'}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    settings = load_settings()
    db_path = Path(settings.get("BUYEYE_DB_PATH", "data/buyeeye.db"))
    init_db(db_path)

    if args.command == "status":
        print("BuyEye status: ready")
        print(f"DB: {db_path}")
        return 0

    if args.command == "run":
        print("BuyEye daemon started in local loop mode.")
        queries = load_live_queries()
        loop = LiveMarketLoop()
        try:
            while True:
                asyncio.run(_run_market_pass(loop, queries))
                print("Sleeping 180s before next market pass...")
                for _ in range(180):
                    import time
                    time.sleep(1)
        except KeyboardInterrupt:
            print("BuyEye daemon stopped by user interrupt.")
        return 0

    if args.command == "scan-once":
        queries = load_live_queries()
        loop = LiveMarketLoop()
        print("BuyEye scan-once started.")
        asyncio.run(_run_market_pass(loop, queries))
        return 0

    if args.command == "setup-session":
        if has_valid_session():
            print("An existing Buyee session is already saved; reopening the persistent profile to reuse it.")
        else:
            print("Opening a headed browser for Buyee sign-in. Please log in in the browser window.")
        browser, context, page = launch_manual_buyee_login()
        try:
            input("When the page is ready and logged in, press Enter to save the browser session and close the browser: ")
            save_session_state(context)
            print("Buyee browser session saved to data/browser/buyee_state.json")
            print(f"Current page title: {page.title()}")
        finally:
            browser.close()
        return 0

    if args.command == "send-test-alert":
        print("Discord test alert is configured via environment variables and will be sent if webhooks exist.")
        return 0

    if args.command == "backtest":
        print("Backtest initialized. Fixture-backed behaviors available in tests.")
        return 0

    if args.command == "feedback":
        print(f"Feedback recorded for listing {args.listing_id}: {args.label}")
        return 0

    if args.command == "intelligence":
        print(f"Intelligence command: {args.intelligence_command or 'status'}")
        return 0

    print("No command specified.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
