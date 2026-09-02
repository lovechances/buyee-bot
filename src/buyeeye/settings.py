from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_env_file(path: str | Path | None = None) -> None:
    """Load local runtime secrets without logging or overwriting inherited values."""
    env_path = Path(path) if path else ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name.replace("_", "").isalnum():
            os.environ.setdefault(name, value)


# Load local development values before deriving module-level paths. In hosted environments,
# inherited variables still win because load_env_file never overwrites them.
load_env_file()


def runtime_data_dir() -> Path:
    """Return the persistent runtime root, always honoring Railway's attached volume."""
    configured = os.environ.get("BUYEYE_DATA_DIR")
    railway_mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    if railway_mount:
        return Path(railway_mount).expanduser()
    if configured:
        return Path(configured).expanduser()
    return ROOT / "data"


def runtime_state_path(variable: str, relative_path: str) -> Path:
    """Resolve mutable state and keep Railway paths inside its mounted persistent root."""
    root = runtime_data_dir()
    configured = os.environ.get(variable)
    if not configured:
        return root / relative_path

    candidate = Path(configured).expanduser()
    railway_mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    if not railway_mount:
        return candidate

    # Local .env defaults such as data/browser must never redirect a Railway process to /app/data.
    # Absolute overrides are accepted only when they remain under the attached volume.
    if candidate.is_absolute() and (candidate == root or root in candidate.parents):
        return candidate
    return root / relative_path


def _config_dir() -> Path:
    configured = Path(os.environ.get("BUYEYE_CONFIG_DIR", ROOT / "config")).expanduser()
    if configured.is_absolute():
        return configured
    if os.environ.get("RAILWAY_VOLUME_MOUNT_PATH"):
        return Path("/app") / configured
    return ROOT / configured


CONFIG_DIR = _config_dir()
DEFAULT_SETTINGS_PATH = CONFIG_DIR / "settings.example.yaml"


def load_yaml(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_SETTINGS_PATH
    if not config_path.exists():
        alt = CONFIG_DIR / "settings.yaml"
        if alt.exists():
            config_path = alt
        else:
            config_path = DEFAULT_SETTINGS_PATH
    return load_yaml(config_path)
