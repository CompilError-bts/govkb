from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from govkb.core.orchestrator import Orchestrator


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        config = load_config(args.config)
        orchestrator = Orchestrator(config)
        result = orchestrator.run(url=args.url, city=args.city, months=args.months, max_pages=args.max_pages)
        print(json.dumps(_jsonable(result), ensure_ascii=False, indent=2))
        return 0

    if args.command == "wizard":
        print("wizard mode is not implemented yet. Use: govkb run --url https://example.gov.cn --months 3")
        return 2

    if args.command == "webui":
        from govkb.webui import serve

        serve(args.host, args.port, open_browser=not args.no_open)
        return 0

    parser.print_help()
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="govkb", description="Generate a government news knowledge base as docx files.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Scrape recent news and generate docx files")
    target = run_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--url", help="Government portal URL, such as https://www.xianyang.gov.cn")
    target.add_argument("--city", help="City/district name. Requires an LLM config to resolve the official site.")
    run_parser.add_argument("--months", type=int, default=3, help="How many recent months to scrape")
    run_parser.add_argument("--max-pages", type=int, default=None, help="Maximum list pages to scan")

    subparsers.add_parser("wizard", help="Interactive mode")

    webui_parser = subparsers.add_parser("webui", help="Start the local WebUI")
    webui_parser.add_argument("--host", default="127.0.0.1")
    webui_parser.add_argument("--port", type=int, default=8765)
    webui_parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    return parser


def load_config(path: str) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    text = config_path.read_text(encoding="utf-8")
    try:
        import yaml
    except ModuleNotFoundError:
        return _parse_simple_yaml(text)
    loaded = yaml.safe_load(text)
    return loaded or {}


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Tiny YAML subset parser for config.yaml when PyYAML is unavailable."""

    root: dict[str, Any] = {}
    current_section: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current_section = {}
            root[line[:-1].strip()] = current_section
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed_value = _parse_scalar(value.strip())
        if line.startswith(" ") and current_section is not None:
            current_section[key.strip()] = parsed_value
        else:
            root[key.strip()] = parsed_value
    return root


def _parse_scalar(value: str) -> Any:
    value = value.strip("'\"")
    if value.isdigit():
        return int(value)
    try:
        return float(value)
    except ValueError:
        return value


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    sys.exit(main())
