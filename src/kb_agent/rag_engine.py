"""RAG search engine — semantic + keyword hybrid search over local KB"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import chromadb

from src.kb_agent.indexer import CHROMA_DIR, COLLECTION_NAME, _get_embedding
from src.logger import get_logger

log = get_logger(__name__)

KB_DIR = Path("output/knowledge_base")
RRF_K = 60


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


def hybrid_search(query: str, top_k: int = 10, rerank: bool = True) -> list[dict]:
    """combined keyword + semantic search with RRF fusion + optional rerank"""
    # parallel keyword + semantic
    with ThreadPoolExecutor(max_workers=2) as executor:
        kw_future = executor.submit(keyword_search, query, top_k)
        sem_future = executor.submit(semantic_search, query, top_k)
        kw_results = kw_future.result()
        sem_results = sem_future.result()

    if not kw_results and not sem_results:
        return []

    # RRF fusion (replaces simple score averaging)
    merged = _rrf_fusion(kw_results, sem_results)
    merged = merged[:max(top_k, 20)]  # keep 20 for reranker

    if rerank and len(merged) > 5:
        merged = _rerank(query, merged, top_k=min(top_k, 5))

    return merged[:top_k]


def _rrf_fusion(list_a: list[dict], list_b: list[dict], k: int = RRF_K) -> list[dict]:
    """Reciprocal Rank Fusion — merges two ranked lists by rank position"""
    scores: dict[str, float] = {}
    lookup: dict[str, dict] = {}

    for rank, item in enumerate(list_a):
        key = item.get("path", item.get("title", str(rank)))
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        lookup[key] = item

    for rank, item in enumerate(list_b):
        key = item.get("path", item.get("title", str(rank)))
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        if key not in lookup:
            lookup[key] = item

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    result = []
    for doc_id, rrf_score in ranked:
        item = lookup.get(doc_id)
        if item:
            item = dict(item)
            item["score"] = round(rrf_score, 3)
            item["method"] = "hybrid"
            result.append(item)
    return result


def _get_reranker():
    """lazy-load reranker singleton"""
    global _reranker
    if _reranker is None:
        try:
            from src.kb_agent.reranker import ReRanker
            _reranker = ReRanker()
        except ImportError:
            log.warning("Reranker not available")
            _reranker = False  # type: ignore
    return _reranker if _reranker is not False else None


_reranker = None


def _rerank(query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
    """re-rank candidates with Cross-Encoder if available"""
    model = _get_reranker()
    if model is None:
        return candidates[:top_k]
    try:
        result = model.rerank(query, candidates, top_k=top_k)
        if result:
            return result
    except Exception:
        log.exception("Rerank error, falling back to RRF")
    return candidates[:top_k]
