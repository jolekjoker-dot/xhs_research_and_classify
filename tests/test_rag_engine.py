import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from src.kb_agent.rag_engine import keyword_search, hybrid_search
from src.kb_agent.searcher import search, format_results, _is_in_scope, PROJECT_ROOT
from src.kb_agent.indexer import _parse_md


class TestParseMd:
    def test_parses_frontmatter(self, tmp_path: Path):
        md = tmp_path / "test.md"
        md.write_text("""---
title: "Test Post"
category: "技术编程"
tags: [Python, AI]
---
# Test Post
## Body
Content here.""", encoding="utf-8")
        doc = _parse_md(md)
        assert doc["title"] == "Test Post"
        assert doc["category"] == "技术编程"
        assert len(doc["chunks"]) > 0

    def test_no_frontmatter(self, tmp_path: Path):
        md = tmp_path / "test.md"
        md.write_text("# Just a heading\nSome content.", encoding="utf-8")
        doc = _parse_md(md)
        assert doc["title"] == "test"


class TestScopeConstraint:
    def test_in_scope(self):
        assert _is_in_scope(str(PROJECT_ROOT / "output/knowledge_base/test.md"))

    def test_out_of_scope(self):
        assert not _is_in_scope("C:/windows/system32/test.md")


class TestFormatResults:
    def test_empty_results(self):
        output = format_results([], "test query")
        assert "未找到" in output

    def test_formatted_output(self):
        results = [{
            "title": "Test Post",
            "path": "output/kb/cat/test.md",
            "score": 0.85,
            "summary": "A test summary",
            "category": "技术编程",
            "method": "hybrid",
            "url": "http://x.com/test",
        }]
        output = format_results(results, "test")
        assert "Test Post" in output
        assert "85%" in output
        assert "[混合]" in output


class TestSearch:
    @patch("src.kb_agent.searcher.hybrid_search")
    def test_returns_results(self, mock_hybrid):
        mock_hybrid.return_value = [{
            "title": "Test",
            "path": str(PROJECT_ROOT / "output/knowledge_base/cat/test.md"),
            "score": 0.9,
            "method": "hybrid",
            "summary": "test summary",
        }]
        results = search("test query", top_k=5)
        assert len(results) >= 1
        assert results[0]["title"] == "Test"

    @patch("src.kb_agent.searcher.hybrid_search")
    def test_filters_out_of_scope(self, mock_hybrid):
        mock_hybrid.return_value = [{
            "title": "Bad",
            "path": "C:/windows/test.md",
            "score": 0.9,
            "method": "hybrid",
        }]
        results = search("test", top_k=5)
        assert len(results) == 0


class TestKeywordSearch:
    def test_no_files(self, tmp_path: Path):
        with patch("src.kb_agent.rag_engine.KB_DIR", tmp_path):
            results = keyword_search("test")
            assert results == []
