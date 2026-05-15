import json
from unittest.mock import MagicMock, patch

import pytest

from src.classify.classifier import (
    _build_classification_prompt,
    _parse_classification_response,
    _fallback_classify,
    classify_post,
    classify_posts,
    MIN_CONTENT_LENGTH,
)
from src.models import XHSPost, ClassifiedPost


def make_post(
    post_id: str = "abc123",
    title: str = "Test Title",
    content: str = "A" * 200,
    like_count: int = 100,
    collect_count: int = 50,
    comment_count: int = 10,
    tags: list | None = None,
) -> XHSPost:
    return XHSPost(
        url=f"http://x.com/{post_id}",
        post_id=post_id,
        title=title,
        content=content,
        author_name="TestAuthor",
        like_count=like_count,
        collect_count=collect_count,
        comment_count=comment_count,
        tags=tags or ["Python", "Tutorial"],
    )


class TestBuildPrompt:
    def test_includes_categories_and_title(self):
        post = make_post(title="Python 性能优化", content="详细介绍Python性能优化技巧")
        prompt = _build_classification_prompt(post)
        assert "Python 性能优化" in prompt
        assert "技术编程" in prompt

    def test_truncates_long_content(self):
        post = make_post(content="A" * 5000)
        prompt = _build_classification_prompt(post)
        assert len("A" * 3000) < len(post.content)


class TestParseClassificationResponse:
    def test_valid_json(self):
        post = make_post()
        raw = json.dumps({
            "category": "技术编程",
            "sub_category": "Python",
            "summary": "一篇关于Python的教程",
            "keywords": ["Python", "编程"],
            "entities": ["Python"],
            "sentiment": "positive",
            "quality_score": 8.5,
        })
        result = _parse_classification_response(raw, post)
        assert result.category == "技术编程"
        assert result.sub_category == "Python"
        assert result.summary == "一篇关于Python的教程"
        assert "Python" in result.keywords
        assert result.sentiment == "positive"
        assert result.quality_score == 8.5

    def test_json_wrapped_in_text(self):
        post = make_post()
        raw = 'Here is the classification:\n```json\n{"category": "工具资源", "sub_category": "效率工具", "summary": "工具推荐", "keywords": ["工具"], "entities": [], "sentiment": "neutral", "quality_score": 6}\n```'
        result = _parse_classification_response(raw, post)
        assert result.category == "工具资源"
        assert result.quality_score == 6

    def test_invalid_json_falls_back(self):
        post = make_post()
        raw = "not json at all"
        result = _parse_classification_response(raw, post)
        assert result.category == "未分类"


class TestFallbackClassify:
    def test_uses_tags_as_keywords(self):
        post = make_post(tags=["Python", "AI"])
        result = _fallback_classify(post)
        assert "Python" in result.keywords

    def test_score_based_on_content_length(self):
        short = make_post(content="x" * 100)
        long = make_post(content="x" * 2000)
        short_result = _fallback_classify(short)
        long_result = _fallback_classify(long)
        assert long_result.quality_score > short_result.quality_score

    def test_score_includes_interactions(self):
        low = make_post(like_count=0, collect_count=0, comment_count=0)
        high = make_post(like_count=1000, collect_count=500, comment_count=200)
        assert _fallback_classify(high).quality_score > _fallback_classify(low).quality_score


class TestClassifyPost:
    def test_short_content_skips_llm(self):
        post = make_post(content="short")
        result = classify_post(post)
        assert result.category == "未分类"

    @patch("src.classify.classifier.OpenAI")
    def test_calls_llm_for_long_content(self, mock_openai_cls):
        post = make_post(content="A" * MIN_CONTENT_LENGTH)
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "category": "技术编程",
            "sub_category": "Python",
            "summary": "测试摘要",
            "keywords": ["test"],
            "entities": [],
            "sentiment": "neutral",
            "quality_score": 7.0,
        })
        mock_client.chat.completions.create.return_value.choices = [mock_choice]
        mock_openai_cls.return_value = mock_client

        result = classify_post(post, api_key="test-key")
        assert result.category == "技术编程"

    @patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": ""}, clear=False)
    def test_no_api_key_falls_back(self):
        post = make_post(content="A" * MIN_CONTENT_LENGTH)
        result = classify_post(post, api_key="")
        assert result.category == "未分类"


class TestClassifyPosts:
    @patch("src.classify.classifier.classify_post")
    def test_classifies_all(self, mock_classify):
        post1 = make_post("p1")
        post2 = make_post("p2")
        mock_classify.side_effect = [
            ClassifiedPost(
                post=post1, category="技术编程", sub_category="Python",
                summary="s1", keywords=["k"], quality_score=8,
            ),
            ClassifiedPost(
                post=post2, category="产品设计", sub_category="UX/UI",
                summary="s2", keywords=["d"], quality_score=7,
            ),
        ]
        results = classify_posts([post1, post2])
        assert len(results) == 2
        assert results[0].category == "技术编程"
        assert results[1].category == "产品设计"
