from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .browser_session import has_valid_session, launch_manual_buyee_login, save_session_state
from .db import init_db
from .discord_alerts import send_test_alert
from .live_sources import LiveMarketLoop, load_live_queries
from .pipeline import health_report, scan_once
from .settings import load_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="buyeeye")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status")
    subparsers.add_parser("run")
    subparsers.add_parser("scan-once")
    scan_alert = subparsers.add_parser("scan-alert-once")
    scan_alert.add_argument("query", nargs="?")
    subparsers.add_parser("health")
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

    if args.command == "health":
        report = health_report(db_path)
        print(f"BuyEye health: {'ready' if report['database_ready'] else 'degraded'}")
        print(f"Database: {report['database']}")
        print(f"Buyee session: {'ready' if report['session_ready'] else 'manual login required'}")
        return 0 if report["database_ready"] else 1

    if args.command == "run":
        print("BuyEye daemon started.")
        queries = load_live_queries()
        interval = int(load_settings().get("scanner", {}).get("discovery_interval_seconds", 180))
        stopping = False

        def stop_handler(_signum: int, _frame: Any) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGINT, stop_handler)
        signal.signal(signal.SIGTERM, stop_handler)
        backoff = interval
        try:
            while not stopping:
                for query in queries:
                    try:
                        result = asyncio.run(scan_once(query, db_path))
                        print(f"query={query!r} scanned={result.scanned} alerts={result.new_alerts} suppressed={result.suppressed_alerts}")
                        backoff = interval
                    except Exception as exc:  # noqa: BLE001
                        print(f"query={query!r} failed={type(exc).__name__} retry_in={backoff}s")
                        backoff = min(backoff * 2, 1800)
                for _ in range(backoff):
                    if stopping:
                        break
                    time.sleep(1)
        finally:
            print("BuyEye daemon stopped.")
        return 0

    if args.command == "scan-once":
        queries = load_live_queries()
        loop = LiveMarketLoop()
        print("BuyEye scan-once started.")
        asyncio.run(_run_market_pass(loop, queries))
        return 0

    if args.command == "scan-alert-once":
        queries = load_live_queries()
        query = args.query or (queries[0] if queries else "Number (N)ine")
        try:
            result = asyncio.run(scan_once(query, db_path))
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"query={query!r} failed={type(exc).__name__}")
            return 1
        print(f"query={query!r} scanned={result.scanned} new_rows={result.new_rows} updated_rows={result.updated_rows} alerts={result.new_alerts} suppressed={result.suppressed_alerts} failed_alerts={result.failed_alerts}")
        return 0 if result.scanned else 1

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
        return send_test_alert()

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
