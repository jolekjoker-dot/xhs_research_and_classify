"""Test search improvements: RRF + parallel + optional rerank"""
from src.kb_agent.rag_engine import hybrid_search

results = hybrid_search("Agent面试", top_k=5)
for i, r in enumerate(results, 1):
    print(f"{i}. [{r['score']:.3f}] {r['title'][:50]} ({r['method']})")
