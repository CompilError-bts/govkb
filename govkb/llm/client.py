from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMConfig:
    provider: str = "none"
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    timeout: int = 60


class LLMClient:
    """Small OpenAI/Claude-compatible LLM client used by SiteAnalyzer."""

    def __init__(self, config: dict[str, Any] | LLMConfig | None = None):
        if isinstance(config, LLMConfig):
            self.config = config
        else:
            self.config = self._load_config(config or {})

    @property
    def enabled(self) -> bool:
        return bool(self.config.provider and self.config.provider != "none" and self.config.api_key)

    def complete(self, prompt: str) -> str:
        if not self.enabled:
            raise RuntimeError("LLM is not configured")
        if self.config.provider == "claude":
            return self._complete_claude(prompt)
        return self._complete_openai_compatible(prompt)

    def _complete_openai_compatible(self, prompt: str) -> str:
        base_url = self.config.base_url or "https://api.openai.com/v1"
        model = self.config.model or "gpt-4o-mini"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You return valid JSON when asked. Do not wrap JSON in Markdown."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        }
        data = self._post_json(f"{base_url.rstrip('/')}/chat/completions", payload)
        return data["choices"][0]["message"]["content"]

    def _complete_claude(self, prompt: str) -> str:
        base_url = self.config.base_url or "https://api.anthropic.com/v1"
        model = self.config.model or "claude-3-5-sonnet-latest"
        payload = {
            "model": model,
            "max_tokens": 4096,
            "temperature": 0.1,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
        }
        data = self._post_json(f"{base_url.rstrip('/')}/messages", payload, headers=headers)
        content = data.get("content") or []
        if content and isinstance(content, list):
            return "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return str(data)

    def _post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        merged_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(headers or {}),
        }
        if self.config.provider != "claude":
            merged_headers["Authorization"] = f"Bearer {self.config.api_key}"

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=merged_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM request failed: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc

    def _load_config(self, config: dict[str, Any]) -> LLMConfig:
        provider = str(config.get("provider") or "none").lower()
        api_key = self._expand_env(str(config.get("api_key") or ""))
        if not api_key and provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key and provider == "claude":
            api_key = os.getenv("ANTHROPIC_API_KEY", "")

        return LLMConfig(
            provider=provider,
            api_key=api_key,
            model=str(config.get("model") or ""),
            base_url=str(config.get("base_url") or ""),
            timeout=int(config.get("timeout") or 60),
        )

    def _expand_env(self, value: str) -> str:
        if value.startswith("${") and value.endswith("}"):
            return os.getenv(value[2:-1], "")
        return os.path.expandvars(value)

