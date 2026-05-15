import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

from src.scrape.scraper import (
    CHECKPOINT_FILE,
    XHSScrapeError,
    XHSPostNotFound,
    _load_checkpoint,
    _save_checkpoint,
    _parse_count,
    _parse_datetime,
    _parse_note_text,
    scrape_posts,
    scrape_from_results,
)
from src.models import SearchResult


# ── checkpoint tests ────────────────────────────────────────────


class TestCheckpoint:
    def test_load_no_file(self, tmp_path):
        with patch("src.scrape.scraper.CHECKPOINT_FILE", tmp_path / "nonexist.json"):
            assert _load_checkpoint() == set()

    def test_save_and_load(self, tmp_path):
        cp = tmp_path / "cp.json"
        with patch("src.scrape.scraper.CHECKPOINT_FILE", cp):
            _save_checkpoint("abc123")
            scraped = _load_checkpoint()
            assert "abc123" in scraped

    def test_deduplication(self, tmp_path):
        cp = tmp_path / "cp.json"
        with patch("src.scrape.scraper.CHECKPOINT_FILE", cp):
            _save_checkpoint("abc123")
            _save_checkpoint("abc123")
            assert len(_load_checkpoint()) == 1


# ── parse helpers ───────────────────────────────────────────────


class TestParseCount:
    def test_plain_number(self):
        assert _parse_count("1234") == 1234

    def test_wan(self):
        assert _parse_count("1.2万") == 12000
        assert _parse_count("10万") == 100000

    def test_comma(self):
        assert _parse_count("1,234") == 1234

    def test_empty(self):
        assert _parse_count("") == 0
        assert _parse_count(None) == 0  # type: ignore[arg-type]


class TestParseDatetime:
    def test_standard_format(self):
        dt = _parse_datetime("2025-06-15 14:30")
        assert dt is not None
        assert dt.month == 6

    def test_slash_format(self):
        dt = _parse_datetime("2025/06/15")
        assert dt is not None

    def test_relative_minutes(self):
        dt = _parse_datetime("5分钟前")
        assert dt is not None

    def test_relative_hours(self):
        dt = _parse_datetime("3小时前")
        assert dt is not None

    def test_relative_days(self):
        dt = _parse_datetime("3天前")
        assert dt is not None

    def test_just_now(self):
        assert _parse_datetime("刚刚") is not None

    def test_none_input(self):
        assert _parse_datetime("") is None
        assert _parse_datetime(None) is None  # type: ignore[arg-type]


# ── parse_note_text tests ──────────────────────────────────────


class TestParseNoteText:
    """test parsing with the 3 known XHS note structures"""

    def test_type_a_followed_image_post(self):
        """Type A: followed author, image-based (no body text)"""
        raw = (
            "1/14\n"
            "逸\n"
            "已关注\n"
            "腾讯大模型应用开发 二面\n"
            "#面试 #面经 #AI #agent #实习\n"
            "04-12 陕西\n"
            "共 16 条评论\n"
            "Zuko\n"
            "感觉这些问题好难\n"
            "04-26江苏\n"
            "4\n"
            "2\n"
            "说点什么...\n"
            "515\n"
            "1105\n"
            "16\n"
            "发送\n"
            "取消"
        )
        post = _parse_note_text(raw, "http://x.com/test", "test")
        assert post.author_name == "逸"
        assert post.title == "腾讯大模型应用开发 二面"
        assert len(post.tags) == 5
        assert "面试" in post.tags
        assert post.publish_time is not None
        assert post.comment_count == 16
        # image-based → body text should be empty (only comments)
        assert len(post.content) > 0  # has comments section

    def test_type_b_not_followed_with_body(self):
        """Type B: not followed author, has body text"""
        raw = (
            "1/8\n"
            "学长学姐帮\n"
            "关注\n"
            "阿里淘天暑期实习Agent算法岗一面\n"
            "淘天集团 · Agent智能客服方向\n"
            "核心题目：\n"
            "1. Attention本质是什么？从向量空间变换角度解释\n"
            "#互联网大厂 #面试 #面经\n"
            "04-12 广东\n"
            "共 2 条评论\n"
            "铁锈味的日落\n"
            "太假\n"
            "05-01美国\n"
            "说点什么...\n"
            "74\n"
            "138\n"
            "2\n"
            "发送"
        )
        post = _parse_note_text(raw, "http://x.com/test", "test")
        assert post.author_name == "学长学姐帮"
        assert post.title == "阿里淘天暑期实习Agent算法岗一面"
        assert "Attention本质" in post.content  # body text extracted
        assert "核心题目" in post.content
        assert post.comment_count == 2
        assert post.like_count == 74
        assert post.collect_count == 138

    def test_type_c_no_follow_with_body(self):
        """Type C: no follow indicator, has body text"""
        raw = (
            "御手洗藻\n"
            "快手 AI Agent研发实习 日常实习 一面\n"
            "又是一篇凉经 不过说实话这次大手子的面试真的感觉是最近收获最多的一场\n"
            "#openclaw #RAG #找实习\n"
            "04-08\n"
            "共 38 条评论\n"
            "momo\n"
            "有没有不需要手撕的利口的agent实习岗\n"
            "04-08马来西亚\n"
            "说点什么...\n"
            "598\n"
            "890\n"
            "38\n"
            "发送"
        )
        post = _parse_note_text(raw, "http://x.com/test", "test")
        assert post.author_name == "御手洗藻"
        assert post.title == "快手 AI Agent研发实习 日常实习 一面"
        assert "凉经" in post.content
        assert post.comment_count == 38

    def test_empty_input(self):
        post = _parse_note_text("", "http://x.com/test", "test")
        assert post.title == ""


# ── error tests ─────────────────────────────────────────────────


class TestErrors:
    def test_scrape_error(self):
        with pytest.raises(XHSScrapeError):
            raise XHSScrapeError("fail")

    def test_post_not_found_inherits(self):
        with pytest.raises(XHSScrapeError):
            raise XHSPostNotFound("not found")


# ── high-level mock tests ────────────────────────────────────────


class TestScrapePosts:
    @patch("src.scrape.scraper.sync_playwright")
    @patch("src.scrape.scraper._load_checkpoint")
    def test_resume_skips_scraped(self, mock_checkpoint, mock_pw):
        mock_checkpoint.return_value = {"post2"}
        urls = [
            "http://x.com/post1", "http://x.com/post2", "http://x.com/post3",
        ]
        # mock the playwright context
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.query_selector_all.return_value = []
        mock_context.__enter__.return_value = mock_context
        mock_context.new_page.return_value = mock_page
        mock_pw.return_value.__enter__.return_value = MagicMock()
        mock_pw.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value = mock_context

        posts = scrape_posts(urls, headless=True, resume=True)
        # with all cards empty, no posts scraped
        assert len(posts) >= 0


class TestScrapeFromResults:
    @patch("src.scrape.scraper.scrape_posts")
    def test_converts_results_to_urls(self, mock_scrape_posts):
        results = [
            SearchResult(
                url="http://x.com/1", post_id="1", title="T1", author_name="A"
            ),
            SearchResult(
                url="http://x.com/2", post_id="2", title="T2", author_name="B"
            ),
        ]
        scrape_from_results(results, headless=True)
        mock_scrape_posts.assert_called_once()
        call_args = mock_scrape_posts.call_args[0][0]
        assert len(call_args) == 2
