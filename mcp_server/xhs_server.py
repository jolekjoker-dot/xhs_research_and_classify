#!/usr/bin/env python3
"""XHS Knowledge Base MCP Server

Exposes the XHS knowledge base workflow as MCP tools.
Claude can call these tools directly via the MCP protocol.

Usage:
  python mcp_server/xhs_server.py
  (or via Claude Code MCP config)

Tools exposed:
  - run_pipeline: full workflow (search -> scrape -> format -> classify -> build)
  - search_xhs: search Xiaohongshu only
  - search_kb: search local knowledge base
"""

# ── MUST be set before ANY import that transitively pulls in protobuf ──
# PaddleOCR's _pb2 files are incompatible with protobuf >= 4.x.
# Setting this env var forces pure-Python protobuf (slower but compatible).
import os as _os
_os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(str(PROJECT_ROOT))

from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.stdio import stdio_server

# separate log file for MCP operations (does not interfere with stdio)
LOG_FILE = PROJECT_ROOT / "output" / "mcp_server.log"


def _mcp_log(msg: str) -> None:
    """write to mcp log file (not stdout, to avoid breaking MCP protocol)"""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {msg}\n")


server = Server("xhs-kb")


def _run_sync(func, *args, **kwargs):
    """run sync function in thread pool to avoid blocking async loop"""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        return future.result()


# ── Tool implementations ──────────────────────────────────────────


def _run_pipeline_impl(keyword: str, count: int = 5) -> str:
    """implement run_pipeline tool"""
    from src.search.searcher import search_batch, XHSLoginRequiredError, XHSRateLimitError
    from src.scrape.scraper import scrape_from_results

    try:
        # search
        all_results = search_batch([keyword], count_per=count, headless=True)
    except XHSLoginRequiredError:
        return f"[需要登录] 请在项目目录下运行: python xiaohongshu.py run --keywords '{keyword}' --count {count} --no-headless\n在打开的浏览器中扫码登录后重试。"
    except XHSRateLimitError as e:
        return f"[验证码拦截] {e}\n请稍后重试，或使用 --no-headless 手动解决验证码。"
    except Exception as e:
        return f"[搜索失败] {type(e).__name__}: {e}"

    all_urls = []
    for results in all_results.values():
        if results:
            all_urls.extend(results)
    if not all_urls:
        return f"No results found for: {keyword}\n可能原因: 登录态过期或触发验证码。\n请在项目目录下运行: python xiaohongshu.py search '{keyword}' --no-headless"

    from src.classify.formatter import format_posts
    from src.classify.classifier import classify_posts
    from src.knowledge_base.builder import build_knowledge_base

    # scrape
    try:
        posts = scrape_from_results(all_urls, keyword=keyword, headless=True, resume=True)
    except Exception as e:
        return f"[抓取失败] {type(e).__name__}: {e}"

    if not posts:
        return f"Failed to scrape any posts for: {keyword}"

    # format
    posts = format_posts(posts)

    # classify
    classified = classify_posts(posts)

    # build
    root = build_knowledge_base(classified)

    # summary
    cats = set(cp.category for cp in classified)
    return (
        f"Pipeline complete!\n"
        f"Keyword: {keyword}\n"
        f"Posts: {len(posts)} scraped, {len(classified)} classified\n"
        f"Categories: {', '.join(cats)}\n"
        f"KB output: {root}/INDEX.md"
    )


def _search_xhs_impl(keyword: str, count: int = 5) -> str:
    """implement search_xhs tool"""
    from src.search.searcher import search_batch, XHSSearchError, XHSLoginRequiredError, XHSRateLimitError

    try:
        all_results = search_batch([keyword], count_per=count, headless=True)
    except XHSLoginRequiredError:
        return f"[需要登录] 请在项目目录下运行: python xiaohongshu.py search '{keyword}' --no-headless --count {count}\n在打开的浏览器中扫码登录后重试。"
    except XHSRateLimitError as e:
        return f"[验证码拦截] {e}\n请稍后重试，或在项目目录下运行: python xiaohongshu.py search '{keyword}' --no-headless --count {count}"
    except Exception as e:
        return f"[搜索失败] {type(e).__name__}: {e}"

    lines = [f"Search results for '{keyword}':"]
    total = 0
    for kw, results in all_results.items():
        for r in results:
            lines.append(f"- [{r.title}]({r.url}) by {r.author_name}")
            total += 1
    if total == 0:
        return f"No results found for: {keyword}\n可能原因: 登录态过期、触发验证码、或搜索结果为空。"
    return "\n".join(lines)


def _search_kb_impl(query: str, top_k: int = 5) -> str:
    """implement search_kb tool - search local knowledge base"""
    from src.kb_agent.searcher import search, format_results

    results = search(query, top_k=top_k)
    return format_results(results, query)


def _search_images_impl(query: str, top_k: int = 5) -> str:
    """implement search_images tool - search images by text"""
    from src.kb_agent.searcher import search_images, format_image_results

    results = search_images(query, top_k=top_k)
    return format_image_results(results, query)


def _ask_kb_impl(question: str, top_k: int = 5) -> str:
    """implement ask_kb tool — RAG QA over knowledge base"""
    from src.kb_agent.qa import answer_question

    result = answer_question(question, top_k=top_k)
    lines = [f"## 回答\n\n{result['answer']}"]
    if result["sources"]:
        lines.append("\n### 来源")
        for s in result["sources"]:
            lines.append(f"- {s['title']} (相关度: {s['score']:.0%})")
    return "\n".join(lines)


# ── Timeout constants ─────────────────────────────────────────────

TOOL_TIMEOUTS = {
    "search_kb": 30,       # local search should be fast
    "search_images": 30,   # image search — same as text search
    "ask_kb": 60,          # QA — search + LLM call
    "search_xhs": 90,      # browser + network + possible captcha
    "run_pipeline": 600,   # full pipeline: search + scrape + OCR + format + classify + build
}

# ── MCP tool registration ────────────────────────────────────────


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="run_pipeline",
            description="Search Xiaohongshu by keyword, scrape posts, OCR images, AI classify, and build local Markdown knowledge base. Full pipeline.",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Search keyword (Chinese or English)",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of posts to scrape (default: 5)",
                        "default": 5,
                    },
                },
                "required": ["keyword"],
            },
        ),
        Tool(
            name="search_xhs",
            description="Search Xiaohongshu by keyword, return post titles, URLs and authors.",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Search keyword",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of results (default: 5)",
                        "default": 5,
                    },
                },
                "required": ["keyword"],
            },
        ),
        Tool(
            name="search_kb",
            description="Search local knowledge base using hybrid (keyword + semantic) search. Returns relevant documents with content and scores.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search query",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Max results to return (default: 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="ask_kb",
            description="Ask a question and get an answer based on the local knowledge base. Uses RAG: search → retrieve relevant documents → generate answer with LLM. Returns answer with source attribution.",
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Question to answer based on the knowledge base",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of documents to retrieve (default: 5)",
                        "default": 5,
                    },
                },
                "required": ["question"],
            },
        ),
        Tool(
            name="search_images",
            description="Search for images in the local knowledge base by text query. Uses OCR text and surrounding context to find matching images. Returns image paths with source post info.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query to find matching images",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Max images to return (default: 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    import time as _time

    t0 = _time.time()
    timeout = TOOL_TIMEOUTS.get(name, 60)

    _mcp_log(f"CALL {name} | args={json.dumps(arguments, ensure_ascii=False)}")

    loop = asyncio.get_running_loop()

    if name == "run_pipeline":
        keyword = arguments.get("keyword", "")
        count = arguments.get("count", 5)
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _run_pipeline_impl, keyword, count),
            timeout=timeout,
        )
    elif name == "search_xhs":
        keyword = arguments.get("keyword", "")
        count = arguments.get("count", 5)
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _search_xhs_impl, keyword, count),
            timeout=timeout,
        )
    elif name == "search_kb":
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 5)
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _search_kb_impl, query, top_k),
            timeout=timeout,
        )
    elif name == "search_images":
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 5)
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _search_images_impl, query, top_k),
            timeout=timeout,
        )
    elif name == "ask_kb":
        question = arguments.get("question", "")
        top_k = arguments.get("top_k", 5)
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _ask_kb_impl, question, top_k),
            timeout=timeout,
        )
    else:
        result = f"Unknown tool: {name}"

    elapsed = _time.time() - t0
    _mcp_log(f"OK   {name} | {elapsed:.1f}s | result={len(result)} chars")
    return [TextContent(type="text", text=result)]


# ── Main ─────────────────────────────────────────────────────────


async def main():
    # preload embedding model in the default thread pool to avoid cross-thread deadlock
    loop = asyncio.get_running_loop()
    _mcp_log("START MCP Server")
    try:
        def _preload():
            from src.kb_agent.indexer import _get_embedder
            e = _get_embedder()
            return f"dim={e.get_sentence_embedding_dimension()}"
        result = await asyncio.wait_for(loop.run_in_executor(None, _preload), timeout=30)
        _mcp_log(f"Embedding model preloaded OK ({result})")
    except Exception as e:
        _mcp_log(f"Embedding preload failed (non-fatal): {e}")

    async with stdio_server() as (reader, writer):
        await server.run(
            reader,
            writer,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
