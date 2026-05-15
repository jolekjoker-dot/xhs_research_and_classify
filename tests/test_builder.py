import json
import os
from pathlib import Path

import pytest

from src.knowledge_base.builder import (
    _slugify,
    _format_post_md,
    _group_by_category,
    build_knowledge_base,
    KB_DIR,
)
from src.models import ClassifiedPost, XHSPost


def make_cpost(
    post_id="p1",
    title="Test",
    content="Test content",
    author="Author",
    category="技术编程",
    sub_category="Python",
    summary="A summary",
    keywords=None,
    quality_score=7.0,
) -> ClassifiedPost:
    post = XHSPost(
        url=f"http://x.com/{post_id}",
        post_id=post_id,
        title=title,
        content=content,
        author_name=author,
        like_count=10,
        tags=keywords or [],
    )
    return ClassifiedPost(
        post=post,
        category=category,
        sub_category=sub_category,
        summary=summary,
        keywords=keywords or [],
        quality_score=quality_score,
    )


class TestSlugify:
    def test_english(self):
        assert "Hello-World" == _slugify("Hello World")

    def test_chinese(self):
        result = _slugify("Python协程实战")
        assert len(result) > 0

    def test_empty(self):
        assert _slugify("") == "untitled"

    def test_max_len(self):
        long_text = "a" * 100
        assert len(_slugify(long_text)) <= 60


class TestFormatPostMd:
    def test_includes_frontmatter(self):
        cp = make_cpost()
        md = _format_post_md(cp)
        assert "---" in md
        assert "title:" in md
        assert "url:" in md
        assert "category:" in md

    def test_includes_content(self):
        cp = make_cpost(content="Some detailed content here")
        md = _format_post_md(cp)
        assert "Some detailed content here" in md

    def test_includes_summary(self):
        cp = make_cpost(summary="Test summary text")
        md = _format_post_md(cp)
        assert "Test summary text" in md


class TestGroupByCategory:
    def test_groups_posts(self):
        posts = [
            make_cpost(post_id="1", category="技术编程"),
            make_cpost(post_id="2", category="产品设计"),
            make_cpost(post_id="3", category="技术编程"),
        ]
        groups = _group_by_category(posts)
        assert len(groups["技术编程"]) == 2
        assert len(groups["产品设计"]) == 1

    def test_unknown_category(self):
        posts = [make_cpost(category="")]
        groups = _group_by_category(posts)
        assert "未分类" in groups


class TestBuildKnowledgeBase:
    def test_creates_all_files(self, tmp_path: Path):
        posts = [
            make_cpost(post_id="1", title="Post One", category="技术编程"),
            make_cpost(post_id="2", title="Post Two", category="产品设计"),
        ]
        root = build_knowledge_base(posts, output_dir=tmp_path)

        assert (root / "INDEX.md").exists()
        assert (root / "metadata.json").exists()

        cats_dir = root / "categories"
        assert (cats_dir / "技术编程" / "_index.md").exists()
        assert (cats_dir / "产品设计" / "_index.md").exists()

    def test_generates_post_files(self, tmp_path: Path):
        posts = [make_cpost(post_id="abc12345", title="Hello World", category="技术编程")]
        root = build_knowledge_base(posts, output_dir=tmp_path)

        post_dir = root / "categories" / "技术编程"
        md_files = list(post_dir.glob("*.md"))
        non_index = [f for f in md_files if f.name != "_index.md"]
        assert len(non_index) == 1

    def test_metadata_json(self, tmp_path: Path):
        posts = [make_cpost(post_id="1"), make_cpost(post_id="2")]
        root = build_knowledge_base(posts, output_dir=tmp_path)

        meta = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        assert meta["total_posts"] == 2
        assert meta["total_files"] == 2
        assert "quality_stats" in meta
        assert "sentiment_stats" in meta

    def test_empty_posts(self, tmp_path: Path):
        root = build_knowledge_base([], output_dir=tmp_path)
        assert (root / "INDEX.md").exists()
        meta = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        assert meta["total_posts"] == 0
