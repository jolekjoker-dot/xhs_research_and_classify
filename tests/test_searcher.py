import pytest
from unittest.mock import MagicMock, patch

from src.search.searcher import (
    _build_search_url,
    _parse_note_card,
    _detect_block,
    XHSSearchError,
    XHSRateLimitError,
    XHSLoginRequiredError,
    search_batch,
)


class TestBuildSearchUrl:
    def test_simple_keyword(self):
        url = _build_search_url("Python")
        assert "search_result" in url
        assert "%E6%90%9C%E7%B4%A2" not in url
        assert "keyword=Python" in url or "keyword=python" in url.lower()
        assert "sort=general" in url

    def test_chinese_keyword(self):
        url = _build_search_url("机器学习")
        assert "search_result" in url
        assert "sort=general" in url

    def test_with_sort(self):
        url = _build_search_url("test", sort="time_descending")
        assert "sort=time_descending" in url


class TestParseNoteCard:
    def test_valid_card(self):
        card = MagicMock()
        link = MagicMock()
        link.get_attribute.return_value = "/explore/abc123"
        card.query_selector.return_value = link

        title_el = MagicMock()
        title_el.inner_text.return_value = "Test Title"
        card.query_selector.side_effect = lambda sel: {
            "a[href*='/explore/']": link,
            "a[href*='/discovery/item/']": None,
            ".title": title_el,
            "[class*='title']": title_el,
        }.get(sel)

        result = _parse_note_card(card)
        assert result is not None
        assert "explore" in result.url

    def test_no_link_returns_none(self):
        card = MagicMock()
        card.query_selector.return_value = None
        assert _parse_note_card(card) is None

    def test_empty_href_returns_none(self):
        card = MagicMock()
        link = MagicMock()
        link.get_attribute.return_value = ""
        card.query_selector.return_value = link
        assert _parse_note_card(card) is None


class TestDetectBlock:
    def test_captcha_detection(self):
        page = MagicMock()
        page.content.return_value = "请完成滑块验证"
        assert _detect_block(page) == "captcha_detected"

    def test_rate_limit_detection(self):
        page = MagicMock()
        page.content.return_value = "访问频繁，请稍后再试"
        assert _detect_block(page) == "rate_limited"

    def test_login_required(self):
        page = MagicMock()
        page.title.return_value = "登录"
        assert _detect_block(page) == "login_required"

    def test_no_block(self):
        page = MagicMock()
        page.content.return_value = "<html>normal content</html>"
        page.title.return_value = "小红书"
        assert _detect_block(page) is None


class TestErrors:
    def test_xhs_search_error(self):
        with pytest.raises(XHSSearchError):
            raise XHSSearchError("test")

    def test_rate_limit_inherits(self):
        with pytest.raises(XHSSearchError):
            raise XHSRateLimitError("test")

    def test_login_required_inherits(self):
        with pytest.raises(XHSSearchError):
            raise XHSLoginRequiredError("test")


class TestSearchBatch:
    @patch("src.search.searcher.search")
    def test_batch_returns_dict(self, mock_search):
        from src.models import SearchResult
        mock_search.return_value = [
            SearchResult(url="http://x.com/1", post_id="1", title="T", author_name="A")
        ]
        results = search_batch(["kw1", "kw2"], count_per=2, headless=True)
        assert len(results) == 2
        assert "kw1" in results
        assert len(results["kw1"]) == 1

    @patch("src.search.searcher.search")
    def test_batch_error_recovery(self, mock_search):
        mock_search.side_effect = [XHSSearchError("fail"), []]
        results = search_batch(["kw1", "kw2"], count_per=2, headless=True)
        assert results["kw1"] == []
        assert results["kw2"] == []