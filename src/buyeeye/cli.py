from __future__ import annotations

import argparse
import asyncio
import fcntl
import logging
import os
import random
import signal
import sys
import time
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any

from .browser_session import has_valid_session, launch_manual_buyee_login, save_session_state
from .db import init_db
from .discord_alerts import send_status_event, send_test_alert
from .discovery import build_query_universe, query_layer_counts
from .live_sources import AuthExpiredError, LiveMarketLoop, load_live_queries
from .pipeline import health_report, scan_once
from .runtime_state import (
    choose_query,
    get_health,
    hourly_metrics,
    record_query_result,
    update_health,
)
from .settings import (
    CONFIG_DIR,
    load_env_file,
    load_settings,
    load_yaml,
    runtime_data_dir,
    runtime_state_path,
)


def _runtime_logger() -> logging.Logger:
    log_dir = runtime_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("buyeeye.runtime")
    if not logger.handlers:
        handler = RotatingFileHandler(
            log_dir / "buyeeye.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.addHandler(logging.StreamHandler())
        logger.setLevel(logging.INFO)
    return logger


def _acquire_runtime_lock() -> Any | None:
    lock_path = runtime_data_dir() / "buyeeye.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="buyeeye")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status")
    subparsers.add_parser("run")
    scan_once_parser = subparsers.add_parser("scan-once")
    scan_once_parser.add_argument("query", nargs="?")
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
    load_env_file()
    parser = _build_parser()
    args = parser.parse_args(argv)

    settings = load_settings()
    db_path = runtime_state_path("BUYEYE_DB_PATH", "buyeeye.db")
    init_db(db_path)

    if args.command == "status":
        print("BuyEye status: ready")
        print(f"DB: {db_path}")
        return 0

    if args.command == "health":
        report = health_report(db_path)
        runtime = get_health(db_path)
        metrics = hourly_metrics(db_path)
        print(f"BuyEye health: {'ready' if report['database_ready'] else 'degraded'}")
        print(f"Database: {report['database']}")
        print(f"Buyee session: {'ready' if report['session_ready'] else 'manual login required'}")
        print(f"Last successful scan: {runtime.last_successful_scan or 'never'}")
        print(f"Current/last query: {runtime.current_query or '-'} / {runtime.last_query or '-'}")
        print(f"Consecutive source failures: {runtime.consecutive_source_failures}")
        print(f"Browser restarts: {runtime.browser_restart_count}")
        print(f"Unique listings scanned: {runtime.unique_listings_scanned}")
        print(f"Alerts emitted: {runtime.alerts_emitted}")
        print(
            "Last hour: "
            f"queries={metrics['queries']} pages={metrics['search_pages']} cards={metrics['cards']} "
            f"unique={metrics['unique']} duplicates={metrics['duplicates']} "
            f"stage_a_telemetry_scans={metrics['stage_a_telemetry_scans']} "
            f"stage_a_telemetry_unique={metrics['stage_a_telemetry_unique']} "
            f"brand_confirmed={metrics['stage_a_brand_confirmed']} "
            f"category_valid={metrics['stage_a_category_valid']} "
            f"stage_a_eligible={metrics['stage_a_eligible']} "
            f"stage_a_capped={metrics['stage_a_capped']} "
            f"stage_a_rejected={metrics['stage_a_rejected']} stage_b={metrics['stage_b_candidates']} "
            f"enriched={metrics['stage_b_enriched']} failed={metrics['stage_b_failed']} "
            f"post_detail_rejected={metrics['stage_b_rejected']} comps={metrics['comp_lookups']} "
            f"comp_cache={metrics['comp_cache_hits']}/{metrics['comp_cache_misses']} "
            f"final={metrics['final_candidates']} alerts={metrics['alerts']} "
            f"routes={metrics['routes']} alert_routes={metrics['alert_routes']} "
            f"theses={metrics['theses']} "
            f"opportunities={metrics['opportunities']} "
            f"strategies={metrics['strategy_types']} "
            f"stage_a_paths={metrics['stage_a_paths']} "
            f"stage_a_rejects={metrics['stage_a_reject_reasons']} "
            f"families={metrics['detected_family_funnel']}"
        )
        return 0 if report["database_ready"] else 1

    if args.command == "run":
        lock_handle = _acquire_runtime_lock()
        if lock_handle is None:
            print("BuyEye daemon is already running; refusing a duplicate scanner.")
            return 2
        logger = _runtime_logger()
        search_config = load_yaml(CONFIG_DIR / "searches.yaml")
        taxonomy = load_yaml(CONFIG_DIR / "fashion_taxonomy.yaml")
        market_config = load_yaml(CONFIG_DIR / "market_structure.yaml")
        queries = build_query_universe(search_config, taxonomy, market_config)
        scanner = settings.get("scanner", {})
        layer_shares = scanner.get("query_layer_shares", {})
        strategy_shares = scanner.get("market_strategy_shares", {})
        query_gap = int(scanner.get("query_gap_seconds", 20))
        jitter = int(scanner.get("jitter_seconds", 30))
        max_attempts = int(scanner.get("max_source_attempts", 3))
        stopping = False
        zero_result_streak = 0
        recovery_pending = False
        auth_warning_sent = False

        def stop_handler(_signum: int, _frame: Any) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGINT, stop_handler)
        signal.signal(signal.SIGTERM, stop_handler)
        update_health(db_path, started_at=datetime.now(UTC), current_query=None)
        layers = query_layer_counts(queries)
        logger.info(
            "startup query_universe=%s query_layers=%s target_shares=%s",
            len(queries),
            layers,
            layer_shares,
        )
        send_status_event(
            f"BuyEye started with {len(queries)} rotating discovery queries "
            f"(broad={layers['broad']}, medium={layers['medium']}, "
            f"surgical={layers['surgical']}, market_structure={layers['market_structure']})."
        )
        try:
            while not stopping:
                spec = choose_query(
                    queries,
                    db_path,
                    layer_shares=layer_shares,
                    strategy_shares=strategy_shares,
                )
                if spec is None:
                    time.sleep(30)
                    continue
                update_health(db_path, current_query=spec.query)
                result = None
                last_error: Exception | None = None
                for attempt in range(max_attempts):
                    try:
                        result = asyncio.run(scan_once(spec, db_path, global_dedupe=True))
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        wait_seconds = min(60, 5 * (2**attempt))
                        logger.warning(
                            "source_failure query=%r attempt=%s/%s error=%s retry_in=%ss",
                            spec.query,
                            attempt + 1,
                            max_attempts,
                            type(exc).__name__,
                            wait_seconds,
                        )
                        update_health(
                            db_path,
                            last_failure_at=datetime.now(UTC),
                            browser_restart_count=get_health(db_path).browser_restart_count + 1,
                        )
                        if isinstance(exc, AuthExpiredError):
                            if not auth_warning_sent:
                                send_status_event(
                                    "Buyee authentication expired. Run `buyeeye setup-session`; discovery is backing off.",
                                    "warning",
                                )
                                auth_warning_sent = True
                            break
                        if attempt + 1 < max_attempts:
                            time.sleep(wait_seconds)
                if result is None:
                    health = get_health(db_path)
                    failures = health.consecutive_source_failures + 1
                    update_health(
                        db_path,
                        current_query=None,
                        last_query=spec.query,
                        consecutive_source_failures=failures,
                    )
                    record_query_result(spec, object(), db_path, failed=True)
                    if failures == 3:
                        send_status_event(
                            f"Sustained Buyee failure after 3 queries: {type(last_error).__name__ if last_error else 'unknown'}.",
                            "error",
                        )
                    pause = (
                        900 if isinstance(last_error, AuthExpiredError) else min(300, 30 * failures)
                    )
                    for _ in range(pause):
                        if stopping:
                            break
                        time.sleep(1)
                    continue

                if result.scanned == 0:
                    zero_result_streak += 1
                    if zero_result_streak == 3:
                        update_health(db_path, last_parser_anomaly=datetime.now(UTC))
                        send_status_event(
                            "Three consecutive zero-result searches; scanning paused briefly for source/parser health.",
                            "warning",
                        )
                else:
                    zero_result_streak = 0
                    auth_warning_sent = False
                prior = get_health(db_path)
                if prior.consecutive_source_failures:
                    recovery_pending = True
                update_health(
                    db_path,
                    current_query=None,
                    last_query=spec.query,
                    last_successful_scan=datetime.now(UTC),
                    last_auth_success=datetime.now(UTC),
                    consecutive_source_failures=0,
                    unique_listings_scanned=prior.unique_listings_scanned + result.unique_listings,
                    alerts_emitted=prior.alerts_emitted + result.new_alerts,
                )
                record_query_result(spec, result, db_path)
                logger.info(
                    "scan query=%r family=%s layer=%s strategy=%s strategy_id=%s "
                    "pages=%s cards=%s unique=%s duplicates=%s "
                    "brand_confirmed=%s category_valid=%s stage_a_eligible=%s "
                    "stage_a_capped=%s stage_a_rejected=%s stage_a_paths=%s stage_a_rejects=%s "
                    "stage_b=%s enriched=%s failed=%s post_detail_rejected=%s "
                    "comps=%s cache=%s/%s "
                    "final=%s alerts=%s routes=%s theses=%s opportunities=%s",
                    spec.query,
                    spec.family,
                    spec.query_layer,
                    spec.strategy_type,
                    spec.strategy_id,
                    result.search_pages,
                    result.scanned,
                    result.unique_listings,
                    result.duplicates,
                    result.stage_a_brand_confirmed,
                    result.stage_a_category_valid,
                    result.stage_a_eligible,
                    result.stage_a_capped,
                    result.stage_a_rejected,
                    result.stage_a_path_counts,
                    result.stage_a_reject_counts,
                    result.stage_b_candidates,
                    result.stage_b_enriched,
                    result.stage_b_failed,
                    result.stage_b_rejected,
                    result.comp_queried,
                    result.comp_cache_hits,
                    result.comp_cache_misses,
                    result.final_candidates,
                    result.new_alerts,
                    result.route_counts,
                    result.thesis_counts,
                    result.opportunity_counts,
                )
                if recovery_pending:
                    send_status_event(
                        "BuyEye recovered and completed a successful scan.", "recovery"
                    )
                    recovery_pending = False
                pause = query_gap + random.randint(0, max(0, jitter))
                for _ in range(pause):
                    if stopping:
                        break
                    time.sleep(1)
        finally:
            update_health(db_path, current_query=None)
            logger.info("shutdown graceful=%s", stopping)
            if not stopping:
                send_status_event("BuyEye stopped unexpectedly.", "error")
            lock_handle.close()
        return 0

    if args.command == "scan-once":
        queries = load_live_queries()
        query = args.query or (queries[0] if queries else "Number (N)ine")
        try:
            result = asyncio.run(scan_once(query, db_path, send_alerts=False))
        except Exception as exc:  # noqa: BLE001
            print(f"query={query!r} failed={type(exc).__name__}")
            return 1
        print(
            f"query={query!r} pages={result.search_pages} scanned={result.scanned} "
            f"unique={result.unique_listings} stage_a_rejected={result.stage_a_rejected} "
            f"brand_confirmed={result.stage_a_brand_confirmed} "
            f"category_valid={result.stage_a_category_valid} "
            f"stage_a_eligible={result.stage_a_eligible} stage_a_capped={result.stage_a_capped} "
            f"stage_a_paths={result.stage_a_path_counts} "
            f"stage_a_rejects={result.stage_a_reject_counts} "
            f"stage_b_candidates={result.stage_b_candidates} "
            f"stage_b_enriched={result.stage_b_enriched} stage_b_failed={result.stage_b_failed} "
            f"stage_b_rejected={result.stage_b_rejected} "
            f"comp_queried={result.comp_queried} final_candidates={result.final_candidates} "
            f"alerts=0 routes={result.route_counts} theses={result.thesis_counts} "
            f"opportunities={result.opportunity_counts}"
        )
        return 0 if result.scanned else 1

    if args.command == "scan-alert-once":
        queries = load_live_queries()
        query = args.query or (queries[0] if queries else "Number (N)ine")
        try:
            result = asyncio.run(scan_once(query, db_path))
        except Exception as exc:  # noqa: BLE001
            print(f"query={query!r} failed={type(exc).__name__}")
            return 1
        print(
            f"query={query!r} scanned={result.scanned} rejected_irrelevant={result.rejected_irrelevant} "
            f"rejected_invalid={result.rejected_invalid} below_threshold={result.below_threshold} "
            f"new_rows={result.new_rows} updated_rows={result.updated_rows} alerts={result.new_alerts} "
            f"suppressed={result.suppressed_alerts} failed_alerts={result.failed_alerts} "
            f"stage_a_rejected={result.stage_a_rejected} stage_b_candidates={result.stage_b_candidates} "
            f"stage_a_eligible={result.stage_a_eligible} stage_a_capped={result.stage_a_capped} "
            f"stage_a_paths={result.stage_a_path_counts} "
            f"stage_a_rejects={result.stage_a_reject_counts} "
            f"stage_b_enriched={result.stage_b_enriched} stage_b_failed={result.stage_b_failed} "
            f"stage_b_rejected={result.stage_b_rejected} "
            f"comp_queried={result.comp_queried} comp_candidates={result.comp_candidates} "
            f"comps_accepted={result.comps_accepted} japan_comp_candidates={result.japan_comp_candidates} "
            f"japan_comps_accepted={result.japan_comps_accepted} comp_cache_hits={result.comp_cache_hits} "
            f"comp_cache_misses={result.comp_cache_misses} pages={result.search_pages} "
            f"unique={result.unique_listings} duplicates={result.duplicates} "
            f"final_candidates={result.final_candidates} "
            f"routes={result.route_counts} theses={result.thesis_counts} "
            f"opportunities={result.opportunity_counts}"
        )
        return 0 if result.scanned else 1

    if args.command == "setup-session":
        if has_valid_session():
            print(
                "An existing Buyee session is already saved; reopening the persistent profile to reuse it."
            )
        else:
            print(
                "Opening a headed browser for Buyee sign-in. Please log in in the browser window."
            )
        playwright, context, page = launch_manual_buyee_login()
        try:
            input(
                "When the page is ready and logged in, press Enter to save the browser session and close the browser: "
            )
            save_session_state(context)
            print(
                f"Buyee browser session saved to {runtime_data_dir() / 'browser/buyee_state.json'}"
            )
            print(f"Current page title: {page.title()}")
        finally:
            context.close()
            playwright.stop()
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
