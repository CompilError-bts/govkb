from __future__ import annotations

import json
import re
from dataclasses import asdict
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from govkb.llm.prompts import build_site_analysis_prompt, build_site_analysis_repair_prompt
from govkb.models.site_info import SiteInfo
from govkb.utils.http import _find_meta_refresh_url, fetch_text


class SiteAnalyzer:
    """Use an LLM to analyze a government website and discover its news section."""

    NEWS_KEYWORDS = (
        "咸阳新闻",
        "本地要闻",
        "政务要闻",
        "今日要闻",
        "政府新闻",
        "工作动态",
        "新闻动态",
        "要闻动态",
        "综合新闻",
        "要闻",
        "新闻",
    )
    NEGATIVE_KEYWORDS = (
        "公告",
        "公示",
        "通知",
        "政策",
        "解读",
        "专题",
        "互动",
        "信箱",
        "公开",
        "招商",
        "视频",
        "图片",
    )
    DEFAULT_ARTICLE_PATTERN = r'href=["\'](?P<url>[^"\']*(?:\d{6}/t\d+_\d+|/\d{4,}/\d+|/art/\d+/\d+)[^"\']*\.s?html?)["\'][^>]*>(?P<title>.*?)</a>[\s\S]{0,120}?(?P<date>\d{4}-\d{2}-\d{2})'
    DEFAULT_DATE_PATTERN = r"\d{4}-\d{2}-\d{2}"

    def __init__(self, llm_client: Any | None = None, *, timeout: int = 15):
        self.llm = llm_client
        self.timeout = timeout

    def analyze(self, url: str) -> SiteInfo:
        """
        Analyze a government portal and return structured information for scraping.

        The LLM is the primary analyzer. A deterministic heuristic fallback is kept so
        CLI smoke tests can still identify common government news columns without API keys.
        """

        base_url = self._normalize_url(url)
        try:
            homepage = fetch_text(base_url, timeout=self.timeout)
        except RuntimeError:
            homepage = fetch_text(base_url, timeout=self.timeout, follow_meta_refresh=False)
        compact_html = self._compact_html(homepage.text, homepage.url)

        site = self._analyze_with_llm(compact_html)
        if site is None:
            site = self._analyze_with_heuristics(homepage.text, homepage.url, homepage.encoding)

        site = self._normalize_site(site, homepage.url, homepage.encoding)
        site = self._descend_to_specific_list(site)
        if self.verify(site):
            return site

        corrected = self._correct_list_url_from_homepage(site, homepage.text, homepage.url)
        if corrected and self.verify(corrected):
            return corrected

        repaired = self._repair_with_llm(site, compact_html, "新闻列表页验证失败，未能提取到有效文章。")
        if repaired is not None:
            repaired = self._normalize_site(repaired, homepage.url, homepage.encoding)
            repaired = self._descend_to_specific_list(repaired)
            if self.verify(repaired):
                return repaired

        improved = self._improve_from_list_page(site)
        if improved and self.verify(improved):
            return improved

        fallback = self._try_known_list_candidates(site, homepage.text, homepage.url)
        if fallback and self.verify(fallback):
            return fallback

        raise RuntimeError(f"Unable to verify news section for {base_url}: {asdict(site)}")

    def verify(self, site: SiteInfo) -> bool:
        """Verify analysis by fetching the news list page and extracting article-like rows."""

        try:
            result = fetch_text(site.news_list_url, timeout=self.timeout, retries=0)
        except RuntimeError:
            return False

        articles = self._extract_articles(result.text, site.news_list_url, site.article_link_pattern)
        if not articles:
            return False

        if site.articles_per_page <= 0:
            site.articles_per_page = len(articles)
        if not site.pagination_pattern:
            site.pagination_pattern = self._observe_pagination(site.news_list_url, result.text)
        if site.pagination_pattern and not self._verify_pagination_pattern(site.pagination_pattern):
            site.pagination_pattern = ""
        return True

    def _verify_pagination_pattern(self, pattern: str) -> bool:
        if "{n}" not in pattern:
            return False
        try:
            result = fetch_text(pattern.format(n=1), timeout=self.timeout, retries=0)
            return result.status == 200 and bool(self._extract_articles(result.text, result.url, self.DEFAULT_ARTICLE_PATTERN))
        except Exception:
            return False

    def _analyze_with_llm(self, compact_html: str) -> SiteInfo | None:
        if self.llm is None:
            return None

        prompt = build_site_analysis_prompt(compact_html)
        try:
            response = self._call_llm(prompt)
            data = self._parse_json(response)
            return self._site_from_llm_json(data)
        except Exception:
            return None

    def _repair_with_llm(self, site: SiteInfo, compact_html: str, error: str) -> SiteInfo | None:
        if self.llm is None:
            return None

        prompt = build_site_analysis_repair_prompt(error, json.dumps(asdict(site), ensure_ascii=False), compact_html)
        try:
            response = self._call_llm(prompt)
            data = self._parse_json(response)
            return self._site_from_llm_json(data)
        except Exception:
            return None

    def _call_llm(self, prompt: str) -> str:
        for method_name in ("complete", "generate", "chat"):
            method = getattr(self.llm, method_name, None)
            if callable(method):
                result = method(prompt)
                return self._coerce_llm_text(result)
        if callable(self.llm):
            return self._coerce_llm_text(self.llm(prompt))
        raise TypeError("llm_client must expose complete(), generate(), chat(), or be callable")

    def _coerce_llm_text(self, result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            for key in ("content", "text", "message"):
                if key in result:
                    return self._coerce_llm_text(result[key])
        if isinstance(result, list) and result:
            return self._coerce_llm_text(result[0])
        content = getattr(result, "content", None)
        if content is not None:
            return self._coerce_llm_text(content)
        text = getattr(result, "text", None)
        if text is not None:
            return str(text)
        return str(result)

    def _parse_json(self, text: str) -> dict[str, Any]:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise ValueError("LLM response does not contain a JSON object")
        return json.loads(match.group(0))

    def _site_from_llm_json(self, data: dict[str, Any]) -> SiteInfo:
        news = data.get("news_section") or {}
        article = data.get("article_page") or {}
        pagination = news.get("pagination") or {}
        return SiteInfo(
            domain="",
            portal_name=str(data.get("portal_name") or ""),
            news_list_url=str(news.get("list_url") or ""),
            article_link_pattern=str(news.get("article_link_pattern") or self.DEFAULT_ARTICLE_PATTERN),
            date_pattern=str(article.get("date_pattern") or self.DEFAULT_DATE_PATTERN),
            title_selector=str(article.get("title_selector") or ""),
            pagination_pattern=str(pagination.get("pattern") or ""),
            articles_per_page=int(news.get("articles_per_page") or 0),
            encoding=str(article.get("encoding") or "utf-8"),
            content_selector=str(article.get("content_location") or ""),
            news_section_name=str(news.get("name") or ""),
        )

    def _analyze_with_heuristics(self, html: str, base_url: str, encoding: str) -> SiteInfo:
        links = _LinkExtractor.collect(html, base_url)
        portal_name = self._guess_portal_name(html) or urlparse(base_url).netloc
        best = max(links, key=self._score_link, default={"href": base_url, "text": "新闻"})

        return SiteInfo(
            domain=urlparse(base_url).netloc,
            portal_name=portal_name,
            news_list_url=best["href"],
            article_link_pattern=self.DEFAULT_ARTICLE_PATTERN,
            date_pattern=self.DEFAULT_DATE_PATTERN,
            title_selector="h1, .title, .article-title",
            pagination_pattern="",
            articles_per_page=0,
            encoding=encoding,
            content_selector=".TRS_Editor, .article-content, .content, #zoom",
            news_section_name=best["text"],
        )

    def _normalize_site(self, site: SiteInfo, base_url: str, encoding: str) -> SiteInfo:
        parsed = urlparse(base_url)
        site.domain = site.domain or parsed.netloc
        site.portal_name = site.portal_name or parsed.netloc
        site.news_list_url = urljoin(base_url, site.news_list_url or base_url)
        site.encoding = site.encoding or encoding or "utf-8"
        site.date_pattern = site.date_pattern or self.DEFAULT_DATE_PATTERN
        site.article_link_pattern = site.article_link_pattern or self.DEFAULT_ARTICLE_PATTERN
        return site

    def _descend_to_specific_list(self, site: SiteInfo) -> SiteInfo:
        """If the selected URL is a channel page, prefer a concrete news list below it."""

        try:
            result = fetch_text(site.news_list_url, timeout=self.timeout, retries=0)
        except RuntimeError:
            return site

        current_articles = self._extract_articles(result.text, site.news_list_url, site.article_link_pattern)
        links = _LinkExtractor.collect(result.text, result.url)
        list_candidates = [link for link in links if self._looks_like_list_link(link, site.news_list_url)]
        if not list_candidates:
            return site

        best = max(list_candidates, key=self._score_link)
        best_score = self._score_link(best)
        current_score = self._score_link({"href": site.news_list_url, "text": site.news_section_name})

        # A channel page with many article links is usable, but a named child list is cleaner.
        should_descend = best_score > current_score or (
            current_articles and self._same_path_prefix(best["href"], site.news_list_url)
        )
        if should_descend:
            site.news_list_url = best["href"]
            site.news_section_name = best["text"]
        return site

    def _looks_like_list_link(self, link: dict[str, str], current_url: str) -> bool:
        href = link["href"]
        text = link["text"]
        if href == current_url:
            return False
        if re.search(r"/\d{6}/t\d+_\d+\.s?html?$", href):
            return False
        if href.endswith(".html") and not re.search(r"(?:index|list|channel|_\d+)\.html?$", href, re.I):
            return False
        if not self._same_path_prefix(href, current_url):
            return False
        return self._score_link({"href": href, "text": text}) > 0

    def _same_path_prefix(self, href: str, current_url: str) -> bool:
        href_parsed = urlparse(href)
        current_parsed = urlparse(current_url)
        if href_parsed.netloc != current_parsed.netloc:
            return False
        current_path = current_parsed.path.rstrip("/") + "/"
        return href_parsed.path.startswith(current_path)

    def _improve_from_list_page(self, site: SiteInfo) -> SiteInfo | None:
        try:
            result = fetch_text(site.news_list_url, timeout=self.timeout, retries=0)
        except RuntimeError:
            return None

        articles = self._extract_articles(result.text, site.news_list_url, self.DEFAULT_ARTICLE_PATTERN)
        if not articles:
            articles = self._extract_articles(result.text, site.news_list_url, self._xianyang_pattern())
        if not articles:
            return None

        site.article_link_pattern = self._xianyang_pattern()
        site.articles_per_page = len(articles)
        site.pagination_pattern = self._observe_pagination(site.news_list_url, result.text)
        return site

    def _try_known_list_candidates(self, site: SiteInfo, html: str, base_url: str) -> SiteInfo | None:
        candidates = self._known_list_candidates(base_url)
        refresh_url = _find_meta_refresh_url(html, base_url)
        if refresh_url:
            candidates.extend(self._known_list_candidates(refresh_url))

        for candidate in dict.fromkeys(candidates):
            trial = SiteInfo(
                domain=urlparse(candidate).netloc,
                portal_name=site.portal_name,
                news_list_url=candidate,
                article_link_pattern=site.article_link_pattern,
                date_pattern=site.date_pattern,
                title_selector=site.title_selector,
                pagination_pattern="",
                articles_per_page=0,
                encoding=site.encoding,
                content_selector=site.content_selector,
                news_section_name=site.news_section_name or "新闻",
            )
            if self.verify(trial):
                return trial
        return None

    def _known_list_candidates(self, base_url: str) -> list[str]:
        parsed = urlparse(base_url)
        host = parsed.netloc.lower()
        scheme = parsed.scheme or "https"
        origin = f"{scheme}://{parsed.netloc}"
        candidates: list[str] = []
        if "urumqi.gov.cn" in host or "wlmq.gov.cn" in host:
            for domain in ("www.urumqi.gov.cn", "www.wlmq.gov.cn"):
                for candidate_scheme in ("https", "http"):
                    candidates.append(f"{candidate_scheme}://{domain}/wlmqs/c119052/common_list.shtml")
                    candidates.append(f"{candidate_scheme}://{domain}/wlmqs/c119052/common_list_1.shtml")
        candidates.append(urljoin(origin + "/", "wlmqs/c119052/common_list.shtml"))
        return candidates

    def _extract_articles(self, html: str, base_url: str, pattern: str) -> list[dict[str, str]]:
        cleaned_html = re.sub(r"\s+", " ", html)
        patterns = [
            pattern,
            self._xianyang_pattern(),
            self._date_before_link_pattern(),
            self._date_after_link_pattern(),
            self._month_day_inside_link_pattern(),
            self.DEFAULT_ARTICLE_PATTERN,
        ]
        articles: list[dict[str, str]] = []

        for candidate in dict.fromkeys(patterns):
            try:
                regex = re.compile(candidate, re.I)
            except re.error:
                continue
            for match in regex.finditer(cleaned_html):
                data = match.groupdict()
                groups = match.groups()
                url = data.get("url") or (groups[0] if len(groups) >= 1 else "")
                title = data.get("title_attr") or data.get("title") or (groups[1] if len(groups) >= 2 else "")
                date = data.get("date") or self._date_from_url_parts(data) or (groups[2] if len(groups) >= 3 else "")
                title = self._clean_text(title)
                if url and title and re.match(self.DEFAULT_DATE_PATTERN, date):
                    articles.append({"url": urljoin(base_url, url), "title": title, "date": date})
            if articles:
                return articles
        return articles

    def _date_from_url_parts(self, data: dict[str, str]) -> str:
        year = data.get("year")
        month_day = data.get("month_day")
        if year and month_day:
            return f"{year}-{month_day}"
        return ""

    def _xianyang_pattern(self) -> str:
        return r'href=["\'](?P<url>\./\d{6}/t\d+_\d+\.html)["\'][^>]*>(?P<title>.*?)</a>\s*<span>\s*(?P<date>\d{4}-\d{2}-\d{2})\s*</span>'

    def _date_before_link_pattern(self) -> str:
        return r'(?P<date>\d{4}-\d{2}-\d{2})[\s\S]{0,120}?<a\b[^>]*?(?:title=["\'](?P<title_attr>[^"\']+)["\'][^>]*?)?href=["\'](?P<url>[^"\']+\.s?html?)["\'][^>]*>(?P<title>.*?)</a>'

    def _date_after_link_pattern(self) -> str:
        return r'<a\b(?=[^>]*href=["\'](?P<url>[^"\']+\.s?html?)["\'])(?=[^>]*(?:title=["\'](?P<title_attr>[^"\']+)["\']))[^>]*>(?P<title>.*?)</a>[\s\S]{0,80}?(?P<date>\d{4}-\d{2}-\d{2})'

    def _month_day_inside_link_pattern(self) -> str:
        return r'<a\b[^>]*href=["\'](?P<url>[^"\']+/art/(?P<year>\d{4})/\d{1,2}/\d{1,2}/[^"\']+\.html)["\'][^>]*>(?P<title>[\s\S]*?)<span>\s*(?P<month_day>\d{2}-\d{2})\s*</span>[\s\S]*?</a>'

    def _correct_list_url_from_homepage(self, site: SiteInfo, html: str, base_url: str) -> SiteInfo | None:
        links = _LinkExtractor.collect(html, base_url)
        labels = [site.news_section_name, "本地要闻", "荆门要闻", "政务要闻", "要闻"]
        candidates = [
            link
            for link in links
            if any(label and label in link["text"] for label in labels)
            and not re.search(r"/art/\d{4}/", link["href"])
        ]
        if not candidates:
            return None
        for link in sorted(candidates, key=self._score_link, reverse=True):
            trial = SiteInfo(**asdict(site))
            trial.news_list_url = link["href"]
            trial.news_section_name = link["text"]
            trial.pagination_pattern = self._guess_pagination(link["href"], "")
            if self.verify(trial):
                return trial
        return None

    def _guess_pagination(self, list_url: str, html: str) -> str:
        return self._observe_pagination(list_url, html)

    def _observe_pagination(self, list_url: str, html: str) -> str:
        if re.search(r"common_list_\d+\.shtml", html) or re.search(r"common_list(?:_\d+)?\.shtml$", list_url):
            return re.sub(r"common_list(?:_\d+)?\.shtml$", "common_list_{n}.shtml", list_url)
        if re.search(r"index_\d+\.html", html):
            return re.sub(r"(?:index(?:_\d+)?\.html)?$", "index_{n}.html", list_url.rstrip("/") + "/")
        return ""

    def _compact_html(self, html: str, base_url: str, max_chars: int = 24000) -> str:
        html = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<!--.*?-->", " ", html)
        title = self._guess_portal_name(html)
        links = _LinkExtractor.collect(html, base_url)
        link_lines = [f'<a href="{item["href"]}">{item["text"]}</a>' for item in links[:240] if item["text"]]
        compact = "\n".join(([f"<title>{title}</title>"] if title else []) + link_lines)
        compact = self._clean_text(compact)
        return compact[:max_chars]

    def _guess_portal_name(self, html: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if not match:
            return ""
        title = self._clean_text(match.group(1))
        return re.split(r"[-_|—]", title)[0].strip()

    def _score_link(self, link: dict[str, str]) -> int:
        text = link["text"]
        href = link["href"]
        score = 0
        for index, keyword in enumerate(self.NEWS_KEYWORDS):
            if keyword in text:
                score += 100 - index * 3
        for keyword in self.NEGATIVE_KEYWORDS:
            if keyword in text:
                score -= 60
        if re.search(r"/(?:xw|news|yw|zwyw|xyxw|gzyw)/", href, re.I):
            score += 25
        if re.search(r"/xyxw_14/?$", href):
            score += 40
        if any(keyword in text for keyword in ("国务院", "省政府", "省人民政府")):
            score -= 45
        if href.endswith((".gov.cn", ".gov.cn/")):
            score -= 20
        return score

    def _normalize_url(self, url: str) -> str:
        if not re.match(r"https?://", url, re.I):
            url = "https://" + url
        return url

    def _clean_text(self, text: str) -> str:
        if ">" in text:
            text = text.rsplit(">", 1)[-1]
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r'"\s+target=["\']?_blank["\']?', "", text)
        return unescape(re.sub(r"\s+", " ", text)).strip()


class _LinkExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    @classmethod
    def collect(cls, html: str, base_url: str) -> list[dict[str, str]]:
        parser = cls(base_url)
        parser.feed(html)
        seen: set[tuple[str, str]] = set()
        unique = []
        for item in parser.links:
            key = (item["href"], item["text"])
            if key not in seen:
                unique.append(item)
                seen.add(key)
        return unique

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = dict(attrs)
        href = attr_map.get("href")
        if href and not href.startswith(("javascript:", "#", "mailto:")):
            self._href = urljoin(self.base_url, href)
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return
        text = re.sub(r"\s+", " ", "".join(self._text_parts)).strip()
        if text:
            self.links.append({"href": self._href, "text": text})
        self._href = None
        self._text_parts = []
