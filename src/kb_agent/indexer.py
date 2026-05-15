"""Vector index builder — parse Markdown KB → embeddings → ChromaDB

Uses ModelScope (Chinese mirror) to download embedding model, avoiding HF network issues.
"""

import json
import re
from pathlib import Path

import chromadb

from src.logger import get_logger

log = get_logger(__name__)

KB_DIR = Path("output/knowledge_base")
CHROMA_DIR = Path("output/chroma_db")
COLLECTION_NAME = "xhs_knowledge"

_embedder = None


def _get_embedder():
    """lazy-load embedding model via ModelScope (no HF download needed)"""
    global _embedder
    if _embedder is None:
        from modelscope import snapshot_download
        from sentence_transformers import SentenceTransformer

        log.info("Downloading embedding model from ModelScope...")
        model_dir = snapshot_download(
            "iic/nlp_gte_sentence-embedding_chinese-small",
            revision="master",
        )
        _embedder = SentenceTransformer(model_dir)
        log.info("Embedding model ready (dim=%d)", _embedder.get_sentence_embedding_dimension())
    return _embedder


def _get_embedding(text: str) -> list[float]:
    embedder = _get_embedder()
    return embedder.encode(text).tolist()


def _parse_md(filepath: Path) -> dict:
    """parse a knowledge base MD file into structured data"""
    text = filepath.read_text(encoding="utf-8")
    meta = {}
    body_start = 0

    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            frontmatter = text[3:end].strip()
            for line in frontmatter.split("\n"):
                if ":" in line:
                    key, _, val = line.partition(":")
                    meta[key.strip()] = val.strip().strip('"')
            body_start = end + 3

    body = text[body_start:].strip()
    # split into chunks by ## headings
    chunks = []
    sections = re.split(r"\n(?=## )", body)
    for section in sections:
        section = section.strip()
        if section:
            chunks.append(section)

    if not chunks:
        chunks = [body]

    return {
        "path": str(filepath),
        "title": meta.get("title", filepath.stem),
        "category": meta.get("category", ""),
        "tags": meta.get("tags", ""),
        "keywords": meta.get("keywords", ""),
        "likes": int(meta.get("likes", 0)),
        "collects": int(meta.get("collects", 0)),
        "comments": int(meta.get("comments", 0)),
        "url": meta.get("url", ""),
        "chunks": chunks,
    }


def build_index(kb_dir: Path | None = None, chroma_dir: Path | None = None) -> int:
    """build ChromaDB vector index from knowledge base MD files

    Returns number of chunks indexed.
    """
    kb_path = kb_dir or KB_DIR
    chroma_path = chroma_dir or CHROMA_DIR

    md_files = list(kb_path.rglob("*.md"))
    # exclude index files
    md_files = [f for f in md_files if not f.name.startswith("_")]
    if not md_files:
        log.warning("No MD files found in %s", kb_path)
        return 0

    log.info("Parsing %d MD files...", len(md_files))
    docs = [_parse_md(f) for f in md_files]

    client = chromadb.PersistentClient(path=str(chroma_path))
    # recreate collection each time for fresh index
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(name=COLLECTION_NAME)

    total_chunks = 0
    for doc in docs:
        for i, chunk in enumerate(doc["chunks"]):
            chunk_id = f"{Path(doc['path']).stem}_{i}"
            embedding = _get_embedding(chunk)
            collection.add(
                ids=[chunk_id],
                embeddings=[embedding],
                metadatas=[{
                    "title": doc["title"],
                    "category": doc["category"],
                    "path": doc["path"],
                    "url": doc["url"],
                    "tags": doc["tags"],
                    "keywords": doc["keywords"],
                    "chunk_index": i,
                }],
                documents=[chunk],
            )
            total_chunks += 1

    log.info("Indexed %d chunks from %d documents → %s", total_chunks, len(docs), chroma_path)
    return total_chunks


if __name__ == "__main__":
    build_index()
