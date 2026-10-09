"""Lightweight entry point for the existing interactive session refresh."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="buyeeye setup-session",
        description="Refresh the existing Buyee profile through manual browser login.",
    )
    parser.parse_args(argv)
    print("Starting manual Buyee login; no scanner or database is started.", flush=True)

    from .browser_session import (
        STATE_PATH,
        has_valid_session,
        launch_manual_buyee_login,
        save_session_state,
    )

    print(
        "Reopening the existing profile for manual login."
        if has_valid_session()
        else "Opening a visible browser for manual Buyee login.",
        flush=True,
    )
    playwright, context, page = launch_manual_buyee_login()
    try:
        input("Once logged in, press Enter to save the session and close the browser: ")
        save_session_state(context)
        print(f"Buyee browser session saved to {STATE_PATH}", flush=True)
        print(f"Current page title: {page.title()}", flush=True)
    finally:
        context.close()
        playwright.stop()
    return 0
