from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
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
