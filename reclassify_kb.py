"""Re-classify existing knowledge base posts using current LLM provider.

Reads all MD files from output/knowledge_base/, extracts content and
metadata, runs classify_posts, and rebuilds the knowledge base.
"""
import re
from pathlib import Path

from src.classify.classifier import classify_posts
from src.config import get_config
from src.knowledge_base.builder import build_knowledge_base
from src.models import ClassifiedPost, XHSPost

KB_DIR = Path("output/knowledge_base")


def main():
    cfg = get_config()
    print(f"Provider: {cfg.api_provider}")
    print(f"Model:    {cfg.api_model}")
    print(f"Key:      {cfg.api_key[:25]}..." if len(cfg.api_key) > 25 else "")

    posts: list[ClassifiedPost] = []

    for md_file in KB_DIR.rglob("*.md"):
        if md_file.name.startswith("_") or md_file.name == "INDEX.md":
            continue

        text = md_file.read_text(encoding="utf-8")
        meta, body = _parse_md(text)

        pid = meta.get("post_id", "")
        if not pid:
            pid = md_file.stem.rsplit("_", 1)[-1] if "_" in md_file.stem else md_file.stem

        xhs = XHSPost(
            url=meta.get("url", ""),
            post_id=pid,
            title=meta.get("title", md_file.stem),
            content=_extract_content(body),  # strip template, keep real body only
            author_name=meta.get("author", ""),
            like_count=int(meta.get("likes", 0)),
            collect_count=int(meta.get("collects", 0)),
            comment_count=int(meta.get("comments", 0)),
            tags=meta.get("tags", "").replace("，", ",").split(","),
            image_urls=[],
        )

        # Preserve previous category if already classified
        prev_cat = meta.get("category", "")
        prev_sub = meta.get("sub_category", "")
        prev_keywords = meta.get("keywords", "")

        posts.append(ClassifiedPost(
            post=xhs,
            category=prev_cat if prev_cat and prev_cat != "未分类" else "",  # empty = re-classify
            sub_category=prev_sub,
            summary=meta.get("summary", ""),
            keywords=prev_keywords.replace("，", ",").split(",") if prev_keywords else [],
            entities=[],
            sentiment=meta.get("sentiment", "neutral"),
            quality_score=float(meta.get("quality_score", 0) or 0),
        ))

    unclassified = [p for p in posts if not p.category or p.category == "未分类"]
    already = len(posts) - len(unclassified)
    print(f"\n已分类: {already}, 待分类: {len(unclassified)}")

    if unclassified:
        xhs_list = [p.post for p in unclassified]
        print(f"\n开始分类 {len(xhs_list)} 篇...")
        new_classified = classify_posts(xhs_list, max_workers=cfg.llm_max_workers)
        # Merge: already-classified + new results
        final = [p for p in posts if p.category and p.category != "未分类"] + new_classified
        print(f"分类完成: {len(final)} 篇")
    else:
        final = [p for p in posts if p.category and p.category != "未分类"]
        print("全部已分类，直接重建")

    # Rebuild with clean content
    print("重建知识库...")
    root = build_knowledge_base(final)
    print(f"Done → {root}/INDEX.md")


def _parse_md(text: str) -> tuple[dict, str]:
    meta = {}
    body_start = 0
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            for line in text[3:end].strip().split("\n"):
                if ":" in line:
                    key, _, val = line.partition(":")
                    meta[key.strip()] = val.strip().strip('"').strip("'")
            body_start = end + 3
    raw = text[body_start:].strip()
    return meta, raw


def _extract_content(raw: str) -> str:
    """extract only the real post body from the MD, stripping builder template

    Due to prior builder bugs, MD files may have duplicated template blocks.
    We use the last "## 正文" which has the real content (earlier ones are empty
    or contain only the title line).
    """
    # Find last "## 正文" — the real content block
    body_idx = raw.rfind("## 正文")
    if body_idx < 0:
        return raw
    body = raw[body_idx + len("## 正文"):]
    # stop at next ## section
    for stop in ["## 图片", "## 关键信息", "## 原文链接", "## 摘要"]:
        idx = body.find(stop)
        if idx > 0:
            body = body[:idx]
    return body.strip()


if __name__ == "__main__":
    main()
