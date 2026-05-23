"""RAG QA engine — search + answer with source attribution"""

import re
from pathlib import Path

from openai import OpenAI

from src.config import get_config
from src.kb_agent.rag_engine import hybrid_search
from src.logger import get_logger

log = get_logger(__name__)

QA_SYSTEM_PROMPT = """你是一个知识库问答助手。根据提供的知识库内容回答用户问题。

要求：
- 只基于下面提供的知识库内容回答，不要编造知识库中没有的信息
- 如果内容不足以完整回答问题，请明确说明"知识库中关于此问题的信息有限"
- 回答要结构化、有条理
- 在答案最后列出引用的来源文档"""

QA_USER_TEMPLATE = """## 知识库内容
{context}

## 用户问题
{query}

## 回答
请基于上述知识库内容回答用户问题。"""


def _read_md_content(path_str: str, max_chars: int = 2000) -> str:
    """read content from a KB MD file, extracting only real body + OCR text"""
    try:
        p = Path(path_str)
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8")

        # strip frontmatter
        if text.startswith("---"):
            end = text.find("---", 3)
            if end > 0:
                text = text[end + 3:]

        # Extract only first occurrence of each real content section
        parts = []

        # 摘要
        idx = text.find("## 摘要")
        if idx >= 0:
            chunk = text[idx + len("## 摘要"):]
            end_idx = chunk.find("## ")
            if end_idx > 0:
                chunk = chunk[:end_idx]
            parts.append("## 摘要\n" + chunk.strip())

        # 正文 (first occurrence before 图片 section)
        idx = text.find("## 正文")
        if idx >= 0:
            chunk = text[idx + len("## 正文"):]
            for stop in ["## 图片", "## 关键信息", "## 原文链接", "## 摘要"]:
                end_idx = chunk.find(stop)
                if end_idx > 0:
                    chunk = chunk[:end_idx]
            parts.append("## 正文\n" + chunk.strip())

        # 图片提取文字
        idx = text.find("## 图片提取文字")
        if idx >= 0:
            chunk = text[idx + len("## 图片提取文字"):]
            end_idx = chunk.find("## 图片")
            if end_idx > 0:
                chunk = chunk[:end_idx]
            parts.append("## 图片提取文字\n" + chunk.strip())

        content = "\n\n".join(parts) if parts else text.strip()
        # remove image markdown
        content = re.sub(r"!\[.*?\]\([^)]+\)", "", content)
        content = re.sub(r"\n{3,}", "\n\n", content).strip()

        return content[:max_chars]
    except Exception:
        return ""


def answer_question(query: str, top_k: int = 5) -> dict:
    """search knowledge base and generate answer with LLM

    Returns {"answer": str, "sources": list[dict], "query": str}
    """
    # 1. Retrieve relevant documents
    docs = hybrid_search(query, top_k=top_k)
    if not docs:
        return {
            "answer": "知识库中未找到相关内容，无法回答此问题。",
            "sources": [],
            "query": query,
        }

    # 2. Read full content from MD files (search chunk may be incomplete)
    parts = []
    sources = []
    for i, d in enumerate(docs, 1):
        path = d.get("path", "")
        title = d.get("title", "未知")
        source = f"[{i}] {title}"

        # Prefer full file content over search chunk
        content = _read_md_content(path) if path else d.get("content", "")
        if not content:
            content = d.get("content", "")[:1500]

        parts.append(f"### {source}\n{content}")
        sources.append({
            "title": title,
            "path": path,
            "score": d.get("score", 0),
            "url": d.get("url", ""),
        })

    context = "\n\n---\n\n".join(parts)
    prompt = QA_USER_TEMPLATE.format(context=context, query=query)

    # 3. Call LLM
    config = get_config()
    client = OpenAI(api_key=config.api_key, base_url=config.api_base_url)

    try:
        response = client.chat.completions.create(
            model=config.api_model,
            messages=[
                {"role": "system", "content": QA_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=1500,
        )
        answer = response.choices[0].message.content or "生成答案失败，请重试。"
        log.info("QA complete: %d chars answer, %d sources", len(answer), len(sources))
    except Exception:
        log.exception("QA LLM call failed")
        answer = "LLM 调用失败，请检查 API 配置。以下是相关文档链接："
        for s in sources:
            answer += f"\n- {s['title']}: {s.get('url', s.get('path', ''))}"

    return {
        "answer": answer,
        "sources": sources,
        "query": query,
    }
