"""Query Rewriter — LLM analyzes input, picks strategy, outputs search-friendly queries

Three rewrite types (LLM self-judges):
  decompose — long sentence with multiple sub-topics → split into 2-3 queries
  oral      — colloquial/informal → standardize as search keywords
  fuzzy     — vague, missing technical terms → generate precise synonyms
"""

import json
import re

from openai import OpenAI

from src.config import get_config
from src.logger import get_logger

log = get_logger(__name__)

REWRITE_PROMPT = """你是一个查询改写专家。分析用户输入，判断类型并改写为搜索友好的关键词组合。

三种类型：
1. decompose — 句子长、包含多个子话题 → 拆解为2-3条独立搜索query
2. oral — 口语化、非正式表达 → 标准化为搜索关键词
3. fuzzy — 表达模糊、缺少专业术语 → 生成同义精准搜索query

只输出JSON，不要其他内容：
{"type": "decompose|oral|fuzzy", "queries": ["q1", "q2", ...]}"""

REWRITE_USER_TEMPLATE = "用户输入: {query}"


class QueryRewriter:
    """LLM-based query rewriter with self-judging strategy selection"""

    def __init__(self):
        self._last_type = ""
        self._last_queries: list[str] = []

    def rewrite(self, query: str) -> dict:
        """rewrite query into search-friendly keywords

        Returns {"type": str, "queries": list[str], "original": str}
        """
        config = get_config()
        if not config.api_key:
            log.warning("No API key, skip rewrite")
            return {"type": "passthrough", "queries": [query], "original": query}

        prompt = REWRITE_USER_TEMPLATE.format(query=query)

        try:
            client = OpenAI(api_key=config.api_key, base_url=config.api_base_url)
            response = client.chat.completions.create(
                model=config.api_model,
                messages=[
                    {"role": "system", "content": REWRITE_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=400,
            )
            raw = response.choices[0].message.content or "{}"
            data = self._parse(raw)

            self._last_type = data.get("type", "passthrough")
            self._last_queries = data.get("queries", [query])

            log.info("Rewrite: %s → type=%s, %d queries",
                     query[:40], self._last_type, len(self._last_queries))

            return {
                "type": self._last_type,
                "queries": self._last_queries,
                "original": query,
            }

        except Exception:
            log.exception("Rewrite failed, using original query")
            return {"type": "passthrough", "queries": [query], "original": query}

    @staticmethod
    def _parse(raw: str) -> dict:
        """extract JSON from LLM response, with tolerance for markdown wrapping"""
        # strip code fences
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if m:
            raw = m.group(1).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # try extracting JSON object
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(raw[start:end])
            raise
