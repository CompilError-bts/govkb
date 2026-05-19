from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from govkb.utils.http import fetch_text


@dataclass
class ResolvedTarget:
    url: str
    portal_name: str = ""
    confidence: float = 0.0
    source: str = "manual"


class TargetResolver:
    """Resolve a city/government name into an official portal URL."""

    def __init__(self, llm_client: Any | None = None, *, timeout: int = 15):
        self.llm = llm_client
        self.timeout = timeout

    def resolve(self, target: str) -> ResolvedTarget:
        target = target.strip()
        if not target:
            raise ValueError("Target is empty")
        if self._looks_like_url(target):
            return self._verify_url(self._normalize_url(target), source="manual")
        if self.llm is None:
            raise RuntimeError("City resolution requires an LLM API. Please set provider, API key, and model, or enter a URL.")

        candidates = self._search_official_site_candidates(target)
        candidates.extend(self._ask_llm_for_candidates(target))
        errors: list[str] = []
        for candidate in candidates:
            try:
                return self._verify_url(candidate.url, source=candidate.source, city_hint=target)
            except Exception as exc:
                errors.append(f"{candidate.url}: {exc}")
        raise RuntimeError("Could not verify an official website candidate. " + "; ".join(errors[:3]))

    def _search_official_site_candidates(self, target: str) -> list[ResolvedTarget]:
        query = urllib.parse.quote(f"{target} 官网 政府门户")
        search_urls = [
            f"https://www.bing.com/search?q={query}",
            f"https://duckduckgo.com/html/?q={query}",
        ]
        candidates: list[ResolvedTarget] = []
        candidates.extend(self._seed_candidates(target))
        for search_url in search_urls:
            try:
                result = fetch_text(search_url, timeout=self.timeout, retries=0)
            except Exception:
                continue
            for url in self._extract_search_urls(result.text):
                if not self._is_likely_official(url, target):
                    continue
                candidates.append(ResolvedTarget(url=self._normalize_url(url), source="search", confidence=0.8))
            if candidates:
                break
        return self._dedupe_candidates(candidates)

    def _seed_candidates(self, target: str) -> list[ResolvedTarget]:
        seeds: list[ResolvedTarget] = []
        if any(word in target for word in ("滨江", "高新区")):
            seeds.append(ResolvedTarget(url="https://www.hhtz.gov.cn/", source="seed", confidence=0.95))
        return seeds

    def _ask_llm_for_candidates(self, target: str) -> list[ResolvedTarget]:
        prompt = f"""
你是中国政府门户网站识别助手。请根据用户输入推断最可能的官方政府门户网站。

要求：
1. 只返回JSON，不要Markdown。
2. 优先返回本级人民政府门户，不要返回政务服务网、新闻媒体、百科、地图、公众号。
3. URL应为官网首页，通常是 gov.cn 域名。
4. 如果不确定，也要给出1到3个候选，并按置信度排序。

用户输入：{target}

返回格式：
{{
  "candidates": [
    {{"url": "https://www.example.gov.cn", "portal_name": "某某市人民政府", "confidence": 0.8}}
  ]
}}
""".strip()
        response = self.llm.complete(prompt)
        data = self._parse_json(response)
        candidates = data.get("candidates") or []
        resolved: list[ResolvedTarget] = []
        for item in candidates:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            resolved.append(
                ResolvedTarget(
                    url=self._normalize_url(url),
                    portal_name=str(item.get("portal_name") or ""),
                    confidence=float(item.get("confidence") or 0.0),
                    source="llm",
                )
            )
        if not resolved:
            raise RuntimeError("LLM did not return any website candidate")
        return resolved

    def _extract_search_urls(self, html: str) -> list[str]:
        urls = re.findall(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", html)
        cleaned = []
        for url in urls:
            url = urllib.parse.unquote(url)
            match = re.search(r"https?://[^&\"'<>\s]+", url)
            if match:
                cleaned.append(match.group(0).rstrip(").,;"))
        return cleaned

    def _is_likely_official(self, url: str, target: str = "") -> bool:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if not host.endswith(".gov.cn"):
            return False
        if parsed.query and not parsed.path.endswith(("index.html", "/")):
            return False
        if any(bad in host for bad in ("gov.cn.e", "95ye", "bendibao", "miit.gov.cn", "samr.gov.cn", "mca.gov.cn", "beian.mps.gov.cn")):
            return False
        if target and any(word in target for word in ("滨江", "高新区")):
            return "hhtz.gov.cn" in host or "hangzhou.gov.cn" in host
        return True

    def _dedupe_candidates(self, candidates: list[ResolvedTarget]) -> list[ResolvedTarget]:
        seen = set()
        unique = []
        for candidate in candidates:
            host = urlparse(candidate.url).netloc.lower()
            if host in seen:
                continue
            seen.add(host)
            unique.append(candidate)
        return unique[:5]

    def _verify_url(self, url: str, *, source: str, city_hint: str = "") -> ResolvedTarget:
        try:
            result = fetch_text(url, timeout=self.timeout)
        except RuntimeError:
            parsed_url = urlparse(url)
            if parsed_url.scheme == "https":
                fallback_url = parsed_url._replace(scheme="http").geturl()
                try:
                    result = fetch_text(fallback_url, timeout=self.timeout)
                except RuntimeError:
                    result = fetch_text(fallback_url, timeout=self.timeout, follow_meta_refresh=False)
            else:
                result = fetch_text(url, timeout=self.timeout, follow_meta_refresh=False)
        title = self._extract_title(result.text)
        parsed = urlparse(result.url)
        if not parsed.netloc.endswith(".gov.cn"):
            raise RuntimeError(f"not a gov.cn domain: {parsed.netloc}")
        if parsed.netloc.lower() in {"www.gov.cn", "gov.cn"}:
            raise RuntimeError("central government portal is not a local official site")

        confidence = 0.7
        if title:
            confidence += 0.1
        short_hint = city_hint.replace("人民政府", "").replace("政府", "").replace("市", "").replace("区", "").strip()
        if short_hint and short_hint in title:
            confidence += 0.2
        if source != "seed" and short_hint and short_hint not in title and short_hint not in parsed.netloc:
            raise RuntimeError(f"candidate does not match target hint: {short_hint}")

        return ResolvedTarget(
            url=result.url,
            portal_name=title or parsed.netloc,
            confidence=min(confidence, 1.0),
            source=source,
        )

    def _parse_json(self, text: str) -> dict[str, Any]:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise ValueError("LLM response does not contain JSON")
        return json.loads(match.group(0))

    def _extract_title(self, html: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if not match:
            return ""
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group(1))).strip()

    def _looks_like_url(self, value: str) -> bool:
        return "." in value and not re.search(r"[\u4e00-\u9fff]", value)

    def _normalize_url(self, url: str) -> str:
        if not re.match(r"https?://", url, re.I):
            return "https://" + url
        return url
