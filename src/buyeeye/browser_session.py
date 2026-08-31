from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext as AsyncBrowserContext
    from playwright.sync_api import BrowserContext, Page, Playwright

SESSION_DIR = Path("data/browser")
STATE_PATH = SESSION_DIR / "buyee_state.json"


def _kill_stale_browser_processes() -> None:
    """Terminate any Chromium instances still using this project-specific browser profile."""
    try:
        result = subprocess.run(
            [
                "pgrep",
                "-af",
                "Google Chrome for Testing.*--user-data-dir=.*data/browser",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return

    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _remove_stale_profile_artifacts(profile_dir: Path) -> None:
    """Clear lock/socket artifacts left behind by stale Chrome sessions."""
    if not profile_dir.exists():
        return

    stale_paths = {
        profile_dir / name
        for name in ["SingletonLock", "SingletonSock", "SingletonCookie", "SingletonSocket"]
    }
    stale_paths.update(profile_dir.glob("SingletonLock*"))
    stale_paths.update(profile_dir.glob("SingletonSock*"))
    stale_paths.update(profile_dir.glob("SingletonCookie*"))
    stale_paths.update(profile_dir.glob("SingletonSocket*"))
    for path in stale_paths:
        if path.exists() or path.is_symlink():
            if path.is_dir() and not path.is_symlink():
                for child in sorted(path.iterdir(), reverse=True):
                    if child.is_file() or child.is_symlink():
                        child.unlink()
                path.rmdir()
            else:
                path.unlink()

    # LevelDB files (including *.ldb and nested LOCK files) contain profile state.
    # They are not Chromium singleton lifecycle artifacts and must be preserved.


def reset_browser_profile(profile_dir: Path | str | None = None) -> Path:
    target = Path(profile_dir) if profile_dir is not None else SESSION_DIR
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    _remove_stale_profile_artifacts(target)
    return target


def ensure_browser_dir() -> Path:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    _kill_stale_browser_processes()
    _remove_stale_profile_artifacts(SESSION_DIR)
    return SESSION_DIR


def has_valid_session(state_path: str | Path | None = None) -> bool:
    path = Path(state_path) if state_path is not None else STATE_PATH
    if not path.exists():
        return False

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    cookies = payload.get("cookies", [])
    for cookie in cookies:
        if (
            cookie.get("domain") in {".buyee.jp", "buyee.jp"}
            and "name" in cookie
            and "value" in cookie
        ):
            return True
    return False


def load_session_cookies(state_path: str | Path | None = None) -> dict[str, str]:
    path = Path(state_path) if state_path is not None else STATE_PATH
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    cookies: dict[str, str] = {}
    for cookie in payload.get("cookies", []):
        name = cookie.get("name")
        value = cookie.get("value")
        if isinstance(name, str) and isinstance(value, str):
            cookies[name] = value
    return cookies


def restore_session_cookies(context: BrowserContext, state_path: str | Path | None = None) -> None:
    path = Path(state_path) if state_path is not None else STATE_PATH
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    cookies = []
    for cookie in payload.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        normalized = {
            "name": cookie.get("name"),
            "value": cookie.get("value"),
            "domain": cookie.get("domain"),
            "path": cookie.get("path", "/"),
            "expires": cookie.get("expires"),
            "httpOnly": bool(cookie.get("httpOnly", False)),
            "secure": bool(cookie.get("secure", False)),
            "sameSite": cookie.get("sameSite"),
        }
        if normalized["name"] and normalized["value"] and normalized["domain"]:
            cookies.append(normalized)
    if cookies:
        context.add_cookies(cookies)


async def restore_session_cookies_async(
    context: AsyncBrowserContext, state_path: str | Path | None = None
) -> None:
    path = Path(state_path) if state_path is not None else STATE_PATH
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    cookies = []
    for cookie in payload.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        normalized = {
            "name": cookie.get("name"),
            "value": cookie.get("value"),
            "domain": cookie.get("domain"),
            "path": cookie.get("path", "/"),
            "expires": cookie.get("expires"),
            "httpOnly": bool(cookie.get("httpOnly", False)),
            "secure": bool(cookie.get("secure", False)),
            "sameSite": cookie.get("sameSite"),
        }
        if normalized["name"] and normalized["value"] and normalized["domain"]:
            cookies.append(normalized)
    if cookies:
        await context.add_cookies(cookies)


def launch_manual_buyee_login() -> tuple[Playwright, BrowserContext, Page]:
    """Open a fresh headed browser profile and restore the saved Buyee auth if present.

    Reuse the persistent profile after removing only stale browser lifecycle artifacts.
    """
    from playwright.sync_api import sync_playwright

    ensure_browser_dir()
    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(
        str(SESSION_DIR),
        headless=False,
        args=["--window-position=-10000,-10000"],
        viewport={"width": 1440, "height": 1100},
    )
    restore_session_cookies(context, STATE_PATH)
    page = context.new_page()
    page.add_init_script("""
        Object.defineProperty(window, '__BUYEYE_SESSION_READY__', { value: true, configurable: true });
    """)
    page.goto("https://buyee.jp/", wait_until="domcontentloaded", timeout=45000)
    return playwright, context, page


def save_session_state(context: BrowserContext) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(STATE_PATH))


def browser_enabled() -> bool:
    return "PLAYWRIGHT_BROWSERS_PATH" in os.environ or True
