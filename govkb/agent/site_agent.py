from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any
from urllib.parse import urlparse

from govkb.agent.schemas import ListExtractionStrategy
from govkb.agent.tools import AgentTools
from govkb.models.site_info import SiteInfo


class SiteAgent:
    """Agentic government site analyzer with a controlled action loop."""

    ALLOWED_ACTIONS = {"inspect_page", "extract_links", "try_extract_list", "trace_article_context", "finish"}

    def __init__(self, llm_client: Any, *, tools: AgentTools | None = None, max_steps: int = 6, success_threshold: int = 8):
        self.llm = llm_client
        self.tools = tools or AgentTools()
        self.max_steps = max_steps
        self.success_threshold = success_threshold
        self.trace: list[str] = []

    def analyze(self, url: str, *, portal_name_hint: str = "", target_hint: str = "") -> SiteInfo:
        """
        Analyze a site through iterative tool use.

        The LLM selects actions, but all real effects are mediated by AgentTools.
        If the LLM gets stuck, deterministic candidate testing provides a fallback.
        """

        state: dict[str, Any] = {
            "target_url": url,
            "portal_name_hint": portal_name_hint,
            "target_hint": target_hint,
            "observations": [],
            "tested": [],
            "pagination_by_url": {},
        }

        first = self.tools.inspect_page(url)
        state["observations"].append({"action": "inspect_page", "result": self._jsonable(first)})
        self.trace.append(f"inspect_page {url}: links={first.link_count}, articles={len(first.article_candidates)}")

        fallback = self._try_ranked_candidates(url, first, portal_name_hint)
        if fallback:
            self.trace.append(f"fallback candidate succeeded: {fallback.news_section_name} {fallback.news_list_url}")
            return fallback

        for step in range(self.max_steps):
            action = self._ask_next_action(state)
            name = action.get("action")
            if name not in self.ALLOWED_ACTIONS:
                self.trace.append(f"step {step + 1}: invalid action {name}")
                continue

            self.trace.append(f"step {step + 1}: {name}")
            if name == "finish":
                site = self._site_from_finish(action, url, portal_name_hint)
                if site:
                    extracted = self.tools.try_extract_list(site.news_list_url)
                    if extracted.get("ok"):
                        site.articles_per_page = int(extracted.get("count") or 0)
                        return site
                state["observations"].append({"action": "finish", "error": "finish result could not be verified"})
                continue

            result = self._execute_action(action, url)
            state["observations"].append({"action": name, "args": action, "result": result})
            self._remember_pagination(state, result)

            finished = self._site_from_successful_result(result, url, portal_name_hint)
            if finished:
                return finished

        final_fallback = self._try_all_seen_candidates(url, state, portal_name_hint)
        if final_fallback:
            return final_fallback
        raise RuntimeError("SiteAgent could not find a verifiable news list. Trace: " + " | ".join(self.trace))

    def _ask_next_action(self, state: dict[str, Any]) -> dict[str, Any]:
        prompt = self._build_prompt(state)
        try:
            response = self.llm.complete(prompt)
            return self._parse_json(response)
        except Exception as exc:
            self.trace.append(f"LLM action failed: {exc}")
            return self._fallback_action(state)

    def _execute_action(self, action: dict[str, Any], base_url: str) -> dict[str, Any]:
        name = action["action"]
        url = action.get("url") or base_url
        if name == "inspect_page":
            return self._jsonable(self.tools.inspect_page(str(url)))
        if name == "extract_links":
            return self.tools.extract_links(str(url))
        if name == "try_extract_list":
            strategy = action.get("strategy") or {"type": "auto"}
            return self.tools.try_extract_list(str(url), strategy)
        if name == "trace_article_context":
            return self._jsonable(self.tools.trace_article_context(str(url)))
        return {"ok": False, "error": f"unsupported action: {name}"}

    def _try_ranked_candidates(self, original_url: str, inspection: Any, portal_name_hint: str) -> SiteInfo | None:
        if getattr(inspection, "article_candidates", None):
            result = self.tools.try_extract_list(inspection.final_url or original_url)
            if (
                result.get("ok")
                and int(result.get("count") or 0) >= self.success_threshold
                and not getattr(inspection, "news_link_candidates", None)
            ):
                return self._site_from_extract_result(result, original_url, portal_name_hint, "新闻")

        for link in getattr(inspection, "news_link_candidates", [])[:8]:
            if not self._same_site(original_url, link["href"]):
                self.trace.append(f"skip offsite candidate {link['text']} {link['href']}")
                continue
            if self._bad_section(link.get("text", "")):
                self.trace.append(f"skip low-quality candidate {link['text']} {link['href']}")
                continue
            result = self.tools.try_extract_list(link["href"])
            self.trace.append(f"try candidate {link['text']} {link['href']}: {result.get('count', 0)}")
            if result.get("ok") and int(result.get("count") or 0) >= self.success_threshold:
                return self._site_from_extract_result(result, original_url, portal_name_hint, link["text"])
            traced = self._try_article_backtrace(original_url, result, portal_name_hint)
            if traced:
                return traced
        return None

    def _try_article_backtrace(self, original_url: str, list_result: dict[str, Any], portal_name_hint: str) -> SiteInfo | None:
        for article in (list_result.get("articles") or [])[:3]:
            article_url = article.get("url")
            if not article_url:
                continue
            context = self.tools.trace_article_context(article_url)
            self.trace.append(f"trace article {article_url}: {len(context.column_candidates)} column candidates")
            for candidate in context.column_candidates[:6]:
                href = candidate.get("href", "")
                text = candidate.get("text", "新闻")
                if not href or not self._same_site(original_url, href) or self._bad_section(text):
                    continue
                result = self.tools.try_extract_list(href)
                self.trace.append(f"try backtrace candidate {text} {href}: {result.get('count', 0)}")
                if result.get("ok") and int(result.get("count") or 0) >= self.success_threshold:
                    return self._site_from_extract_result(result, original_url, portal_name_hint, text)
        return None

    def _try_all_seen_candidates(self, original_url: str, state: dict[str, Any], portal_name_hint: str) -> SiteInfo | None:
        seen: list[dict[str, str]] = []
        for observation in state.get("observations", []):
            result = observation.get("result") or {}
            candidates = result.get("news_link_candidates") or result.get("links") or []
            for item in candidates:
                if isinstance(item, dict) and item.get("href"):
                    seen.append({"href": item["href"], "text": item.get("text", "新闻")})
        for item in seen[:20]:
            if not self._same_site(original_url, item["href"]):
                continue
            if self._bad_section(item.get("text", "")):
                continue
            result = self.tools.try_extract_list(item["href"])
            if result.get("ok") and int(result.get("count") or 0) >= 3:
                return self._site_from_extract_result(result, original_url, portal_name_hint, item["text"])
        return None

    def _site_from_successful_result(self, result: dict[str, Any], original_url: str, portal_name_hint: str) -> SiteInfo | None:
        if result.get("url") and not self._same_site(original_url, str(result.get("url"))):
            return None
        if result.get("ok") and result.get("articles") and int(result.get("count") or 0) >= self.success_threshold:
            return self._site_from_extract_result(result, original_url, portal_name_hint, "新闻")
        return None

    def _same_site(self, original_url: str, candidate_url: str) -> bool:
        original = urlparse(original_url).netloc.lower()
        candidate = urlparse(candidate_url).netloc.lower()
        if not original or not candidate:
            return True
        return candidate == original or candidate.endswith("." + original) or original.endswith("." + candidate)

    def _bad_section(self, text: str) -> bool:
        return any(keyword in text for keyword in ("走进", "概况", "旅游", "招商", "数据", "营商", "专题", "公告", "政策", "解读", "时政"))

    def _site_from_extract_result(self, result: dict[str, Any], original_url: str, portal_name_hint: str, section_name: str) -> SiteInfo:
        list_url = str(result["url"])
        domain_url = original_url
        site = self.tools.make_site_info(
            domain_url=domain_url,
            portal_name=portal_name_hint or urlparse(domain_url).netloc,
            list_url=list_url,
            section_name=section_name,
        )
        site.articles_per_page = int(result.get("count") or 0)
        site.pagination_pattern = self._pagination_pattern_from_evidence(result.get("pagination_evidence") or [])
        return site

    def _site_from_finish(self, action: dict[str, Any], original_url: str, portal_name_hint: str) -> SiteInfo | None:
        list_url = action.get("news_list_url") or action.get("url")
        if not list_url:
            return None
        site = self.tools.make_site_info(
            domain_url=original_url,
            portal_name=str(action.get("portal_name") or portal_name_hint or urlparse(original_url).netloc),
            list_url=str(list_url),
            section_name=str(action.get("news_section_name") or "新闻"),
        )
        evidence = action.get("pagination_evidence") or []
        site.pagination_pattern = self._pagination_pattern_from_evidence(evidence)
        return site

    def _remember_pagination(self, state: dict[str, Any], result: dict[str, Any]) -> None:
        url = result.get("url")
        evidence = result.get("pagination_evidence")
        if url and evidence:
            state.setdefault("pagination_by_url", {})[url] = evidence

    def _pagination_pattern_from_evidence(self, evidence: list[dict[str, str]]) -> str:
        for item in evidence:
            if item.get("type") not in {"observed_link", "observed_script"}:
                continue
            url = item.get("url") or ""
            if re.search(r"index_\d+\.s?html?$", url):
                return re.sub(r"index_\d+(\.s?html?)$", r"index_{n}\1", url)
            if re.search(r"common_list_\d+\.shtml$", url):
                return re.sub(r"common_list_\d+\.shtml$", "common_list_{n}.shtml", url)
        return ""

    def _fallback_action(self, state: dict[str, Any]) -> dict[str, Any]:
        observations = state.get("observations", [])
        for observation in observations:
            result = observation.get("result") or {}
            for candidate in result.get("news_link_candidates", [])[:5]:
                href = candidate.get("href")
                if href and href not in state.get("tested", []):
                    state.setdefault("tested", []).append(href)
                    return {"action": "try_extract_list", "url": href, "strategy": {"type": "auto"}}
        return {"action": "try_extract_list", "url": state["target_url"], "strategy": {"type": "auto"}}

    def _build_prompt(self, state: dict[str, Any]) -> str:
        compact_state = self._trim_state(state)
        return f"""
你是 GovKB 的政府网站采集智能体。你必须通过受控工具逐步找到可验证的新闻列表页。

目标：
- 找到本级政府综合新闻栏目，例如“本地要闻”“政务要闻”“工作动态”“新闻动态”。
- 不要选公告、公示、政策、专题、互动、政务服务、外链媒体栏目。
- 成功标准：try_extract_list 至少得到3条包含标题和URL的新闻候选。

只能返回一个JSON对象，不能返回Markdown。允许的动作：
1. {{"action": "inspect_page", "url": "https://..."}}
2. {{"action": "extract_links", "url": "https://..."}}
3. {{"action": "try_extract_list", "url": "https://...", "strategy": {{"type": "auto"}}}}
4. {{"action": "trace_article_context", "url": "https://article-url..."}}
5. {{"action": "finish", "portal_name": "...", "news_section_name": "...", "news_list_url": "https://..."}}

当前状态：
{json.dumps(compact_state, ensure_ascii=False)}
""".strip()

    def _trim_state(self, state: dict[str, Any]) -> dict[str, Any]:
        trimmed = {
            "target_url": state.get("target_url"),
            "portal_name_hint": state.get("portal_name_hint"),
            "target_hint": state.get("target_hint"),
            "observations": [],
        }
        for observation in state.get("observations", [])[-4:]:
            result = observation.get("result")
            if isinstance(result, dict):
                result = {
                    key: result.get(key)
                    for key in (
                        "url",
                        "final_url",
                        "title",
                        "link_count",
                        "date_samples",
                        "news_link_candidates",
                        "count",
                        "articles",
                        "pagination_evidence",
                        "breadcrumb_links",
                        "column_candidates",
                        "error",
                    )
                    if key in result
                }
                if isinstance(result.get("news_link_candidates"), list):
                    result["news_link_candidates"] = result["news_link_candidates"][:8]
                if isinstance(result.get("articles"), list):
                    result["articles"] = result["articles"][:5]
            trimmed["observations"].append({"action": observation.get("action"), "result": result})
        return trimmed

    def _parse_json(self, text: str) -> dict[str, Any]:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise ValueError("LLM response does not contain JSON")
        return json.loads(match.group(0))

    def _jsonable(self, value: Any) -> Any:
        if hasattr(value, "__dataclass_fields__"):
            return asdict(value)
        if isinstance(value, list):
            return [self._jsonable(item) for item in value]
        if isinstance(value, dict):
            return {key: self._jsonable(item) for key, item in value.items()}
        return value
