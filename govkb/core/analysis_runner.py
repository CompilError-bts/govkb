from __future__ import annotations

from typing import Any

from govkb.agent.site_agent import SiteAgent
from govkb.agent.memory import RecipeMemory
from govkb.agent.tools import AgentTools
from govkb.core.site_analyzer import SiteAnalyzer
from govkb.models.site_info import SiteInfo


class AnalysisRunner:
    """Run agentic analysis first, then fall back to the legacy analyzer."""

    def __init__(self, llm_client: Any | None = None, *, timeout: int = 15, recipe_dir: str = "recipes"):
        self.llm = llm_client
        self.timeout = timeout
        self.memory = RecipeMemory(recipe_dir)
        self.trace: list[str] = []

    def analyze(self, url: str, *, portal_name_hint: str = "", target_hint: str = "") -> SiteInfo:
        cached = self.memory.load(url)
        if cached is not None:
            if self._bad_cached_section(cached.news_section_name):
                self.trace = [f"recipe:stale_bad_section {cached.news_section_name}"]
            else:
                verifier = SiteAnalyzer(None, timeout=self.timeout)
                if verifier.verify(cached):
                    self.trace = [f"recipe:hit {cached.domain}", "recipe:verified"]
                    return cached
                self.trace = [f"recipe:stale {cached.domain}"]

        if self.llm is not None:
            try:
                agent = SiteAgent(self.llm, tools=AgentTools(timeout=self.timeout), max_steps=6)
                site = agent.analyze(url, portal_name_hint=portal_name_hint, target_hint=target_hint)
                self.memory.save(site)
                self.trace = [*self.trace, "agent:start", *agent.trace, "agent:success", f"recipe:saved {site.domain}"]
                return site
            except Exception as exc:
                self.trace = [*self.trace, f"agent:failed {exc}", "legacy:start"]

        analyzer = SiteAnalyzer(self.llm, timeout=self.timeout)
        site = analyzer.analyze(url)
        self.memory.save(site)
        self.trace = [*self.trace, "legacy:success", f"recipe:saved {site.domain}"]
        return site

    def _bad_cached_section(self, name: str) -> bool:
        return any(keyword in name for keyword in ("走进", "概况", "旅游", "招商", "数据", "营商", "专题", "公告", "政策", "解读"))
