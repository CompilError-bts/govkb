from dataclasses import dataclass


@dataclass
class SiteInfo:
    """LLM analysis result describing a government portal news section."""

    domain: str
    portal_name: str
    news_list_url: str
    article_link_pattern: str
    date_pattern: str
    title_selector: str
    pagination_pattern: str
    articles_per_page: int
    encoding: str = "utf-8"
    content_selector: str = ""
    news_section_name: str = ""

