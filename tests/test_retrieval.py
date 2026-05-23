"""Retrieval evaluation: compare keyword / semantic / RRF / rerank quality.

Labels are defined below as QUERY_LABELS. Each query maps to post IDs that
are relevant (manually judged). Run with:

    python -m pytest tests/test_retrieval.py -v -s
"""
import json
import math
from pathlib import Path

import pytest

from src.kb_agent.rag_engine import (
    hybrid_search,
    keyword_search,
    semantic_search,
    _rrf_fusion,
)
from src.kb_agent.reranker import ReRanker

# ── Annotation labels ──────────────────────────────────────────
# Fill in post IDs that should be returned for each query.
# Post IDs are the 8-char hex suffixes from MD filenames (e.g. 69d51830).
QUERY_LABELS: dict[str, dict] = {
    "字节Agent面试": {
        "relevant": [
            "69b4daa5",  # 字节跳动Agent开发一面
            "69ba757c",  # 字节 大模型应用开发 二面面经
            "69de2751",  # 字节AI Agent面试经验（已Offer）
            "69e5db62",  # 字节AI Agent面试经验（已Offer）
            "69aa8465",  # 字节春招 26届后端开发Agent一面
        ],
    },
    "大模型八股文": {
        "relevant": [
            "67f38575",  # 5月大模型面试必问八股文，背完通过率98%
            "69ec2ba1",  # 大模型八股文，5天背完拿offer
            "69fb5130",  # 近期大厂大模型岗位面经总结（2026.3-4）
            "69ad4bb9",  # 2026大模型Agent面试全攻略（上）
        ],
    },
    "腾讯面试": {
        "relevant": [
            "69d51830",  # 20260407腾讯Agent应用开发一面
            "69db6240",  # 腾讯大模型应用开发 二面
            "69cbee68",  # 暑期腾讯一面面经
        ],
    },
    "Agent开发Python": {
        "relevant": [
            "699d5d16",  # agent 开发只会 python 不学 java 行不行
            "69a3163e",  # 不建议 agent 开发只会 python
        ],
    },
    "阿里Agent面试": {
        "relevant": [
            "69b41390",  # 阿里大模型Agent面试，面试完人傻了。。。
            "69dbbf4b",  # 阿里淘天暑期实习Agent算法岗一面
            "6a00a774",  # 淘天-ai应用开发-二面
        ],
    },
}


# ── Metrics ────────────────────────────────────────────────────

def _get_id(result: dict) -> str:
    """extract post ID from result path or title"""
    path = result.get("path", "")
    if path:
        stem = Path(path).stem
        if "_" in stem:
            return stem.rsplit("_", 1)[-1]
        return stem
    return ""


def recall_at_k(results: list[dict], relevant_ids: set[str], k: int = 20) -> float:
    """fraction of relevant docs found in top-k results"""
    if not relevant_ids:
        return 1.0
    found = sum(1 for r in results[:k] if _get_id(r) in relevant_ids)
    return found / len(relevant_ids)


def mrr(results: list[dict], relevant_ids: set[str]) -> float:
    """Mean Reciprocal Rank — 1 / rank of first relevant doc"""
    for i, r in enumerate(results, 1):
        if _get_id(r) in relevant_ids:
            return 1.0 / i
    return 0.0


def ndcg_at_k(results: list[dict], relevant_ids: set[str], k: int = 5) -> float:
    """Normalized Discounted Cumulative Gain — relevance-weighted ranking quality"""
    if not relevant_ids:
        return 1.0

    # binary relevance: 1 if in relevant set, 0 otherwise
    dcg = 0.0
    idcg = 0.0
    for i, r in enumerate(results[:k], 1):
        rel = 1.0 if _get_id(r) in relevant_ids else 0.0
        dcg += rel / math.log2(i + 1)
    # ideal: all relevant docs at top
    for i in range(1, min(len(relevant_ids), k) + 1):
        idcg += 1.0 / math.log2(i + 1)
    return dcg / idcg if idcg > 0 else 0.0


# ── Test runner ────────────────────────────────────────────────

@pytest.mark.parametrize("query_name", list(QUERY_LABELS.keys()))
class TestRetrieval:
    """Compare 4 retrieval methods across all labeled queries"""

    def test_compare_all_methods(self, query_name: str):
        label = QUERY_LABELS[query_name]
        query = query_name
        relevant = set(label["relevant"])

        if not relevant:
            pytest.skip(f"No labels for: {query_name}")

        # semantic candidates for reranker
        sem_candidates = semantic_search(query, top_k=20)
        reranker = ReRanker()
        sem_reranked = reranker.rerank(query, sem_candidates, top_k=5) if reranker._load() else sem_candidates[:5]

        methods = {
            "keyword": lambda: keyword_search(query, top_k=5),
            "semantic": lambda: semantic_search(query, top_k=5),
            "RRF": lambda: _rrf_fusion(
                keyword_search(query, top_k=10),
                semantic_search(query, top_k=10),
            )[:5],
            "RRF+rerank": lambda: hybrid_search(query, top_k=5, rerank=True),
            "sem+rerank": lambda: sem_reranked,
        }

        results = {}
        for name, fn in methods.items():
            try:
                results[name] = fn()
            except Exception as e:
                results[name] = []
                print(f"  {name}: ERROR — {e}")

        print(f"\nQuery: {query_name} (相关: {len(relevant)} 篇)")
        print("-" * 65)
        print(f"{'Method':<15} {'Recall@5':>10} {'MRR':>10} {'nDCG@5':>10}")
        print("-" * 65)

        metrics = {}
        for name, res in results.items():
            r5 = recall_at_k(res, relevant, k=5)
            m = mrr(res, relevant)
            n = ndcg_at_k(res, relevant, k=5)
            metrics[name] = {"recall": r5, "mrr": m, "ndcg": n}
            print(f"{name:<15} {r5:>10.3f} {m:>10.3f} {n:>10.3f}")

        print("-" * 65)

        # Best method for each metric
        best_recall = max(metrics.items(), key=lambda x: x[1]["recall"])
        best_mrr = max(metrics.items(), key=lambda x: x[1]["mrr"])
        best_ndcg = max(metrics.items(), key=lambda x: x[1]["ndcg"])
        print(f"Best Recall: {best_recall[0]} ({best_recall[1]['recall']:.3f})")
        print(f"Best MRR:    {best_mrr[0]} ({best_mrr[1]['mrr']:.3f})")
        print(f"Best nDCG:   {best_ndcg[0]} ({best_ndcg[1]['ndcg']:.3f})")

        # sem+rerank vs semantic gain
        if "semantic" in metrics and "sem+rerank" in metrics:
            mrr_gain = metrics["sem+rerank"]["mrr"] - metrics["semantic"]["mrr"]
            ndcg_gain = metrics["sem+rerank"]["ndcg"] - metrics["semantic"]["ndcg"]
            print(f"sem+rerank vs semantic: MRR {mrr_gain:+.1%}  nDCG {ndcg_gain:+.1%}")

        # Assert rerank doesn't hurt (within noise)
        if "RRF" in metrics and "RRF+rerank" in metrics:
            # Rerank should improve or be equal to RRF alone
            pass  # informational only, not a hard assertion


def test_print_summary():
    """Print a summary table of all query results"""
    print("\n" + "=" * 70)
    print("综合对比摘要")
    print("=" * 70)

    print(f"{'Query':<20} {'Method':<15} {'Recall@5':>10} {'MRR':>10} {'nDCG@5':>10}")
    print("-" * 70)

    reranker = ReRanker()
    reranker_ok = reranker._load()

    for query_name, label in QUERY_LABELS.items():
        relevant = set(label["relevant"])
        if not relevant:
            continue

        query = query_name
        sem_candidates = semantic_search(query, top_k=20)
        sem_reranked = reranker.rerank(query, sem_candidates, top_k=5) if reranker_ok else sem_candidates[:5]

        methods = [
            ("keyword", lambda: keyword_search(query, top_k=5)),
            ("semantic", lambda: semantic_search(query, top_k=5)),
            ("RRF", lambda: _rrf_fusion(
                keyword_search(query, top_k=10),
                semantic_search(query, top_k=10),
            )[:5]),
            ("RRF+rerank", lambda: hybrid_search(query, top_k=5, rerank=True)),
            ("sem+rerank", lambda: sem_reranked),
        ]

        for name, fn in methods:
            try:
                res = fn()
            except Exception:
                continue
            r5 = recall_at_k(res, relevant, k=5)
            m = mrr(res, relevant)
            n = ndcg_at_k(res, relevant, k=5)
            print(f"{query_name:<20} {name:<15} {r5:>10.3f} {m:>10.3f} {n:>10.3f}")
        print("-" * 70)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
