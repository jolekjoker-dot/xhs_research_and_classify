from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SearchResult:
    """小红书搜索结果"""
    url: str
    post_id: str
    title: str
    author_name: str
    author_id: str = ""
    summary: str = ""
    cover_url: str = ""
    like_count: int = 0


@dataclass
class XHSPost:
    """小红书帖子完整内容"""
    url: str
    post_id: str
    title: str
    content: str
    author_name: str
    author_id: str = ""
    publish_time: Optional[datetime] = None
    like_count: int = 0
    collect_count: int = 0
    comment_count: int = 0
    tags: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    note_type: str = "normal"
    ocr_text: str = ""  # text extracted from images via OCR


@dataclass
class ClassifiedPost:
    """AI 分类后的帖子"""
    post: XHSPost
    category: str
    sub_category: str = ""
    summary: str = ""
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    sentiment: str = "neutral"
    quality_score: float = 0.0


@dataclass
class KnowledgeEntry:
    """知识库条目"""
    title: str
    content: str
    source_url: str
    source_author: str
    category: str
    sub_category: str = ""
    summary: str = ""
    keywords: list[str] = field(default_factory=list)
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class SearchQuery:
    """检索查询结果"""
    query: str
    results: list[dict] = field(default_factory=list)
    total: int = 0
    layer_used: str = "grep"
