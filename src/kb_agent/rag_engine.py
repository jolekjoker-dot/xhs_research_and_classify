"""RAG search engine — semantic + keyword hybrid search over local KB"""

from pathlib import Path

import chromadb

from src.kb_agent.indexer import CHROMA_DIR, COLLECTION_NAME, _get_embedding
from src.logger import get_logger

log = get_logger(__name__)

KB_DIR = Path("output/knowledge_base")


def _parse_md_meta_body(filepath: Path) -> tuple[dict, str]:
    """parse MD file into {title, tags, keywords, ...} meta dict and body text"""
    text = filepath.read_text(encoding="utf-8").lower()
    meta: dict[str, str] = {}
    body = ""

    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            frontmatter = text[3:end].strip()
            for line in frontmatter.split("\n"):
                if ":" in line:
                    key, _, val = line.partition(":")
                    meta[key.strip()] = val.strip().strip('"')
            # body starts after frontmatter
            body = text[end + 3:]

    if not body:
        body = text

    # extract content after "## 正文" heading
    body_match = body.split("## 正文")
    if len(body_match) > 1:
        body = body_match[1]
        # stop at next ## section (图片, 关键信息, 原文链接)
        for stop_word in ["\n## 图片", "\n## 关键信息", "\n## 原文链接"]:
            idx = body.find(stop_word)
            if idx > 0:
                body = body[:idx]
    body = body.strip()

    return meta, body


def keyword_search(query: str, top_k: int = 10) -> list[dict]:
    """keyword match against frontmatter tags + keywords fields only"""
    results = []
    md_files = list(KB_DIR.rglob("*.md"))
    md_files = [f for f in md_files if not f.name.startswith("_") and "INDEX.md" not in f.name]

    if not md_files:
        return results

    import jieba
    query_kw = [k.lower() for k in jieba.cut(query) if k.strip() and len(k.strip()) > 1]

    for fpath in md_files:
        try:
            meta, body = _parse_md_meta_body(fpath)

            # match against tags + keywords fields only
            tags_str = meta.get("tags", "")
            kw_str = meta.get("keywords", "")
            match_text = f"{tags_str} {kw_str}"

            score = sum(1 for k in query_kw if k in match_text)
            if score == 0:
                continue

            title = meta.get("title", fpath.stem)

            try:
                rel_path = str(fpath.relative_to(Path.cwd()))
            except ValueError:
                rel_path = str(fpath)

            results.append({
                "title": title,
                "path": rel_path,
                "score": round(score / max(len(query_kw), 1), 2),
                "content": body[:2000],
                "category": meta.get("category", ""),
                "url": meta.get("url", ""),
                "method": "keyword",
            })
            if len(results) >= top_k:
                break
        except Exception:
            continue

    results.sort(key=lambda x: -x["score"])
    return results[:top_k]


def semantic_search(query: str, top_k: int = 10) -> list[dict]:
    """semantic search using ChromaDB vector similarity"""
    try:
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        collection = client.get_collection(COLLECTION_NAME)
    except Exception:
        log.warning("ChromaDB not found, run build_index first")
        return []

    query_embedding = _get_embedding(query)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    if not results["ids"] or not results["ids"][0]:
        return []

    output = []
    for i, chunk_id in enumerate(results["ids"][0]):
        meta = results["metadatas"][0][i]
        distance = results["distances"][0][i]
        # cosine distance in chromadb: 0=identical, 2=opposite
        similarity = round(1.0 / (1.0 + float(distance)), 3)

        path = meta.get("path", "")
        if "INDEX.md" in path or path.endswith("/_index.md"):
            continue

        output.append({
            "title": meta.get("title", ""),
            "path": path,
            "score": similarity,
            "content": results["documents"][0][i][:2000],
            "category": meta.get("category", ""),
            "url": meta.get("url", ""),
            "method": "semantic",
        })

    return output


def hybrid_search(query: str, top_k: int = 10) -> list[dict]:
    """combined keyword + semantic search with result fusion"""
    kw_results = keyword_search(query, top_k)
    sem_results = semantic_search(query, top_k)

    if not kw_results and not sem_results:
        return []

    # merge and dedup by path
    merged: dict[str, dict] = {}
    for r in kw_results:
        key = r.get("path", r.get("title", ""))
        merged[key] = dict(r)
    for r in sem_results:
        key = r.get("path", r.get("title", ""))
        if key in merged:
            # hybrid: average of both scores
            merged[key]["score"] = round((merged[key]["score"] + r["score"]) / 2, 3)
            merged[key]["method"] = "hybrid"
        else:
            merged[key] = dict(r)

    combined = sorted(merged.values(), key=lambda x: -x["score"])
    return combined[:top_k]
