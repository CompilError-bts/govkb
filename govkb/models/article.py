from dataclasses import dataclass


@dataclass
class Article:
    """Article metadata and extracted content."""

    title: str
    date: str
    url: str
    content: str = ""

