"""Image search indexer — parse MD files, extract image contexts, index in ChromaDB

Each image in the knowledge base gets its own document indexed by the
text surrounding it (OCR text, post content). This enables text-to-image
search: user types a query, gets back matching images.
"""

import re
from pathlib import Path

import chromadb

from src.kb_agent.indexer import _get_embedding
from src.logger import get_logger

log = get_logger(__name__)

KB_DIR = Path("output/knowledge_base")
CHROMA_DIR = Path("output/chroma_db")
IMAGE_COLLECTION = "xhs_images"
CONTEXT_WINDOW = 300  # chars of context around each image


def build_image_index(kb_dir: Path | None = None, chroma_dir: Path | None = None) -> int:
    """index all images from knowledge base MD files into ChromaDB

    Returns number of images indexed.
    """
    kb_path = kb_dir or KB_DIR
    chroma_path = chroma_dir or CHROMA_DIR

    md_files = list(kb_path.rglob("*.md"))
    md_files = [f for f in md_files if not f.name.startswith("_") and f.name != "INDEX.md"]

    if not md_files:
        log.warning("No MD files found in %s", kb_path)
        return 0

    client = chromadb.PersistentClient(path=str(chroma_path))
    try:
        client.delete_collection(IMAGE_COLLECTION)
    except Exception:
        pass
    collection = client.create_collection(
        name=IMAGE_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    total = 0
    for fpath in md_files:
        text = fpath.read_text(encoding="utf-8")
        meta, body = _parse_md(text)

        if not meta:
            continue

        images = _extract_images(body)
        if not images:
            continue

        post_title = meta.get("title", fpath.stem)
        post_category = meta.get("category", "")
        post_url = meta.get("url", "")

        for img_path, context in images:
            doc_id = f"{Path(img_path).stem}"
            collection.add(
                ids=[doc_id],
                embeddings=[_get_embedding(context)],
                metadatas=[{
                    "image_path": img_path,
                    "post_title": post_title,
                    "category": post_category,
                    "url": post_url,
                }],
                documents=[context],
            )
            total += 1
            log.debug("Indexed image: %s", img_path)

    log.info("Image index built: %d images → %s", total, chroma_path)
    return total


def _parse_md(text: str) -> tuple[dict, str]:
    """parse frontmatter and body from MD text"""
    meta = {}
    body = text
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            for line in text[3:end].strip().split("\n"):
                if ":" in line:
                    key, _, val = line.partition(":")
                    meta[key.strip()] = val.strip().strip('"').strip("'")
            body = text[end + 3:]
    return meta, body


def _extract_images(body: str) -> list[tuple[str, str]]:
    """extract image references from body

    Uses the FULL post body (including OCR text merged by formatter)
    as context for each image, since per-image OCR text is merged into
    the post content and not stored separately.

    Returns list of (image_path, context_text).
    """
    pattern = re.compile(r"!\[.*?\]\(([^)]+)\)")
    matches = list(pattern.finditer(body))

    if not matches:
        return []

    # Build clean full-body context once (all images in a post share it)
    clean_body = re.sub(r"!\[.*?\]\([^)]+\)", "", body)  # remove images
    clean_body = re.sub(r"#{2,}\s*图片\s*\n.*?(?=#{2,}|\Z)", "", clean_body, flags=re.DOTALL)  # remove image section
    clean_body = re.sub(r"\n{3,}", "\n\n", clean_body).strip()

    if len(clean_body) < 30:
        return []

    results = []
    for m in matches:
        img_path = m.group(1)
        if "/images/" in img_path:
            img_path = img_path.split("/images/")[-1]
            img_path = f"images/{img_path}"
        results.append((img_path, clean_body[:1000]))

    return results
