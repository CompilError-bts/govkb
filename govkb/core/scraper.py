from __future__ import annotations

import re
import time
import ast
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urlencode, urljoin

from govkb.models.article import Article
from govkb.models.site_info import SiteInfo
from govkb.utils.http import fetch_text


class Scraper:
    """Scrape article metadata from list pages and article bodies from detail pages."""

    DEFAULT_MAX_PAGES = 30

    def __init__(self, site: SiteInfo, *, rate_limit: float = 0.3, timeout: int = 15):
        self.site = site
        self.rate_limit = rate_limit
        self.timeout = timeout

    def scrape_list(self, start_date: str, *, max_pages: int = DEFAULT_MAX_PAGES) -> list[Article]:
        """
        Traverse news list pages and collect articles newer than start_date.

        Pagination is read from SiteInfo.pagination_pattern. Page 1 always uses
        SiteInfo.news_list_url because many government portals use a directory or
        index.html for the first page, then index_1.html for the second page.
        """

        start = self._parse_date(start_date)
        seen_urls: set[str] = set()
        articles: list[Article] = []
        stale_pages = 0

        for page_url in self._iter_page_urls(max_pages):
            result = fetch_text(page_url, timeout=self.timeout)
            dynamic_pages = self._scrape_jpaas_pages(result.text, result.url, start, max_pages)
            if dynamic_pages is not None:
                return dynamic_pages
            page_articles = self._extract_list_articles(result.text, result.url)
            if not page_articles:
                dynamic_pages = self._scrape_jpaas_pages(result.text, result.url, start, max_pages)
                if dynamic_pages is not None:
                    return dynamic_pages
            if not page_articles:
                break

            fresh_on_page = 0
            for article in page_articles:
                article_date = self._parse_date(article.date)
                if article.url in seen_urls:
                    continue
                seen_urls.add(article.url)
                if article_date and article_date >= start:
                    articles.append(article)
                    fresh_on_page += 1

            if fresh_on_page == 0:
                stale_pages += 1
            else:
                stale_pages = 0

            oldest = min((self._parse_date(item.date) for item in page_articles), default=None)
            if oldest and oldest < start and stale_pages >= 1:
                break
            if stale_pages >= 2:
                break
            time.sleep(self.rate_limit)

        return articles

    def scrape_content(self, article: Article) -> str:
        """
        Fetch and extract one article body.

        The extractor first tries blocks hinted by SiteInfo.content_selector, then
        falls back to the longest text-heavy HTML block.
        """

        result = fetch_text(article.url, timeout=self.timeout)
        body = self._extract_body(result.text)
        article.content = body
        return body

    def scrape_all(self, start_date: str, concurrency: int = 3, *, max_pages: int = DEFAULT_MAX_PAGES) -> list[Article]:
        """Scrape article metadata serially, then fetch article bodies concurrently."""

        articles = self.scrape_list(start_date, max_pages=max_pages)
        if not articles:
            return []

        workers = max(1, concurrency)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {executor.submit(self.scrape_content, article): article for article in articles}
            for future in as_completed(future_map):
                article = future_map[future]
                try:
                    future.result()
                except Exception as exc:
                    article.content = f"[正文抓取失败: {exc}]"
                time.sleep(self.rate_limit)

        return articles

    def _iter_page_urls(self, max_pages: int) -> Iterable[str]:
        yield self.site.news_list_url
        pattern = self.site.pagination_pattern
        if not pattern or "{n}" not in pattern:
            return
        for page_no in range(1, max_pages):
            url = pattern.format(n=page_no)
            if url == self.site.news_list_url:
                continue
            yield url

    def _extract_list_articles(self, html: str, base_url: str) -> list[Article]:
        compact_html = re.sub(r"\s+", " ", html)
        patterns = self._article_patterns()
        articles: list[Article] = []

        for pattern in patterns:
            try:
                regex = re.compile(pattern, re.I)
            except re.error:
                continue
            for match in regex.finditer(compact_html):
                article = self._article_from_match(match, base_url)
                if article:
                    articles.append(article)
            if articles:
                return articles
        return []

    def _scrape_jpaas_pages(self, html: str, base_url: str, start, max_pages: int) -> list[Article] | None:
        first_dynamic_html = self._load_jpaas_unit_html(html, base_url, page_no=1)
        if not first_dynamic_html:
            return None
        meta = self._parse_jpaas_meta(first_dynamic_html) or {"rows": max(1, self.site.articles_per_page or 15), "count": max_pages * max(1, self.site.articles_per_page or 15)}

        articles: list[Article] = []
        seen_urls: set[str] = set()
        total_pages = max(1, min(max_pages, (meta["count"] + meta["rows"] - 1) // meta["rows"]))
        stale_pages = 0
        for page_no in range(1, total_pages + 1):
            dynamic_html = first_dynamic_html if page_no == 1 else self._load_jpaas_unit_html(html, base_url, page_no=page_no)
            if not dynamic_html:
                break
            page_articles = self._extract_list_articles(dynamic_html, base_url)
            if not page_articles:
                break
            fresh_on_page = 0
            for article in page_articles:
                article_date = self._parse_date(article.date)
                if article.url in seen_urls:
                    continue
                seen_urls.add(article.url)
                if article_date and article_date >= start:
                    articles.append(article)
                    fresh_on_page += 1
            if fresh_on_page == 0:
                stale_pages += 1
            else:
                stale_pages = 0
            oldest = min((self._parse_date(item.date) for item in page_articles), default=None)
            if oldest and oldest < start and stale_pages >= 1:
                break
            if stale_pages >= 2:
                break
            time.sleep(self.rate_limit)
        return articles

    def _parse_jpaas_meta(self, html: str) -> dict[str, int] | None:
        match = re.search(r'<div class="pagination"[^>]*rows=["\'](?P<rows>\d+)["\'][^>]*count=["\'](?P<count>\d+)["\']', html, re.S)
        if not match:
            return None
        return {"rows": int(match.group("rows")), "count": int(match.group("count"))}

    def _load_jpaas_unit_html(self, html: str, base_url: str, *, page_no: int = 1) -> str:
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
        rows_match = re.search(r'rows=["\'](?P<rows>\d+)["\']', html)
        if page_no > 1 or rows_match:
            rows = int(rows_match.group("rows")) if rows_match else 15
            query["paramJson"] = json.dumps({"pageNo": page_no, "pageSize": rows}, ensure_ascii=False)
        api_url = urljoin(base_url, api_path) + "?" + urlencode(query)
        try:
            result = fetch_text(api_url, timeout=self.timeout, retries=0)
            data = json.loads(result.text)
            return str((data.get("data") or {}).get("html") or "")
        except Exception:
            return ""

    def _article_patterns(self) -> list[str]:
        return list(
            dict.fromkeys(
                [
                    self.site.article_link_pattern,
                    r'href=["\'](?P<url>\./\d{6}/t\d+_\d+\.html)["\'][^>]*>(?P<title>.*?)</a>\s*<span>\s*(?P<date>\d{4}-\d{2}-\d{2})\s*</span>',
                    r'<a\s+href=["\'](?P<url>/contents/\d+/\d+\.html)["\'][^>]*>(?P<title>.*?)</a>\s*(?P<date>\d{4}-\d{2}-\d{2})',
                    r'(?P<date>\d{4}-\d{2}-\d{2})[\s\S]{0,120}?<a\b[^>]*?(?:title=["\'](?P<title_attr>[^"\']+)["\'][^>]*?)?href=["\'](?P<url>[^"\']+\.s?html?)["\'][^>]*>(?P<title>.*?)</a>',
                    r'<a\b(?=[^>]*href=["\'](?P<url>[^"\']+\.s?html?)["\'])(?=[^>]*(?:title=["\'](?P<title_attr>[^"\']+)["\']))[^>]*>(?P<title>.*?)</a>[\s\S]{0,80}?(?P<date>\d{4}-\d{2}-\d{2})',
                    r'<a\b[^>]*href=["\'](?P<url>[^"\']+/art/(?P<year>\d{4})/\d{1,2}/\d{1,2}/[^"\']+\.html)["\'][^>]*>(?P<title>[\s\S]*?)<span>\s*(?P<month_day>\d{2}-\d{2})\s*</span>[\s\S]*?</a>',
                    r'href=["\'](?P<url>[^"\']+\.s?html?)["\'][^>]*>(?P<title>.*?)</a>[\s\S]{0,120}?(?P<date>\d{4}-\d{2}-\d{2})',
                ]
            )
        )

    def _article_from_match(self, match: re.Match[str], base_url: str) -> Article | None:
        data = match.groupdict()
        groups = match.groups()
        raw_url = data.get("url") or (groups[0] if len(groups) >= 1 else "")
        raw_title = data.get("title_attr") or data.get("title") or (groups[1] if len(groups) >= 2 else "")
        raw_date = data.get("date") or self._date_from_url_parts(data) or (groups[2] if len(groups) >= 3 else "")

        title = self._clean_text(raw_title)
        date_match = re.search(self.site.date_pattern or r"\d{4}-\d{2}-\d{2}", raw_date)
        if not raw_url or not title or not date_match:
            return None
        return Article(title=title, date=date_match.group(0), url=urljoin(base_url, raw_url))

    def _date_from_url_parts(self, data: dict[str, str]) -> str:
        year = data.get("year")
        month_day = data.get("month_day")
        if year and month_day:
            return f"{year}-{month_day}"
        return ""

    def _extract_body(self, html: str) -> str:
        html = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<!--.*?-->", " ", html)
        hinted_blocks = self._extract_hinted_blocks(html)
        if hinted_blocks:
            return max(hinted_blocks, key=len)

        candidates = _BlockTextExtractor.collect(html)
        if not candidates:
            return self._clean_text(html)
        return max(candidates, key=self._body_score)

    def _extract_hinted_blocks(self, html: str) -> list[str]:
        selectors = self.site.content_selector or ""
        tokens = []
        for selector in re.split(r"[,，]\s*", selectors):
            selector = selector.strip()
            if selector.startswith((".", "#")):
                tokens.append(re.escape(selector[1:]))
            elif selector and re.match(r"^[\w-]+$", selector):
                tokens.append(re.escape(selector))
        if not tokens:
            return []

        token_re = "|".join(tokens)
        block_re = re.compile(
            rf'<(?P<tag>div|article|section|main|td)\b[^>]*(?:class|id)=["\'][^"\']*(?:{token_re})[^"\']*["\'][^>]*>(?P<body>[\s\S]*?)</(?P=tag)>',
            re.I,
        )
        blocks = [self._clean_body(match.group("body")) for match in block_re.finditer(html)]
        return [block for block in blocks if len(block) >= 80]

    def _body_score(self, text: str) -> int:
        punctuation = sum(text.count(mark) for mark in "，。；：、")
        return len(text) + punctuation * 20

    def _clean_body(self, html: str) -> str:
        text = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</section>|</article>|</tr>", "\n", html)
        text = re.sub(r"<[^>]+>", " ", text)
        lines = [self._clean_text(line) for line in text.splitlines()]
        lines = [line for line in lines if line and not self._is_boilerplate(line)]
        return "\n".join(lines)

    def _clean_text(self, text: str) -> str:
        if ">" in text:
            text = text.rsplit(">", 1)[-1]
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r'"\s+target=["\']?_blank["\']?', "", text)
        return unescape(re.sub(r"\s+", " ", text)).strip()

    def _is_boilerplate(self, text: str) -> bool:
        return any(
            keyword in text
            for keyword in (
                "责任编辑",
                "分享到",
                "打印",
                "关闭",
                "上一篇",
                "下一篇",
                "扫一扫",
                "主办单位",
            )
        )

    def _parse_date(self, value: str) -> datetime | None:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d")
        except (TypeError, ValueError):
            return None


class _BlockTextExtractor(HTMLParser):
    CONTENT_TAGS = {"article", "section", "main", "div", "td"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._stack: list[dict[str, object]] = []

    @classmethod
    def collect(cls, html: str) -> list[str]:
        parser = cls()
        parser.feed(html)
        return [block for block in parser.blocks if len(block) >= 120]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self.CONTENT_TAGS:
            self._stack.append({"tag": tag.lower(), "parts": []})
        elif tag.lower() in {"p", "br"}:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        self._append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"p", "br"}:
            self._append("\n")
            return
        if not self._stack or self._stack[-1]["tag"] != tag:
            return
        block = self._stack.pop()
        text = self._normalize("".join(block["parts"]))  # type: ignore[arg-type]
        if text:
            self.blocks.append(text)
        self._append(text)

    def _append(self, data: str) -> None:
        if self._stack:
            parts = self._stack[-1]["parts"]
            assert isinstance(parts, list)
            parts.append(data)

    def _normalize(self, text: str) -> str:
        lines = [unescape(re.sub(r"\s+", " ", line)).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line)
