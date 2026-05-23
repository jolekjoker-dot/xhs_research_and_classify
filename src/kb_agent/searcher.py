"""Multi-layer search coordinator + result formatter + scope constraint"""

from pathlib import Path

from src.config import get_config
from src.kb_agent.rag_engine import hybrid_search, image_search, keyword_search, semantic_search
from src.logger import get_logger

log = get_logger(__name__)

# search scope is strictly limited to current project directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _is_in_scope(filepath: str) -> bool:
    """ensure file is within current project directory"""
    try:
        Path(filepath).resolve().relative_to(PROJECT_ROOT.resolve())
        return True
    except ValueError:
        return False


def search(
    query: str,
    top_k: int = 10,
    layers: list[str] | None = None,
) -> list[dict]:
    """multi-layer search coordinator

    Args:
        query: natural language search query
        top_k: max results per layer
        layers: which layers to use, default ['keyword', 'semantic', 'hybrid']

    Returns:
        deduplicated results sorted by relevance, each with title/path/score/summary
    """
    config = get_config()

    if layers is None:
        layers = ["hybrid"]

    all_results: list[dict] = []

    if "keyword" in layers:
        kw = keyword_search(query, top_k)
        all_results.extend(kw)

    if "semantic" in layers:
        sem = semantic_search(query, top_k)
        all_results.extend(sem)

    if "hybrid" in layers and "hybrid" not in layers:
        pass  # hybrid already included via keyword+semantic
    elif "hybrid" in layers:
        hy = hybrid_search(query, top_k, rerank=config.rerank_enabled)
        # hybrid results may overlap with individual layers, merge carefully
        seen_titles = {r.get("title", "") for r in all_results}
        for r in hy:
            if r.get("title", "") not in seen_titles:
                all_results.append(r)

    # dedup by path
    seen: set[str] = set()
    unique: list[dict] = []
    for r in sorted(all_results, key=lambda x: -x.get("score", 0)):
        key = r.get("path", r.get("title", ""))
        if key not in seen:
            # scope check
            path = r.get("path", "")
            if path and not _is_in_scope(path):
                continue
            seen.add(key)
            unique.append(r)

    return unique[:top_k]


def format_results(results: list[dict], query: str) -> str:
    """format search results as structured Markdown"""
    if not results:
        return f"## 检索结果: \"{query}\"\n\n未找到相关内容。"

    lines = [
        f"## 检索结果: \"{query}\"",
        "",
        f"### 匹配文档 (共 {len(results)} 篇)",
        "",
    ]

    for i, r in enumerate(results, 1):
        title = r.get("title", "未知")
        path = r.get("path", "")
        score = r.get("score", 0)
        content = r.get("content", "")
        category = r.get("category", "")
        method = r.get("method", "")
        url = r.get("url", "")

        # method badge
        method_label = {"keyword": "[关键词]", "semantic": "[语义]", "hybrid": "[混合]"}.get(method, "")

        lines.append(f"{i}. **[{title}]({path})** — 相关度: {score:.0%} {method_label}")
        if category:
            lines.append(f"   - 分类: {category}")
        if content:
            lines.append(f"   - 正文: {content}")
        if url:
            lines.append(f"   - 来源: {url}")
        lines.append("")

    # add method explanation
    methods_used = set(r.get("method", "") for r in results)
    lines.append("---")
    lines.append(f"*检索方式: {', '.join(methods_used)}*")

    return "\n".join(lines)


def search_images(query: str, top_k: int = 5) -> list[dict]:
    """search images by text query, returns list of image results"""
    results = image_search(query, top_k=top_k)
    # scope check
    valid = []
    for r in results:
        if r.get("kb_path") and _is_in_scope(r["kb_path"]):
            valid.append(r)
    return valid[:top_k]


def format_image_results(results: list[dict], query: str) -> str:
    """format image search results as structured Markdown"""
    if not results:
        return f"## 图片检索: \"{query}\"\n\n未找到匹配图片。"

    lines = [
        f"## 图片检索: \"{query}\"",
        "",
        f"### 匹配图片 (共 {len(results)} 张)",
        "",
    ]

    for i, r in enumerate(results, 1):
        img_path = r.get("image_path", "")
        score = r.get("score", 0)
        title = r.get("post_title", "")
        category = r.get("category", "")
        content = r.get("content", "")

        lines.append(f"{i}. **![{img_path}]({img_path})** — 相关度: {score:.0%}")
        lines.append(f"   - 来源: {title}")
        if category:
            lines.append(f"   - 分类: {category}")
        lines.append(f"   - 上下文: {content[:200]}")
        lines.append("")

    lines.append("---")
    lines.append("*检索方式: 图片语义搜索*")
    return "\n".join(lines)
