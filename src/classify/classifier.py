import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from openai import OpenAI

from src.config import get_config
from src.logger import get_logger
from src.models import ClassifiedPost, XHSPost

log = get_logger(__name__)

MIN_CONTENT_LENGTH = 100
DEFAULT_MAX_WORKERS = 5
CACHE_DIR = Path("output/cache/classify")


def _build_classification_prompt(post: XHSPost) -> str:
    config = get_config()
    categories_text = config.get_categories_text()
    template = config.load_classification_prompt()

    prompt = template.replace("{categories_text}", categories_text)

    content_preview = post.content[:3000] if post.content else ""
    prompt += f"\n\n## 帖子信息\n标题: {post.title}\n正文: {content_preview}\n标签: {', '.join(post.tags)}\n互动: 点赞{post.like_count} 收藏{post.collect_count} 评论{post.comment_count}\n\n请返回JSON:"
    return prompt


def _parse_classification_response(raw: str, post: XHSPost) -> ClassifiedPost:
    """parse LLM JSON response into ClassifiedPost, with fallback"""
    json_str = raw

    # strip markdown code fences if present
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if m:
        json_str = m.group(1).strip()

    try:
        json_start = json_str.find("{")
        json_end = json_str.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            data = json.loads(json_str[json_start:json_end])
        else:
            raise ValueError("No JSON found")
    except (json.JSONDecodeError, ValueError):
        log.warning("Failed to parse LLM JSON, raw preview: %s", raw[:200])
        return _fallback_classify(post, raw)

    return ClassifiedPost(
        post=post,
        category=data.get("category", "未分类"),
        sub_category=data.get("sub_category", ""),
        summary=data.get("summary", ""),
        keywords=data.get("keywords", []),
        entities=data.get("entities", []),
        sentiment=data.get("sentiment", "neutral"),
        quality_score=float(data.get("quality_score", 0)),
    )


def _fallback_classify(post: XHSPost, raw_hint: str = "") -> ClassifiedPost:
    """rule-based fallback when LLM fails"""
    content = post.content or ""
    length_score = min(len(content) / 500, 10)
    interaction_score = min(
        (post.like_count + post.collect_count * 2 + post.comment_count * 3) / 100, 10
    )
    quality_score = round((length_score * 0.6 + interaction_score * 0.4), 1)

    # simple keyword detection
    keywords = [tag for tag in post.tags[:5]]

    return ClassifiedPost(
        post=post,
        category="未分类",
        sub_category="",
        summary=content[:200] if content else "",
        keywords=keywords,
        entities=[],
        sentiment="neutral",
        quality_score=quality_score,
    )


def classify_post(
    post: XHSPost,
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> ClassifiedPost:
    """classify a single post using LLM, fall back to rules for short content"""
    config = get_config()

    # short content: skip LLM
    content_len = len(post.content or "")
    if content_len < MIN_CONTENT_LENGTH:
        log.info(
            "Post %s too short (%d chars), using rule-based classify",
            post.post_id,
            content_len,
        )
        return _fallback_classify(post)

    cfg = get_config()
    base_url = api_base or cfg.api_base_url
    key = api_key or cfg.api_key
    model_name = model or cfg.api_model

    if not key:
        log.error("No API key configured")
        return _fallback_classify(post)

    prompt = _build_classification_prompt(post)
    content_hash = hashlib.sha256(prompt.encode()).hexdigest()
    cache_file = CACHE_DIR / f"{content_hash}.json"

    if cache_file.exists() and key:
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            result = ClassifiedPost(
                post=post,
                category=cached.get("category", "未分类"),
                sub_category=cached.get("sub_category", ""),
                summary=cached.get("summary", ""),
                keywords=cached.get("keywords", []),
                entities=cached.get("entities", []),
                sentiment=cached.get("sentiment", "neutral"),
                quality_score=float(cached.get("quality_score", 0)),
            )
            log.info("Classify cache hit for %s → %s", post.post_id, result.category)
            return result
        except Exception:
            pass  # corrupt cache, re-fetch

    try:
        client = OpenAI(api_key=key, base_url=base_url)
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "system",
                    "content": "你是内容分类专家。只返回JSON，不要其他内容。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=2000,
        )
        msg = response.choices[0].message
        raw = msg.content or ""
        # If reasoning model consumed all tokens on thinking, grab reasoning content
        if not raw.strip():
            reasoning = getattr(msg, "reasoning_content", None) or ""
            if reasoning:
                raw = reasoning
                log.info("Using reasoning_content as fallback (%d chars)", len(reasoning))
        result = _parse_classification_response(raw, post)

        # write cache
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({
                "category": result.category,
                "sub_category": result.sub_category,
                "summary": result.summary,
                "keywords": result.keywords,
                "entities": result.entities,
                "sentiment": result.sentiment,
                "quality_score": result.quality_score,
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        log.info(
            "Classified %s → %s/%s (%.1f)",
            post.post_id,
            result.category,
            result.sub_category,
            result.quality_score,
        )
        return result

    except Exception:
        log.exception("LLM classify failed for %s, using fallback", post.post_id)
        return _fallback_classify(post)


def classify_posts(
    posts: list[XHSPost],
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> list[ClassifiedPost]:
    """classify multiple posts in parallel via ThreadPoolExecutor"""
    if not posts:
        return []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {executor.submit(classify_post, post): i for i, post in enumerate(posts)}
        results: list[Optional[ClassifiedPost]] = [None] * len(posts)
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception:
                log.exception("Classify worker failed for post[%d]", idx)
                results[idx] = _fallback_classify(posts[idx])

    log.info("Classify batch complete: %d posts", len(results))
    return results
