"""Content formatter — use LLM to clean and structure scraped/OCR text"""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from openai import OpenAI

from src.logger import get_logger
from src.models import XHSPost

log = get_logger(__name__)
DEFAULT_MAX_WORKERS = 5
CACHE_DIR = Path("output/cache/format")

FORMAT_PROMPT = """你是一个内容整理专家。请对以下从小红书帖子抓取的内容进行格式化整理。

要求：
1. 修复OCR识别错误：合并被错误断开的行，修正明显的错别字（如"GlL"→"GIL"，"selfatention"→"self attention"）
2. 识别面试题格式：如果内容包含"1. 2. 3."或"Q: A:"结构，整理为清晰的问答列表
3. 添加适当的标题层级（## 一级标题, ### 二级标题）
4. 分离"正文"和"评论区"（如果有评论区内容放在最后）
5. 保持原始信息完整，不要添加原文中没有的内容

## 输入内容
{raw_content}

## 输出要求
只输出格式化后的 Markdown 内容，不要添加任何解释说明。"""


def format_content(
    post: XHSPost,
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> XHSPost:
    """format post content using LLM, returns modified post"""
    raw_content = post.content or ""
    ocr_text = post.ocr_text or ""

    # build combined content
    parts = []
    if raw_content.strip():
        parts.append(raw_content.strip())
    if ocr_text.strip():
        parts.append("【图片提取文字】\n" + ocr_text.strip())

    if not parts:
        return post  # nothing to format

    combined = "\n\n".join(parts)
    if len(combined) < 100:
        return post  # too short, format not needed

    # check cache
    content_hash = hashlib.sha256(combined.encode()).hexdigest()
    cache_file = CACHE_DIR / f"{content_hash}.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            post.content = cached["formatted"]
            log.info("Format cache hit for %s", post.post_id)
            return post
        except Exception:
            pass  # cache corrupt, re-fetch

    from src.config import get_config as _get_config
    cfg = _get_config()
    key = api_key or cfg.api_key
    url = api_base or cfg.api_base_url
    model_name = model or cfg.api_model

    if not key:
        log.warning("No API key, skipping format")
        return post

    prompt = FORMAT_PROMPT.format(raw_content=combined)

    try:
        client = OpenAI(api_key=key, base_url=url)
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是内容整理专家，只输出格式化后的Markdown。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
        )
        msg = response.choices[0].message
        formatted = msg.content or ""
        if not formatted.strip():
            reasoning = getattr(msg, "reasoning_content", None) or ""
            if reasoning:
                formatted = reasoning
        if formatted.strip():
            post.content = formatted.strip()
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps({"formatted": formatted.strip(), "original": content_hash}, ensure_ascii=False),
                encoding="utf-8",
            )
            log.info("Formatted: %d chars → %d chars", len(combined), len(formatted))
        return post

    except Exception:
        log.exception("Format failed")
        return post


def format_posts(posts: list[XHSPost], max_workers: int = DEFAULT_MAX_WORKERS) -> list[XHSPost]:
    """format multiple posts in parallel via ThreadPoolExecutor"""
    if not posts:
        return posts

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {executor.submit(format_content, post): i for i, post in enumerate(posts)}
        results = [None] * len(posts)
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception:
                log.exception("Format worker failed for post[%d]", idx)
                results[idx] = posts[idx]
                # Preserve original post on failure

    return results
