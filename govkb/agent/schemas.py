from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


StrategyType = Literal["auto", "css_hint", "regex"]


@dataclass
class ListExtractionStrategy:
    """A controlled strategy for extracting article rows from a list page."""

    type: StrategyType = "auto"
    item_selector: str = ""
    title_selector: str = ""
    url_selector: str = ""
    date_selector: str = ""
    regex: str = ""
    date_strategy: str = ""


@dataclass
class ArticleCandidate:
    title: str
    url: str
    date: str = ""


@dataclass
class PageInspection:
    url: str
    final_url: str
    title: str
    status: int
    link_count: int
    date_samples: list[str] = field(default_factory=list)
    news_link_candidates: list[dict[str, str]] = field(default_factory=list)
    article_candidates: list[ArticleCandidate] = field(default_factory=list)
    pagination_evidence: list[dict[str, str]] = field(default_factory=list)
    html_excerpt: str = ""
    error: str = ""


@dataclass
class ArticleContext:
    url: str
    final_url: str
    title: str = ""
    breadcrumb_links: list[dict[str, str]] = field(default_factory=list)
    column_candidates: list[dict[str, str]] = field(default_factory=list)
    error: str = ""
