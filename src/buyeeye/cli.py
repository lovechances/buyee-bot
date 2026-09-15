from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
import random
import signal
import sys
import time
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace
from typing import Any

from .browser_session import has_valid_session, launch_manual_buyee_login, save_session_state
from .db import StatusIncidentState, get_session, init_db
from .discord_alerts import send_test_alert
from .discovery import build_query_universe, query_layer_counts
from .live_sources import AuthExpiredError, LiveMarketLoop, ParserAnomalyError, load_live_queries
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
from .status_monitor import (
    MAJOR_UPDATE_MESSAGES,
    announce_major_update,
    record_auth_failure,
    record_scanning_success,
    record_source_failure,
    release_event_status,
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


def _record_source_attempt_failure(db_path: str | os.PathLike[str], exc: Exception) -> None:
    """Record actual parser failures; a valid empty search is not an anomaly."""
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "last_failure_at": now,
        "browser_restart_count": get_health(db_path).browser_restart_count + 1,
    }
    if isinstance(exc, ParserAnomalyError):
        values["last_parser_anomaly"] = now
    if isinstance(exc, AuthExpiredError):
        values.update(last_auth_confirmed=False, last_auth_observed_at=now)
    update_health(db_path, **values)


def _health_readiness(
    report: dict[str, Any], runtime: Any, *, now: datetime | None = None,
    stale_minutes: float = 30,
) -> tuple[str, float | None]:
    """Useful recent progress, not merely a file or a living Python process."""
    current = now or datetime.now(UTC)
    last = runtime.last_successful_scan
    age = None if last is None else max(0.0, (current - last.replace(tzinfo=last.tzinfo or UTC)).total_seconds() / 60)
    if not report["database_ready"] or not report["session_ready"]:
        return "degraded", age
    if getattr(runtime, "last_auth_confirmed", None) is False or report.get("auth_incident_open"):
        return "degraded", age
    started = getattr(runtime, "started_at", None)
    if age is None:
        if started and (current - started.replace(tzinfo=started.tzinfo or UTC)).total_seconds() > max(1, stale_minutes) * 60:
            return "degraded", None
        return "starting", None
    if age > max(1, stale_minutes) or runtime.consecutive_source_failures:
        return "degraded", age
    if started and last.replace(tzinfo=last.tzinfo or UTC) < started.replace(tzinfo=started.tzinfo or UTC):
        return "starting", age
    return "ready", age


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
    health = subparsers.add_parser("health")
    health.add_argument("--hours", type=int, choices=range(1, 73), default=1, metavar="1..72")
    subparsers.add_parser("storage-summary")
    subparsers.add_parser("setup-session")
    subparsers.add_parser("send-test-alert")
    status_event = subparsers.add_parser("status-event")
    status_event.add_argument("event", choices=sorted(MAJOR_UPDATE_MESSAGES))
    status_event.add_argument("--status", action="store_true", help="Read persisted release delivery state without registering or sending")
    backtest = subparsers.add_parser("backtest")
    backtest.add_argument("--cohort")
    backtest.add_argument("--full", action="store_true")
    diagnose = subparsers.add_parser("diagnose-listing")
    diagnose.add_argument("url_or_id")
    diagnose.add_argument("--source")
    benchmark = subparsers.add_parser("benchmark-listings")
    benchmark.add_argument("input_path")

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
    feedback.add_argument("--source")
    feedback.add_argument("--purchased", action="store_true")
    pin = subparsers.add_parser("pin-listing")
    pin.add_argument("url_or_id")
    pin.add_argument("--source")
    pin.add_argument("--reason", required=True)

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
    if args.command not in {"backtest", "diagnose-listing", "benchmark-listings", "storage-summary"} and not (
        args.command == "status-event" and args.status
    ):
        init_db(db_path)

    if args.command == "storage-summary":
        from .maintenance import storage_summary

        try:
            print(json.dumps(storage_summary(db_path), indent=2, default=str))
        except (OSError, ValueError) as exc:
            print(f"storage-summary: {type(exc).__name__}")
            return 2
        return 0

    if args.command == "status":
        print("BuyEye status: ready")
        print(f"DB: {db_path}")
        return 0

    if args.command == "health":
        report = health_report(db_path)
        runtime = get_health(db_path)
        with get_session(db_path) as session:
            incident = session.get(StatusIncidentState, 1)
            report["auth_incident_open"] = bool(
                incident and (incident.auth_failure_streak or incident.auth_required_announced)
            )
        metrics = hourly_metrics(db_path, hours=args.hours)
        readiness, progress_age = _health_readiness(
            report, runtime,
            stale_minutes=float(settings.get("status", {}).get("health_stale_minutes", 30)),
        )
        print(f"BuyEye health: {readiness}")
        print(f"Database: {report['database']}")
        print(f"Buyee portable session: {'present' if report['session_ready'] else 'unavailable'} (not a live authentication test)")
        auth_label = {True: "authenticated", False: "login not authenticated", None: "unverified"}
        print(f"Last source login check: {auth_label[runtime.last_auth_confirmed]} ({runtime.last_auth_observed_at or 'never'})")
        print(f"Last confirmed authenticated acquisition: {runtime.last_verified_auth_success or 'not yet verified'}")
        print(f"Authentication incident: {'open' if report['auth_incident_open'] else 'none'}")
        print(f"Last successful scan: {runtime.last_successful_scan or 'never'}")
        print(f"Successful acquisition age: {f'{progress_age:.1f} minutes' if progress_age is not None else 'no recorded success'}")
        print(f"Current/last query: {runtime.current_query or '-'} / {runtime.last_query or '-'}")
        print(f"Consecutive source failures: {runtime.consecutive_source_failures}")
        print(
            "Source/browser retry attempts (cumulative): "
            f"{runtime.browser_restart_count}"
        )
        print(
            "Processed listing passes (legacy counter): "
            f"{runtime.unique_listings_scanned}"
        )
        print(
            "Lifetime-first-seen listings (since Ultra telemetry): "
            f"{runtime.lifetime_first_seen_count or 0}"
        )
        print(f"Alerts emitted: {runtime.alerts_emitted}")
        print(
            f"Last {args.hours} hour(s): "
            f"queries={metrics['queries']} pages={metrics['search_pages']} cards={metrics['cards']} "
            f"processable={metrics['unique']} duplicates={metrics['duplicates']} "
            f"stage_a_telemetry_scans={metrics['stage_a_telemetry_scans']} "
            f"stage_a_telemetry_unique={metrics['stage_a_telemetry_unique']} "
            f"decision_telemetry_scans={metrics['decision_telemetry_scans']} "
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
            f"final_theses={metrics['theses']} alert_theses={metrics['alert_theses']} "
            f"opportunities={metrics['opportunities']} "
            f"strategies={metrics['strategy_types']} "
            f"stage_a_paths={metrics['stage_a_paths']} "
            f"stage_a_rejects={metrics['stage_a_reject_reasons']} "
            f"post_detail_rejects={metrics['post_detail_reject_reasons']} "
            f"relationships={metrics['piece_relationships']} "
            f"confidences={metrics['confidence_counts']} "
            f"recoveries={metrics['recovery_counts']} "
            f"lifetime_first_seen={metrics['lifetime_first_seen']} "
            f"historical_revisits={metrics['historical_revisits']} "
            f"material_state_changes={metrics['material_state_changes']} "
            f"suppressed_alerts={metrics['suppressed_alerts']} "
            f"failed_alerts={metrics['failed_alerts']} "
            f"logic_reconsidered={metrics['logic_reconsidered']} "
            f"decision_samples={metrics['post_detail_decisions']} "
            f"families={metrics['detected_family_funnel']}"
        )
        return 0 if readiness == "ready" else 1

    if args.command == "status-event":
        try:
            if args.status:
                print(json.dumps(release_event_status(args.event, db_path), indent=2))
                return 0
            result = announce_major_update(args.event, db_path)
        except ValueError as exc:
            print(f"BuyEye status event: {exc}")
            return 2
        print(f"BuyEye status event: {result.outcome}")
        if result.message_id:
            print(f"Confirmed Discord message: {result.message_id}; channel: {result.channel_id}")
        return 0 if result.emitted or result.outcome == "deduped" else 1

    if args.command == "run":
        from .maintenance import run_maintenance_slice, seed_protections

        lock_handle = _acquire_runtime_lock()
        if lock_handle is None:
            print("BuyEye daemon is already running; refusing a duplicate scanner.")
            return 2
        logger = _runtime_logger()
        pin_manifest = CONFIG_DIR / "retention_pins.yaml"
        if pin_manifest.exists():
            seed_protections(load_yaml(pin_manifest).get("records", []), db_path)
        search_config = load_yaml(CONFIG_DIR / "searches.yaml")
        taxonomy = load_yaml(CONFIG_DIR / "fashion_taxonomy.yaml")
        market_config = load_yaml(CONFIG_DIR / "market_structure.yaml")
        queries = build_query_universe(search_config, taxonomy, market_config)
        scanner = settings.get("scanner", {})
        layer_shares = scanner.get("query_layer_shares", {})
        strategy_shares = scanner.get("market_strategy_shares", {})
        status_settings = settings.get("status", {})
        query_gap = int(scanner.get("query_gap_seconds", 20))
        jitter = int(scanner.get("jitter_seconds", 30))
        max_attempts = int(scanner.get("max_source_attempts", 3))
        stopping = False

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
        try:
            while not stopping:
                try:
                    run_maintenance_slice(db_path, settings, notify=True)
                except Exception as exc:  # noqa: BLE001 - optional maintenance cannot kill acquisition
                    logger.warning("storage_maintenance_deferred error=%s", type(exc).__name__)
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
                attempts_made = 0
                for attempt in range(max_attempts):
                    attempts_made += 1
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
                        _record_source_attempt_failure(db_path, exc)
                        if isinstance(exc, AuthExpiredError):
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
                    record_query_result(
                        spec,
                        SimpleNamespace(
                            recovery_counts={"source_retries": max(0, attempts_made - 1)}
                        ),
                        db_path,
                        failed=True,
                    )
                    if isinstance(last_error, AuthExpiredError):
                        record_auth_failure(
                            db_path,
                            query=spec.query,
                            query_scope=spec.query_layer,
                            confirmation_threshold=int(
                                status_settings.get("auth_confirmation_threshold", 2)
                            ),
                        )
                    else:
                        record_source_failure(
                            failures,
                            db_path,
                            query=spec.query,
                            query_scope=spec.query_layer,
                            error_type=type(last_error).__name__,
                            outage_threshold=int(
                                status_settings.get("outage_consecutive_failures", 6)
                            ),
                            outage_min_duration_minutes=float(
                                status_settings.get("outage_min_duration_minutes", 15)
                            ),
                            notification_cooldown_minutes=float(
                                status_settings.get(
                                    "notification_cooldown_minutes", 360
                                )
                            ),
                        )
                    pause = (
                        900 if isinstance(last_error, AuthExpiredError) else min(300, 30 * failures)
                    )
                    for _ in range(pause):
                        if stopping:
                            break
                        time.sleep(1)
                    continue

                recovery_counts = dict(getattr(result, "recovery_counts", {}) or {})
                recovery_counts["source_retries"] = max(0, attempts_made - 1)
                result.recovery_counts = recovery_counts
                prior = get_health(db_path)
                auth_confirmed = getattr(result, "auth_confirmed", None)
                update_health(
                    db_path,
                    current_query=None,
                    last_query=spec.query,
                    last_successful_scan=datetime.now(UTC),
                    **({"last_auth_success": datetime.now(UTC), "last_verified_auth_success": datetime.now(UTC)}
                       if auth_confirmed is True and result.scanned > 0 else {}),
                    last_auth_observed_at=datetime.now(UTC),
                    last_auth_confirmed=auth_confirmed,
                    consecutive_source_failures=0,
                    unique_listings_scanned=prior.unique_listings_scanned + result.unique_listings,
                    lifetime_first_seen_count=(prior.lifetime_first_seen_count or 0)
                    + result.lifetime_first_seen,
                    alerts_emitted=prior.alerts_emitted + result.new_alerts,
                )
                record_query_result(spec, result, db_path)
                logger.info(
                    "scan query=%r family=%s layer=%s strategy=%s strategy_id=%s "
                    "pages=%s cards=%s processable=%s duplicates=%s "
                    "brand_confirmed=%s category_valid=%s stage_a_eligible=%s "
                    "stage_a_capped=%s stage_a_rejected=%s stage_a_paths=%s stage_a_rejects=%s "
                    "stage_b=%s enriched=%s failed=%s post_detail_rejected=%s "
                    "post_detail_rejects=%s relationships=%s confidences=%s "
                    "comps=%s cache=%s/%s "
                    "final=%s alerts=%s suppressed=%s failed_alerts=%s "
                    "routes=%s final_theses=%s alert_theses=%s opportunities=%s "
                    "recoveries=%s lifetime_first_seen=%s historical_revisits=%s "
                    "material_state_changes=%s logic_reconsidered=%s decisions=%s",
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
                    getattr(result, "post_detail_reject_counts", {}),
                    getattr(result, "piece_relationship_counts", {}),
                    getattr(result, "confidence_counts", {}),
                    result.comp_queried,
                    result.comp_cache_hits,
                    result.comp_cache_misses,
                    result.final_candidates,
                    result.new_alerts,
                    result.suppressed_alerts,
                    result.failed_alerts,
                    result.route_counts,
                    result.thesis_counts,
                    getattr(result, "alert_thesis_counts", {}),
                    result.opportunity_counts,
                    recovery_counts,
                    getattr(result, "lifetime_first_seen", 0),
                    getattr(result, "historical_revisits", 0),
                    getattr(result, "material_state_changes", 0),
                    getattr(result, "logic_reconsidered", 0),
                    getattr(result, "post_detail_decisions", []),
                )
                # A completed acquisition is operational success even when a
                # narrowly filtered marketplace slice legitimately has zero
                # cards. Parser anomalies raise before reaching this point.
                if auth_confirmed is False:
                    record_auth_failure(
                        db_path, query=spec.query, query_scope=spec.query_layer,
                        confirmation_threshold=int(status_settings.get("auth_confirmation_threshold", 2)),
                        notification_cooldown_minutes=float(status_settings.get("notification_cooldown_minutes", 360)),
                    )
                record_scanning_success(
                    db_path,
                    query=spec.query,
                    query_scope=spec.query_layer,
                    cards_seen=result.scanned,
                    auth_confirmed=auth_confirmed,
                    recovery_confirmation_scans=int(
                        status_settings.get("recovery_confirmation_scans", 2)
                    ),
                )
                pause = query_gap + random.randint(0, max(0, jitter))
                for _ in range(pause):
                    if stopping:
                        break
                    time.sleep(1)
        finally:
            update_health(db_path, current_query=None)
            logger.info("shutdown graceful=%s", stopping)
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
            f"processable={result.unique_listings} stage_a_rejected={result.stage_a_rejected} "
            f"brand_confirmed={result.stage_a_brand_confirmed} "
            f"category_valid={result.stage_a_category_valid} "
            f"stage_a_eligible={result.stage_a_eligible} stage_a_capped={result.stage_a_capped} "
            f"stage_a_paths={result.stage_a_path_counts} "
            f"stage_a_rejects={result.stage_a_reject_counts} "
            f"stage_b_candidates={result.stage_b_candidates} "
            f"stage_b_enriched={result.stage_b_enriched} stage_b_failed={result.stage_b_failed} "
            f"stage_b_rejected={result.stage_b_rejected} "
            f"post_detail_rejects={getattr(result, 'post_detail_reject_counts', {})} "
            f"relationships={getattr(result, 'piece_relationship_counts', {})} "
            f"confidences={getattr(result, 'confidence_counts', {})} "
            f"comp_queried={result.comp_queried} final_candidates={result.final_candidates} "
            f"alerts=0 routes={result.route_counts} final_theses={result.thesis_counts} "
            f"alert_theses={getattr(result, 'alert_thesis_counts', {})} "
            f"opportunities={result.opportunity_counts} "
            f"recoveries={getattr(result, 'recovery_counts', {})} "
            f"lifetime_first_seen={getattr(result, 'lifetime_first_seen', 0)} "
            f"historical_revisits={getattr(result, 'historical_revisits', 0)} "
            f"material_state_changes={getattr(result, 'material_state_changes', 0)} "
            f"logic_reconsidered={getattr(result, 'logic_reconsidered', 0)}"
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
            f"post_detail_rejects={getattr(result, 'post_detail_reject_counts', {})} "
            f"relationships={getattr(result, 'piece_relationship_counts', {})} "
            f"confidences={getattr(result, 'confidence_counts', {})} "
            f"comp_queried={result.comp_queried} comp_candidates={result.comp_candidates} "
            f"comps_accepted={result.comps_accepted} japan_comp_candidates={result.japan_comp_candidates} "
            f"japan_comps_accepted={result.japan_comps_accepted} comp_cache_hits={result.comp_cache_hits} "
            f"comp_cache_misses={result.comp_cache_misses} pages={result.search_pages} "
            f"processable={result.unique_listings} duplicates={result.duplicates} "
            f"final_candidates={result.final_candidates} "
            f"routes={result.route_counts} final_theses={result.thesis_counts} "
            f"alert_theses={getattr(result, 'alert_thesis_counts', {})} "
            f"opportunities={result.opportunity_counts} "
            f"recoveries={getattr(result, 'recovery_counts', {})} "
            f"lifetime_first_seen={getattr(result, 'lifetime_first_seen', 0)} "
            f"historical_revisits={getattr(result, 'historical_revisits', 0)} "
            f"material_state_changes={getattr(result, 'material_state_changes', 0)} "
            f"logic_reconsidered={getattr(result, 'logic_reconsidered', 0)}"
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
        from .calibration import CalibrationError, run_calibration

        try:
            report = run_calibration(args.cohort)
        except (CalibrationError, FileNotFoundError, ValueError) as exc:
            print(f"backtest: {exc}")
            return 2
        output = report if args.full else {
            "cohort_version": report["cohort_version"],
            "decision_logic_version": report["decision_logic_version"],
            "historical_note": report["historical_note"],
            "summary": report["summary"],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        return 0 if report["summary"]["alignment"].get("misaligned", 0) == 0 else 1

    if args.command == "diagnose-listing":
        from .diagnostics import diagnose_listing

        try:
            report = diagnose_listing(
                db_path,
                args.url_or_id,
                source=args.source,
            )
        except ValueError as exc:
            print(f"diagnose-listing: {exc}")
            return 2
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0 if report.get("seen") else 1

    if args.command == "benchmark-listings":
        from .diagnostics import benchmark_listings

        try:
            report = benchmark_listings(db_path, args.input_path)
        except ValueError as exc:
            print(f"benchmark-listings: {exc}")
            return 2
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0 if "invalid_input" not in report["outcomes"] else 1

    if args.command == "feedback":
        from .diagnostics import marketplace_listing_identity
        from .maintenance import protect_listing

        try:
            source, listing_id = marketplace_listing_identity(args.listing_id, source=args.source)
            protect_listing(source, listing_id, db_path, feedback_label=args.label,
                            purchased=args.purchased, provenance="explicit_user_cli")
        except ValueError as exc:
            print(f"feedback: {exc}")
            return 2
        print(f"Human feedback persisted for {source}:{listing_id}; no authenticity certification or eligibility override.")
        return 0

    if args.command == "pin-listing":
        from .diagnostics import marketplace_listing_identity
        from .maintenance import protect_listing

        try:
            source, listing_id = marketplace_listing_identity(args.url_or_id, source=args.source)
            protect_listing(source, listing_id, db_path, reason=args.reason)
        except ValueError as exc:
            print(f"pin-listing: {exc}")
            return 2
        print(f"Evidence pinned for {source}:{listing_id}; live eligibility unchanged.")
        return 0

    if args.command == "intelligence":
        print(f"Intelligence command: {args.intelligence_command or 'status'}")
        return 0

    print("No command specified.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
