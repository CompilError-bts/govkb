from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS: dict[str, Any] = {
    "provider": "deepseek",
    "api_key": "",
    "model": "deepseek-v4-flash",
    "base_url": "https://api.deepseek.com/v1",
    "output_dir": "",
    "recipe_dir": "",
    "months": 3,
    "max_pages": 30,
    "rate_limit": 0.3,
    "concurrency": 3,
    "show_trace": True,
}


def settings_path() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        base = Path(appdata) / "GovKB"
    else:
        base = Path.home() / ".govkb"
    return base / "settings.json"


def load_settings() -> dict[str, Any]:
    data = dict(DEFAULT_SETTINGS)
    path = settings_path()
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
    data["output_dir"] = data.get("output_dir") or str(Path.cwd() / "output")
    data["recipe_dir"] = data.get("recipe_dir") or str(Path.cwd() / "recipes")
    return data


def save_settings(settings: dict[str, Any]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = dict(DEFAULT_SETTINGS)
    cleaned.update({key: value for key, value in settings.items() if key in DEFAULT_SETTINGS})
    path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")


def build_runtime_config(settings: dict[str, Any]) -> dict[str, Any]:
    provider = str(settings.get("provider") or "none")
    llm_provider = "openai" if provider == "deepseek" else provider
    return {
        "llm": {
            "provider": llm_provider,
            "api_key": str(settings.get("api_key") or "").strip(),
            "model": str(settings.get("model") or "").strip(),
            "base_url": str(settings.get("base_url") or "").strip(),
            "timeout": 90,
        },
        "scraper": {
            "concurrency": int(settings.get("concurrency") or 3),
            "rate_limit": float(settings.get("rate_limit") or 0.3),
            "timeout": 15,
            "max_pages": int(settings.get("max_pages") or 30),
        },
        "output": {"dir": str(settings.get("output_dir") or "output").strip()},
        "recipe_dir": str(settings.get("recipe_dir") or "recipes").strip(),
    }
