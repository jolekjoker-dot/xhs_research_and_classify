"""Content formatter — use LLM to clean and structure scraped/OCR text"""

import os
from typing import Optional

from openai import OpenAI

from src.logger import get_logger
from src.models import XHSPost

log = get_logger(__name__)

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

    key = api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    url = api_base or "https://api.deepseek.com/v1"
    model_name = model or "deepseek-chat"

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
            max_tokens=3000,
        )
        formatted = response.choices[0].message.content or ""
        if formatted.strip():
            post.content = formatted.strip()
            # Keep ocr_text as fallback — builder will deduplicate if content
            # already contains the OCR text.
            log.info("Formatted: %d chars → %d chars", len(combined), len(formatted))
        return post

    except Exception:
        log.exception("Format failed")
        return post


def format_posts(posts: list[XHSPost]) -> list[XHSPost]:
    """format multiple posts"""
    for i, post in enumerate(posts):
        log.info("[%d/%d] Formatting %s", i + 1, len(posts), post.post_id)
        format_content(post)
    return posts
