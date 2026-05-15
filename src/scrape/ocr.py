"""OCR module — extract text from XHS post images via PaddleOCR"""

import os

# Fix protobuf compatibility: newer protobuf (5.x+) breaks PaddleOCR's
# generated _pb2.py files. Setting this before any torch/onnx import avoids
# "Descriptors cannot be created directly" errors.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

from pathlib import Path
from typing import Optional

from src.logger import get_logger

log = get_logger(__name__)

_ocr_backend = None

MIN_IMAGE_WIDTH = 200
MIN_IMAGE_HEIGHT = 200
MIN_IMAGE_SIZE_KB = 10


def _init_paddleocr():
    try:
        import torch  # noqa: preload to fix shm.dll on Windows
        from paddleocr import PaddleOCR
        log.info("Using PaddleOCR backend")
        return PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    except Exception as e:
        log.warning("PaddleOCR not available: %s", e)
        return None


def _init_easyocr():
    try:
        import torch  # noqa: preload to fix shm.dll on Windows
        import easyocr
        log.info("Using EasyOCR backend (loading models...)")
        return easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)
    except Exception as e:
        log.warning("EasyOCR not available: %s", e)
        return None


def _get_backend():
    global _ocr_backend
    if _ocr_backend is None:
        _ocr_backend = _init_paddleocr() or _init_easyocr()
        if _ocr_backend is None:
            log.warning("No OCR backend available, image text extraction disabled")
    return _ocr_backend


def ocr_image(image_path: str | Path) -> str:
    """extract text from a single image"""
    path = Path(image_path)
    if not path.exists():
        return ""

    backend = _get_backend()
    if backend is None:
        return ""

    try:
        module_name = type(backend).__module__

        if "paddleocr" in module_name:
            results = backend.ocr(str(path), cls=True)
            if not results or not results[0]:
                return ""
            lines = [line[1][0] for line in results[0] if line[1][0]]
            return "\n".join(lines).strip()

        elif "easyocr" in module_name:
            results = backend.readtext(str(path), detail=0)
            return "\n".join(results).strip() if results else ""

    except Exception:
        log.exception("OCR failed for: %s", path)
        return ""

    return ""


def _is_content_image(path: Path) -> bool:
    """check if image is large enough to contain text content (not avatar/icon)"""
    size_kb = path.stat().st_size / 1024
    if size_kb < MIN_IMAGE_SIZE_KB:
        return False
    try:
        from PIL import Image
        with Image.open(path) as img:
            w, h = img.size
        return w >= MIN_IMAGE_WIDTH and h >= MIN_IMAGE_HEIGHT
    except Exception:
        return size_kb >= 50


def _has_overlap(text_a: str, text_b: str, min_overlap: int = 15) -> Optional[str]:
    """detect overlap between end of text_a and start of text_b"""
    max_overlap = min(len(text_a), len(text_b), 100)
    for n in range(max_overlap, min_overlap - 1, -1):
        suffix = text_a[-n:]
        if text_b[:n] == suffix:
            return suffix
    return None


def merge_ocr_texts(texts: list[str], min_overlap: int = 15) -> str:
    """merge multiple OCR text segments with overlap detection"""
    if not texts:
        return ""
    if len(texts) == 1:
        return texts[0]

    merged = texts[0]
    for text in texts[1:]:
        overlap = _has_overlap(merged, text, min_overlap)
        if overlap:
            merged += text[len(overlap):]
        else:
            merged += "\n" + text
    return merged


def ocr_images(image_paths: list[str], skip_duplicates: bool = True) -> str:
    """extract text from images, skipping small/duplicate images"""
    backend = _get_backend()
    if backend is None:
        log.warning("OCR not available, skipping %d images", len(image_paths))
        return ""

    texts: list[str] = []
    seen_hashes: set[int] = set()
    skipped_small = 0
    skipped_dup = 0
    content_images = []

    for path in image_paths:
        p = Path(path)
        if not p.exists():
            continue

        if not _is_content_image(p):
            skipped_small += 1
            continue

        file_hash = p.stat().st_size
        if skip_duplicates and file_hash in seen_hashes:
            skipped_dup += 1
            continue
        seen_hashes.add(file_hash)
        content_images.append(p)

    for i, p in enumerate(content_images):
        try:
            log.info("OCR [%d/%d] %s", i + 1, len(content_images), p.name)
            text = ocr_image(str(p))
            if text:
                texts.append(text)
        except Exception:
            log.warning("OCR error for: %s", p.name)

    log.info("OCR: %d texts / %d images (skipped %d small + %d dup)",
             len(texts), len(image_paths), skipped_small, skipped_dup)

    if not texts:
        return ""

    result = merge_ocr_texts(texts)
    log.info("OCR complete: %d chars", len(result))
    return result
