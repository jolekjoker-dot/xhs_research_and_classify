import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config import get_config
from src.logger import get_logger
from src.models import ClassifiedPost

log = get_logger(__name__)

POST_TEMPLATE = """---
title: "{title}"
url: "{url}"
author: "{author}"
publish_time: "{publish_time}"
category: "{category}"
sub_category: "{sub_category}"
tags: [{tags}]
keywords: [{keywords}]
likes: {likes}
collects: {collects}
comments: {comments}
scraped_at: "{scraped_at}"
---

# {title}

## 摘要
{summary}

## 正文
{content}

{ocr_section}
## 图片
{images}

## 关键信息
- **实体**: {entities}
- **情感**: {sentiment}
- **质量评分**: {quality_score}/10

## 原文链接
[查看原文]({url})
"""

INDEX_TEMPLATE = """# 小红书知识库 — 总索引

> 最后更新: {updated_at}
> 帖子总数: {total_posts}
> 分类数量: {total_categories}

---

## 按分类浏览

{category_section}

---

## 按关键词索引

{keyword_section}

---

## 按时间索引（最近20篇）

{recent_section}
"""

CATEGORY_INDEX_TEMPLATE = """# {category_label}

> 帖子数量: {count}
> 最后更新: {updated_at}

## 子分类
{subcategories}

## 帖子列表

{post_list}
"""

KB_DIR = Path("output/knowledge_base")
CATEGORIES_DIR = KB_DIR / "categories"


def _slugify(text: str, max_len: int = 60) -> str:
    """generate a safe filename from text"""
    result = ""
    for ch in text:
        if ch.isalnum() or ch in "-_ ":
            result += ch
        else:
            result += "-"
    result = result.strip().replace(" ", "-")[:max_len]
    return result or "untitled"


def _ocr_is_redundant(content: str, ocr_text: str, threshold: float = 0.5) -> bool:
    """check if ocr_text lines already appear in content (after LLM merge)"""
    if not ocr_text:
        return True
    if not content:
        return False
    ocr_lines = [ln.strip() for ln in ocr_text.split("\n") if len(ln.strip()) >= 6]
    if not ocr_lines:
        return True
    matched = sum(1 for ln in ocr_lines if ln in content)
    return (matched / len(ocr_lines)) >= threshold


def _format_post_md(post: ClassifiedPost) -> str:
    """generate a single post markdown file content"""
    p = post.post
    publish_time = p.publish_time.isoformat() if p.publish_time else "未知"
    scraped_at = datetime.now().isoformat()

    # build image links section with correct relative paths
    if p.image_urls:
        image_lines = []
        for ipath in p.image_urls:
            fname = Path(ipath).name
            # convert to relative path from MD location:
            # MD: output/knowledge_base/categories/{cat}/xxx.md
            # IMG: output/knowledge_base/images/xxx.jpg
            # relative: ../../images/xxx.jpg
            rel_path = f"../../images/{fname}"
            # ensure forward slashes
            rel_path = rel_path.replace("\\", "/")
            image_lines.append(f"- ![{fname}]({rel_path})")
        images_section = "\n".join(image_lines)
    else:
        images_section = "（无图片）"

    return POST_TEMPLATE.format(
        title=p.title,
        url=p.url,
        author=p.author_name,
        publish_time=publish_time,
        category=post.category,
        sub_category=post.sub_category or "/",
        tags=", ".join(p.tags),
        keywords=", ".join(post.keywords),
        likes=p.like_count,
        collects=p.collect_count,
        comments=p.comment_count,
        scraped_at=scraped_at,
        summary=post.summary or "无摘要",
        content=p.content or "（无描述文字，见下方图片提取）",
        ocr_section=(
            f"## 图片提取文字\n{p.ocr_text}"
            if not _ocr_is_redundant(p.content or "", p.ocr_text or "")
            else ""
        ),
        entities=", ".join(post.entities) if post.entities else "无",
        sentiment=post.sentiment,
        quality_score=post.quality_score,
        images=images_section,
    )


def _group_by_category(posts: list[ClassifiedPost]) -> dict[str, list[ClassifiedPost]]:
    """group classified posts by their main category"""
    groups: dict[str, list[ClassifiedPost]] = {}
    for p in posts:
        cat = p.category or "未分类"
        groups.setdefault(cat, []).append(p)
    return groups


def build_knowledge_base(
    posts: list[ClassifiedPost],
    output_dir: Optional[Path] = None,
) -> Path:
    """build full knowledge base from classified posts"""
    root = output_dir or KB_DIR
    cats_dir = root / "categories"
    cats_dir.mkdir(parents=True, exist_ok=True)

    config = get_config()
    categories_config = config.load_categories()
    grouped = _group_by_category(posts)

    # Step 1: generate individual post .md files per category
    total_files = 0
    for category, cat_posts in grouped.items():
        cat_slug = _slugify(category)
        cat_dir = cats_dir / cat_slug
        cat_dir.mkdir(parents=True, exist_ok=True)

        for post in cat_posts:
            post_slug = _slugify(post.post.title) + f"_{post.post.post_id[:8]}"
            filepath = cat_dir / f"{post_slug}.md"
            filepath.write_text(_format_post_md(post), encoding="utf-8")
            total_files += 1

    # Step 2: generate _index.md per category
    for category, cat_posts in grouped.items():
        cat_slug = _slugify(category)
        cat_dir = cats_dir / cat_slug
        cat_config = categories_config.get(category, {})
        cat_label = cat_config.get("label", category)
        subcats = cat_config.get("subcategories", [])

        post_items = []
        for p in cat_posts:
            post_slug = _slugify(p.post.title) + f"_{p.post.post_id[:8]}"
            post_items.append(
                f"- [{p.post.title}]({post_slug}.md) — {p.summary[:80] if p.summary else '无摘要'}"
            )

        cat_index = CATEGORY_INDEX_TEMPLATE.format(
            category_label=cat_label,
            count=len(cat_posts),
            updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            subcategories="\n".join(f"- {s}" for s in subcats) if subcats else "无",
            post_list="\n".join(post_items),
        )
        (cat_dir / "_index.md").write_text(cat_index, encoding="utf-8")

    # Step 3: generate INDEX.md
    category_links = []
    for category in grouped:
        cat_slug = _slugify(category)
        cat_label = categories_config.get(category, {}).get("label", category)
        category_links.append(
            f"- [{cat_label}](categories/{cat_slug}/_index.md) ({len(grouped[category])} 篇)"
        )

    # keyword index
    all_keywords: dict[str, int] = {}
    for p in posts:
        for kw in p.keywords:
            all_keywords[kw] = all_keywords.get(kw, 0) + 1
    top_keywords = sorted(all_keywords.items(), key=lambda x: -x[1])[:30]
    keyword_section = "\n".join(f"- **{kw}** ({count})" for kw, count in top_keywords)

    # recent posts
    sorted_posts = sorted(posts, key=lambda p: p.quality_score, reverse=True)
    recent_items = []
    for p in sorted_posts[:20]:
        cat_slug = _slugify(p.category)
        post_slug = _slugify(p.post.title) + f"_{p.post.post_id[:8]}"
        recent_items.append(
            f"- [{p.post.title}](categories/{cat_slug}/{post_slug}.md) — ⭐{p.quality_score}"
        )

    index_content = INDEX_TEMPLATE.format(
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        total_posts=len(posts),
        total_categories=len(grouped),
        category_section="\n".join(category_links),
        keyword_section=keyword_section or "暂无",
        recent_section="\n".join(recent_items) if recent_items else "暂无",
    )
    (root / "INDEX.md").write_text(index_content, encoding="utf-8")

    # Step 4: metadata.json
    metadata = {
        "updated_at": datetime.now().isoformat(),
        "total_posts": len(posts),
        "total_files": total_files,
        "categories": {
            cat: {
                "label": categories_config.get(cat, {}).get("label", cat),
                "count": len(cat_posts),
            }
            for cat, cat_posts in grouped.items()
        },
        "top_keywords": top_keywords[:20],
        "quality_stats": {
            "avg": round(
                sum(p.quality_score for p in posts) / max(len(posts), 1), 1
            ),
            "max": max((p.quality_score for p in posts), default=0),
            "min": min((p.quality_score for p in posts), default=0),
        },
        "sentiment_stats": {
            "positive": sum(1 for p in posts if p.sentiment == "positive"),
            "neutral": sum(1 for p in posts if p.sentiment == "neutral"),
            "negative": sum(1 for p in posts if p.sentiment == "negative"),
        },
    }
    (root / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log.info(
        "Knowledge base built: %d posts in %d categories → %s",
        len(posts),
        len(grouped),
        root,
    )
    return root
