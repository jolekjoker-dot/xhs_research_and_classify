"""Cross-Encoder reranker — lazy-loads bge-reranker-v2-m3 for result refinement

Downloads the model via ModelScope (Chinese mirror). Uses sentence_transformers
CrossEncoder (already a dependency) to avoid FlagEmbedding/transformers version
conflicts.
"""

from src.logger import get_logger

log = get_logger(__name__)


class ReRanker:
    """Cross-Encoder reranker using BAAI/bge-reranker-v2-m3"""

    def __init__(self):
        self._model = None
        self._model_dir = None

    def _load(self) -> bool:
        if self._model is None:
            # download via ModelScope
            if self._model_dir is None:
                try:
                    from modelscope import snapshot_download
                    log.info("Downloading bge-reranker-v2-m3 via ModelScope...")
                    self._model_dir = snapshot_download(
                        "BAAI/bge-reranker-v2-m3",
                        revision="master",
                    )
                    log.info("Model cached at %s", self._model_dir)
                except Exception:
                    log.exception("ModelScope download failed")
                    self._model = False
                    return False

            try:
                from sentence_transformers import CrossEncoder
                log.info("Loading bge-reranker-v2-m3 (~30s first run)...")
                self._model = CrossEncoder(self._model_dir)
                log.info("Reranker ready")
            except Exception:
                log.exception("Failed to load reranker")
                self._model = False
                return False
        return self._model is not False

    def rerank(self, query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
        """re-rank candidates by Cross-Encoder relevance"""
        if not candidates:
            return []

        if not self._load():
            return candidates[:top_k]

        pairs = []
        for c in candidates:
            doc = c.get("document", c.get("content", ""))
            pairs.append([query, doc])

        scores = self._model.predict(pairs, show_progress_bar=False)
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        if isinstance(scores, float):
            scores = [scores]

        ranked = sorted(zip(candidates, scores), key=lambda x: -x[1])
        result = []
        for item, score in ranked:
            item = dict(item)
            item["score"] = round(float(score), 3)
            item["method"] = "hybrid+rerank"
            result.append(item)

        return result[:top_k]
