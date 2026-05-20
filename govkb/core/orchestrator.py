from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from govkb.core.analysis_runner import AnalysisRunner
from govkb.core.docx_generator import DocxGenerator
from govkb.core.scraper import Scraper
from govkb.llm.client import LLMClient
from govkb.utils.target_resolver import TargetResolver


class Orchestrator:
    """Coordinate the GovKB workflow from target URL to generated documents."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        llm_config = self.config.get("llm") or {}
        self.llm = LLMClient(llm_config)
        self.analyzer = AnalysisRunner(
            self.llm if self.llm.enabled else None,
            recipe_dir=str(self.config.get("recipe_dir") or "recipes"),
        )
        self.resolver = TargetResolver(self.llm if self.llm.enabled else None)

    def run(
        self,
        target: str | None = None,
        *,
        url: str | None = None,
        city: str | None = None,
        months: int = 3,
        max_pages: int | None = None,
    ) -> dict[str, Any]:
        """Execute analysis, scraping, and docx generation."""

        target_hint = target or city or url or ""
        resolved_url = self._resolve_target(target=target, url=url, city=city)
        site = self.analyzer.analyze(resolved_url, portal_name_hint="", target_hint=target_hint)

        scraper_config = self.config.get("scraper") or {}
        output_config = self.config.get("output") or {}
        start_date = self._start_date(months)

        scraper = Scraper(
            site,
            rate_limit=float(scraper_config.get("rate_limit") or 0.3),
            timeout=int(scraper_config.get("timeout") or 15),
        )
        concurrency = int(scraper_config.get("concurrency") or 3)
        page_limit = int(max_pages or scraper_config.get("max_pages") or Scraper.DEFAULT_MAX_PAGES)
        articles = scraper.scrape_all(start_date, concurrency=concurrency, max_pages=page_limit)

        output_dir = self._build_output_dir(site, start_date, output_config, target_hint)
        generator = DocxGenerator(
            site,
            filename_pattern=str(output_config.get("filename_pattern") or "{index:03d}_{date}_{title}.docx"),
        )
        success = generator.generate(articles, str(output_dir))

        return {
            "success": success,
            "fail": max(0, len(articles) - success),
            "article_count": len(articles),
            "output_dir": str(output_dir),
            "site": site,
            "analysis_trace": self.analyzer.trace,
            "start_date": start_date,
            "summary": f"{site.portal_name} {site.news_section_name}: {success}/{len(articles)} docx generated",
        }

    def _resolve_target(self, *, target: str | None, url: str | None, city: str | None) -> str:
        if url:
            return url
        if target and "." in target:
            return target
        if city or target:
            return self.resolver.resolve(city or target or "").url
        raise ValueError("A target URL is required.")

    def _start_date(self, months: int) -> str:
        days = max(1, months) * 30
        return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    def _build_output_dir(self, site: Any, start_date: str, output_config: dict[str, Any], target_hint: str = "") -> Path:
        base_dir = Path(str(output_config.get("dir") or "output"))
        region_name = self._region_name(target_hint) or self._region_name(getattr(site, "portal_name", ""))
        portal_name = getattr(site, "portal_name", "") or getattr(site, "domain", "") or "govkb"
        if region_name and region_name not in portal_name:
            display_name = f"{region_name}_{portal_name}"
        else:
            display_name = portal_name or region_name or "govkb"
        safe_name = self._safe_path_name(display_name)
        return base_dir / f"{safe_name}_新闻_{start_date}起"

    def _region_name(self, value: str) -> str:
        import re

        text = re.sub(r"https?://\S+|www\.\S+", "", value or "")
        match = re.search(r"([\u4e00-\u9fff]{2,20})(?:人民政府|政府|官网|门户|网站)?", text)
        if not match:
            return ""
        name = match.group(1)
        for suffix in ("人民政府", "政府", "官网", "门户", "网站"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        return name.strip()

    def _safe_path_name(self, value: str) -> str:
        import re

        value = re.sub(r'[\\/:*?"<>|]', "_", value)
        return value.strip(" .") or "govkb"
