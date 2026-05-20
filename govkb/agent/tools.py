from __future__ import annotations

import re
import ast
import json
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlencode
from urllib.parse import urljoin, urlparse

from govkb.agent.schemas import ArticleCandidate, ArticleContext, ListExtractionStrategy, PageInspection
from govkb.core.site_analyzer import SiteAnalyzer, _LinkExtractor
from govkb.models.site_info import SiteInfo
from govkb.utils.http import fetch_text


class AgentTools:
    """Controlled tools that an LLM agent can use to inspect government sites."""

    NEWS_KEYWORDS = (
        "济南动态",
        "本地动态",
        "市内动态",
        "本地要闻",
        "政务动态",
        "工作动态",
        "新闻动态",
        "本地要闻",
        "政务要闻",
        "荆门要闻",
        "乌鲁木齐要闻",
        "咸阳新闻",
        "青岛要闻",
        "要闻",
        "新闻",
    )
    NEGATIVE_KEYWORDS = (
        "公告",
        "政策",
        "专题",
        "互动",
        "服务",
        "公开",
        "信箱",
        "图片",
        "视频",
        "走进",
        "概况",
        "旅游",
        "招商",
        "数据",
        "营商",
    )

    def __init__(self, *, timeout: int = 15):
        self.timeout = timeout
        self._analyzer = SiteAnalyzer(None, timeout=timeout)

    def fetch_url(self, url: str, *, follow_meta_refresh: bool = True) -> dict[str, object]:
        """Fetch a URL and return compact metadata plus an HTML excerpt."""

        try:
            result = fetch_text(url, timeout=self.timeout, retries=0, follow_meta_refresh=follow_meta_refresh)
            return {
                "ok": True,
                "url": url,
                "final_url": result.url,
                "status": result.status,
                "encoding": result.encoding,
                "title": self._extract_title(result.text),
                "html_excerpt": self._compact_html(result.text),
            }
        except Exception as exc:
            return {"ok": False, "url": url, "error": str(exc)}

    def extract_links(self, url: str) -> dict[str, object]:
        """Fetch a page and extract links with simple news relevance scoring."""

        try:
            result = fetch_text(url, timeout=self.timeout, retries=0)
            links = _LinkExtractor.collect(result.text, result.url)
            ranked = sorted(
                (
                    {"text": link["text"], "href": link["href"], "score": self._score_link(link)}
                    for link in links
                ),
                key=lambda item: item["score"],
                reverse=True,
            )
            return {"ok": True, "url": result.url, "links": ranked[:120]}
        except Exception as exc:
            return {"ok": False, "url": url, "error": str(exc)}

    def inspect_page(self, url: str) -> PageInspection:
        """Return facts useful for deciding whether a page is a news list."""

        try:
            result = fetch_text(url, timeout=self.timeout, retries=0)
            links = _LinkExtractor.collect(result.text, result.url)
            news_links = sorted(
                ({"text": link["text"], "href": link["href"], "score": self._score_link(link)} for link in links),
                key=lambda item: item["score"],
                reverse=True,
            )[:30]
            articles = self.try_extract_list(result.url, ListExtractionStrategy(type="auto")).get("articles", [])
            dynamic_hints = self._dynamic_hints(result.text)
            pagination_evidence = self.detect_pagination(result.text, result.url)
            return PageInspection(
                url=url,
                final_url=result.url,
                title=self._extract_title(result.text),
                status=result.status,
                link_count=len(links),
                date_samples=self._date_samples(result.text),
                news_link_candidates=news_links,
                article_candidates=[ArticleCandidate(**item) for item in articles[:20]],
                pagination_evidence=pagination_evidence,
                html_excerpt=self._compact_html(result.text),
                error="; ".join(dynamic_hints),
            )
        except Exception as exc:
            return PageInspection(url=url, final_url="", title="", status=0, link_count=0, error=str(exc))

    def try_extract_list(self, url: str, strategy: ListExtractionStrategy | dict[str, object] | None = None) -> dict[str, object]:
        """Try extracting article rows from a list page using a controlled strategy."""

        strategy = self._coerce_strategy(strategy)
        try:
            result = fetch_text(url, timeout=self.timeout, retries=0)
            pagination_html = result.text
            if strategy.type == "regex" and strategy.regex:
                articles = self._extract_by_regex(result.text, result.url, strategy.regex)
            else:
                articles = self._extract_auto(result.text, result.url)
                if not articles:
                    dynamic_html = self._load_jpaas_unit_html(result.text, result.url)
                    if dynamic_html:
                        pagination_html = dynamic_html
                        articles = self._extract_auto(dynamic_html, result.url)
            return {
                "ok": bool(articles),
                "url": result.url,
                "count": len(articles),
                "articles": [article.__dict__ for article in articles[:50]],
                "pagination_evidence": self.detect_pagination(pagination_html, result.url),
            }
        except Exception as exc:
            return {"ok": False, "url": url, "count": 0, "articles": [], "error": str(exc)}

    def trace_article_context(self, article_url: str) -> ArticleContext:
        """Open an article page and infer candidate list/column URLs from breadcrumbs."""

        try:
            result = fetch_text(article_url, timeout=self.timeout, retries=0)
            links = _LinkExtractor.collect(result.text, result.url)
            breadcrumb_links = self._extract_breadcrumb_links(result.text, result.url)
            candidates = self._rank_article_context_links(links + breadcrumb_links, result.url)
            return ArticleContext(
                url=article_url,
                final_url=result.url,
                title=self._extract_title(result.text),
                breadcrumb_links=breadcrumb_links[:12],
                column_candidates=candidates[:12],
            )
        except Exception as exc:
            return ArticleContext(url=article_url, final_url="", error=str(exc))

    def make_site_info(self, domain_url: str, portal_name: str, list_url: str, section_name: str) -> SiteInfo:
        parsed = urlparse(domain_url)
        return SiteInfo(
            domain=parsed.netloc,
            portal_name=portal_name or parsed.netloc,
            news_list_url=list_url,
            article_link_pattern=SiteAnalyzer.DEFAULT_ARTICLE_PATTERN,
            date_pattern=SiteAnalyzer.DEFAULT_DATE_PATTERN,
            title_selector="h1, .title, .article-title",
            pagination_pattern="",
            articles_per_page=0,
            encoding="utf-8",
            content_selector=".TRS_Editor, .article-content, .content, #zoom",
            news_section_name=section_name,
        )

    def _extract_auto(self, html: str, base_url: str) -> list[ArticleCandidate]:
        site = SiteInfo(
            domain=urlparse(base_url).netloc,
            portal_name="",
            news_list_url=base_url,
            article_link_pattern=SiteAnalyzer.DEFAULT_ARTICLE_PATTERN,
            date_pattern=SiteAnalyzer.DEFAULT_DATE_PATTERN,
            title_selector="",
            pagination_pattern="",
            articles_per_page=0,
            encoding="utf-8",
        )
        raw = self._analyzer._extract_articles(html, base_url, site.article_link_pattern)
        candidates = [ArticleCandidate(title=item["title"], url=item["url"], date=item["date"]) for item in raw]
        return [candidate for candidate in candidates if self._looks_like_article(candidate)]

    def _extract_by_regex(self, html: str, base_url: str, pattern: str) -> list[ArticleCandidate]:
        articles: list[ArticleCandidate] = []
        try:
            regex = re.compile(pattern, re.I | re.S)
        except re.error:
            return []
        for match in regex.finditer(html):
            data = match.groupdict()
            url = data.get("url") or ""
            title = self._clean_text(data.get("title") or data.get("title_attr") or "")
            date = data.get("date") or ""
            if url and title:
                candidate = ArticleCandidate(title=title, url=urljoin(base_url, url), date=date)
                if self._looks_like_article(candidate):
                    articles.append(candidate)
        return articles

    def _load_jpaas_unit_html(self, html: str, base_url: str) -> str:
        match = re.search(r'url="([^"]+)"[^>]*queryData="([^"]+)"', html, re.S)
        if not match:
            match = re.search(r"url='([^']+)'[^>]*queryData='([^']+)'", html, re.S)
        if not match:
            return ""
        api_path, raw_query = match.groups()
        try:
            query = ast.literal_eval(unescape(raw_query))
        except (SyntaxError, ValueError):
            return ""
        api_url = urljoin(base_url, api_path) + "?" + urlencode(query)
        try:
            result = fetch_text(api_url, timeout=self.timeout, retries=0)
            data = json.loads(result.text)
            return str((data.get("data") or {}).get("html") or "")
        except Exception:
            return ""

    def _looks_like_article(self, article: ArticleCandidate) -> bool:
        if not article.url or not article.title:
            return False
        if len(article.title) > 120:
            return False
        if re.search(r"/(?:col|node|list)/", article.url) and not re.search(r"/art/|/t\d+_", article.url):
            return False
        year_match = re.search(r"/(?:art/)?(\d{4})/", article.url)
        if article.date and year_match and not article.date.startswith(year_match.group(1)):
            return False
        return True

    def _coerce_strategy(self, strategy: ListExtractionStrategy | dict[str, object] | None) -> ListExtractionStrategy:
        if isinstance(strategy, ListExtractionStrategy):
            return strategy
        if isinstance(strategy, dict):
            allowed = {field: strategy.get(field, "") for field in ListExtractionStrategy.__dataclass_fields__}
            return ListExtractionStrategy(**allowed)
        return ListExtractionStrategy()

    def _score_link(self, link: dict[str, str]) -> int:
        text = link.get("text", "")
        href = link.get("href", "")
        score = 0
        for index, keyword in enumerate(self.NEWS_KEYWORDS):
            if keyword in text:
                score += 100 - index * 4
        for keyword in self.NEGATIVE_KEYWORDS:
            if keyword in text:
                score -= 50
        if re.search(r"/(?:col|xw|news|yw|zwyw|xyxw|gzyw|ywdt)/", href, re.I):
            score += 20
        if re.search(r"/art/\d{4}/", href):
            score -= 30
        if "时政" in text:
            score -= 70
        return score

    def _extract_breadcrumb_links(self, html: str, base_url: str) -> list[dict[str, str]]:
        snippets = []
        for pattern in (
            r"当前位置[\s\S]{0,800}",
            r"当前位置[:：][\s\S]{0,800}",
            r"您的位置[\s\S]{0,800}",
            r"location[\s\S]{0,800}",
            r"breadcrumb[\s\S]{0,800}",
            r"crumb[\s\S]{0,800}",
        ):
            match = re.search(pattern, html, re.I)
            if match:
                snippets.append(match.group(0))
        if not snippets:
            return []
        links: list[dict[str, str]] = []
        for snippet in snippets:
            links.extend(_LinkExtractor.collect(snippet, base_url))
        return links

    def _rank_article_context_links(self, links: list[dict[str, str]], article_url: str) -> list[dict[str, str]]:
        candidates = []
        for link in links:
            href = link.get("href", "")
            text = link.get("text", "")
            if not href or href == article_url:
                continue
            if not self._same_site(article_url, href):
                continue
            if re.search(r"/art/\d{4}/", href):
                continue
            score = self._score_link(link)
            if re.search(r"/col/col\d+/index\.html", href):
                score += 40
            if any(word in text for word in ("首页", "政府信息公开", "政务服务")):
                score -= 80
            if score > -20:
                candidates.append({"text": text, "href": href, "score": str(score)})
        return sorted(candidates, key=lambda item: int(item["score"]), reverse=True)

    def _same_site(self, url_a: str, url_b: str) -> bool:
        host_a = urlparse(url_a).netloc.lower()
        host_b = urlparse(url_b).netloc.lower()
        return not host_a or not host_b or host_a == host_b or host_a.endswith("." + host_b) or host_b.endswith("." + host_a)

    def _dynamic_hints(self, html: str) -> list[str]:
        hints = []
        if "unitbuild.js" in html and "/api-gateway/jpaas-publish-server/front/page/build/unit" in html:
            hints.append("dynamic:jpaas_unitbuild")
        if "queryData=" in html:
            hints.append("dynamic:queryData")
        return hints

    def detect_pagination(self, html: str, base_url: str) -> list[dict[str, str]]:
        evidence: list[dict[str, str]] = []
        for href in re.findall(r'href=["\']([^"\']*(?:index_\d+|common_list_\d+)[^"\']*\.s?html?)["\']', html, re.I):
            evidence.append({"type": "observed_link", "url": urljoin(base_url, href)})

        for match in re.finditer(
            r'createPage\(\s*(?P<count>\d+)\s*,\s*(?P<current>\d+)\s*,\s*["\'](?P<name>[^"\']+)["\']\s*,\s*["\'](?P<ext>s?html?)["\']\s*\)',
            html,
            re.I,
        ):
            if int(match.group("count")) <= 1:
                continue
            sample = f"{match.group('name')}_1.{match.group('ext')}"
            evidence.append(
                {
                    "type": "observed_script",
                    "url": urljoin(base_url, sample),
                    "page_count": match.group("count"),
                    "source": "createPage",
                }
            )

        jpaas = self._jpaas_pagination_evidence(html, base_url)
        if jpaas:
            evidence.append(jpaas)
        return evidence[:20]

    def _jpaas_pagination_evidence(self, html: str, base_url: str) -> dict[str, str] | None:
        match = re.search(
            r'<div class="pagination"[^>]*rows=["\'](?P<rows>\d+)["\'][^>]*count=["\'](?P<count>\d+)["\'][^>]*unitUrl=["\'](?P<unit_url>[^"\']+)["\']',
            html,
            re.S,
        )
        if not match:
            return None
        return {
            "type": "dynamic_api",
            "api": urljoin(base_url, match.group("unit_url")),
            "rows": match.group("rows"),
            "count": match.group("count"),
            "source": "jpaas_unitbuild",
        }

    def _date_samples(self, html: str) -> list[str]:
        samples = re.findall(r"\d{4}-\d{2}-\d{2}", html)
        samples.extend(re.findall(r">\s*\d{2}-\d{2}\s*<", html))
        return [sample.strip("<> ") for sample in samples[:20]]

    def _extract_title(self, html: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        return self._clean_text(match.group(1)) if match else ""

    def _compact_html(self, html: str, max_chars: int = 6000) -> str:
        html = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<!--.*?-->", " ", html)
        return re.sub(r"\s+", " ", html).strip()[:max_chars]

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"<[^>]+>", "", text)
        return unescape(re.sub(r"\s+", " ", text)).strip()

    def _guess_pagination(self, list_url: str) -> str:
        if re.search(r"index(?:_\d+)?\.html$", list_url):
            return re.sub(r"index(?:_\d+)?\.html$", "index_{n}.html", list_url)
        if re.search(r"common_list(?:_\d+)?\.shtml$", list_url):
            return re.sub(r"common_list(?:_\d+)?\.shtml$", "common_list_{n}.shtml", list_url)
        return ""
