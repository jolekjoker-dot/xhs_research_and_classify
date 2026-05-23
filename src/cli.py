# Fix PaddleOCR protobuf compatibility (must precede all other imports)
import os as _os
_os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

import argparse
import json
import sys
import time
from pathlib import Path

from src.config import get_config
from src.logger import add_file_handler, get_logger, ProgressTracker
from src.models import ClassifiedPost, SearchResult, XHSPost


def cmd_search(args: argparse.Namespace) -> None:
    """搜索关键词"""
    from src.search.searcher import search, search_batch

    log = get_logger("xhs.search")
    add_file_handler(log)
    tracker = ProgressTracker(log)

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    count = args.count
    headless = not args.no_headless

    tracker.step_start(f"search: {keywords}, count={count}")
    all_results = search_batch(keywords, count_per=count, headless=headless)

    for kw, results in all_results.items():
        log.info("%s: %d results", kw, len(results))

    output_file = args.output or "output/search_results.json"
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    data = {
        kw: [r.__dict__ for r in results] for kw, results in all_results.items()
    }
    Path(output_file).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    tracker.step_end("search", f"saved to {output_file}")


def cmd_scrape(args: argparse.Namespace) -> None:
    """抓取帖子内容"""
    from src.scrape.scraper import scrape_posts

    log = get_logger("xhs.scrape")
    add_file_handler(log)
    tracker = ProgressTracker(log)

    headless = not args.no_headless
    resume = not args.no_resume
    keyword = args.keyword or ""

    input_data = json.loads(Path(args.input).read_text(encoding="utf-8"))

    # support both flat url list and search result dict format
    urls = []
    if isinstance(input_data, dict):
        for kw, items in input_data.items():
            if not keyword:
                keyword = kw  # use first key as keyword
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        urls.append(item.get("url", ""))
                    elif isinstance(item, str):
                        urls.append(item)
        urls = [u for u in urls if u]
    elif isinstance(input_data, list):
        urls = [item if isinstance(item, str) else item.get("url", "") for item in input_data]
        urls = [u for u in urls if u]
    else:
        log.error("Invalid input format")
        return

    tracker.step_start(f"scrape: {len(urls)} urls, keyword={keyword}")
    posts = scrape_posts(urls, keyword=keyword, headless=headless, resume=resume)

    output_file = args.output or "output/scraped_posts.json"
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    data = [p.__dict__ for p in posts]
    Path(output_file).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    tracker.step_end("scrape", f"{len(posts)} posts saved to {output_file}")


def cmd_format(args: argparse.Namespace) -> None:
    """LLM 格式化"""
    from src.classify.formatter import format_posts

    log = get_logger("xhs.format")
    add_file_handler(log)
    tracker = ProgressTracker(log)

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    posts = [XHSPost(**item) for item in raw]

    tracker.step_start(f"format: {len(posts)} posts")
    formatted = format_posts(posts)

    output_file = args.output or args.input  # overwrite by default
    data = [p.__dict__ for p in formatted]
    Path(output_file).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    tracker.step_end("format", f"saved to {output_file}")


def cmd_classify(args: argparse.Namespace) -> None:
    """AI 分类"""
    from src.classify.classifier import classify_posts

    log = get_logger("xhs.classify")
    add_file_handler(log)
    tracker = ProgressTracker(log)

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    posts = [XHSPost(**item) for item in raw]

    tracker.step_start(f"classify: {len(posts)} posts")
    classified = classify_posts(posts)

    output_file = args.output or "output/classified_posts.json"
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    data = []
    for cp in classified:
        d = cp.post.__dict__
        d["category"] = cp.category
        d["sub_category"] = cp.sub_category
        d["summary"] = cp.summary
        d["keywords"] = cp.keywords
        d["entities"] = cp.entities
        d["sentiment"] = cp.sentiment
        d["quality_score"] = cp.quality_score
        data.append(d)
    Path(output_file).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    tracker.step_end("classify", f"saved to {output_file}")


def cmd_ask(args: argparse.Namespace) -> None:
    """RAG 问答"""
    from src.kb_agent.qa import answer_question

    log = get_logger("xhs.ask")
    add_file_handler(log)

    result = answer_question(args.question, top_k=args.top_k)
    print(f"\n{'='*60}")
    print(f"问: {args.question}")
    print(f"{'='*60}\n")
    print(result["answer"])
    if result["sources"]:
        print(f"\n{'─'*60}")
        print("📄 来源:")
        for s in result["sources"]:
            print(f"  - {s['title']} (相关度: {s['score']:.0%})")
    print()

    if args.output:
        output = f"# Q: {args.question}\n\n{result['answer']}\n\n## Sources\n"
        for s in result["sources"]:
            output += f"- [{s['title']}]({s.get('url', s.get('path', ''))})\n"
        Path(args.output).write_text(output, encoding="utf-8")
        log.info("Saved to %s", args.output)


def cmd_search_images(args: argparse.Namespace) -> None:
    """搜索图片"""
    from src.kb_agent.searcher import search_images, format_image_results

    log = get_logger("xhs.imgsearch")
    add_file_handler(log)

    results = search_images(args.query, top_k=args.count)
    output = format_image_results(results, args.query)
    print(output)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        log.info("Saved to %s", args.output)


def cmd_build(args: argparse.Namespace) -> None:
    """构建知识库"""
    from src.knowledge_base.builder import build_knowledge_base

    log = get_logger("xhs.build")
    add_file_handler(log)
    tracker = ProgressTracker(log)

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    posts = []
    for item in raw:
        post = XHSPost(
            url=item["url"], post_id=item["post_id"], title=item.get("title", ""),
            content=item.get("content", ""), author_name=item.get("author_name", ""),
            author_id=item.get("author_id", ""),
            like_count=item.get("like_count", 0),
            collect_count=item.get("collect_count", 0),
            comment_count=item.get("comment_count", 0),
            tags=item.get("tags", []),
            image_urls=item.get("image_urls", []),
            ocr_text=item.get("ocr_text", ""),
        )
        cp = ClassifiedPost(
            post=post,
            category=item.get("category", "未分类"),
            sub_category=item.get("sub_category", ""),
            summary=item.get("summary", ""),
            keywords=item.get("keywords", []),
            entities=item.get("entities", []),
            sentiment=item.get("sentiment", "neutral"),
            quality_score=item.get("quality_score", 0),
        )
        posts.append(cp)

    tracker.step_start(f"build kb: {len(posts)} posts")
    root = build_knowledge_base(posts)
    tracker.step_end("build", f"KB at {root}")


def cmd_run(args: argparse.Namespace) -> None:
    """完整 Workflow: search → scrape ⇢ format ⇢ classify → build (streaming pipeline)"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from src.search.searcher import search_batch
    from src.scrape.scraper import scrape_posts_parallel
    from src.classify.classifier import classify_post
    from src.classify.formatter import format_content
    from src.knowledge_base.builder import build_knowledge_base

    log = get_logger("xhs.run")
    add_file_handler(log)
    tracker = ProgressTracker(log)

    config = get_config()
    llm_workers = config.llm_max_workers
    scrape_workers = config.scrape_max_concurrent

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    count = args.count
    headless = not args.no_headless
    resume = not args.no_resume

    # Phase 1: Search
    tracker.step_start(f"[1/5] Search: {keywords}")
    all_results = search_batch(keywords, count_per=count, headless=headless)
    total_results = sum(len(v) for v in all_results.values())
    tracker.step_end("search", f"{total_results} results")

    all_urls: list[str] = []
    for results in all_results.values():
        for r in results:
            all_urls.append(r.url)
    kw = keywords[0] if keywords else ""

    if not all_urls:
        log.error("No search results, aborting")
        return

    # ── Streaming pipeline ──────────────────────────────────────

    tracked_posts: list = []
    t0 = time.time()

    log.info("[2/5] Pipeline: scrape(%d workers) ⇢ format(%d workers) ⇢ classify(%d workers)",
             scrape_workers, llm_workers, llm_workers)
    log.info("Processing %d urls...", len(all_urls))

    with ThreadPoolExecutor(max_workers=llm_workers) as format_exec:
        with ThreadPoolExecutor(max_workers=llm_workers) as classify_exec:

            # Scrape in parallel → stream into format executor
            scraped = scrape_posts_parallel(
                all_urls, keyword=kw, headless=headless, resume=resume,
                max_concurrent=scrape_workers,
            )

            if not scraped:
                log.error("No posts scraped, aborting")
                return

            # Submit all for formatting
            fmt_futures = {format_exec.submit(format_content, p): p for p in scraped}
            formatted_posts = []
            for ff in as_completed(fmt_futures):
                try:
                    formatted_posts.append(ff.result())
                except Exception:
                    log.exception("Format failed")
                    formatted_posts.append(fmt_futures[ff])

            # Submit formatted for classification
            cls_futures = {classify_exec.submit(classify_post, p): p for p in formatted_posts}
            classified = []
            for cf in as_completed(cls_futures):
                try:
                    classified.append(cf.result())
                except Exception:
                    log.exception("Classify failed")

    elapsed = time.time() - t0
    log.info("Pipeline complete: %d posts in %.1fs", len(classified), elapsed)

    if not classified:
        log.error("No posts classified, aborting")
        return

    # Phase 4: Build
    tracker.step_start(f"[4/5] Build knowledge base")
    root = build_knowledge_base(classified)
    tracker.step_end("build", f"KB at {root}")

    # Phase 5: Knowledge graph
    tracker.step_start(f"[5/5] Build knowledge graph")
    try:
        from src.knowledge_base.graph import KnowledgeGraph
        graph = KnowledgeGraph()
        graph.build(classified)
        viz_path = graph.export_for_viz()
        graph.close()
        tracker.step_end("graph", f"Graph exported → {viz_path}")
    except Exception:
        log.exception("Knowledge graph build failed (non-fatal)")
        tracker.step_end("graph", "skipped (error)")

    # Phase 6: Image index
    if config.image_search_enabled:
        tracker.step_start(f"[6/6] Build image index")
        try:
            from src.kb_agent.image_indexer import build_image_index
            n = build_image_index()
            tracker.step_end("img_idx", f"{n} images indexed")
        except Exception:
            log.exception("Image index build failed (non-fatal)")
            tracker.step_end("img_idx", "skipped (error)")
    else:
        log.info("[6/6] Image index skipped (disabled)")

    log.info("Workflow complete! Open %s/INDEX.md or %s/graph.html", root, root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xhs-kb",
        description="xiaohongshu knowledge base workflow",
    )
    sub = parser.add_subparsers(dest="command")

    p_search = sub.add_parser("search", help="search xiaohongshu by keywords")
    p_search.add_argument("keywords", help="search keywords (comma-separated)")
    p_search.add_argument("--count", type=int, default=20)
    p_search.add_argument("--sort", default="general")
    p_search.add_argument("--output", default=None)
    p_search.add_argument("--no-headless", action="store_true", help="show browser window")

    p_scrape = sub.add_parser("scrape", help="scrape posts from url list")
    p_scrape.add_argument("--input", required=True, help="json file with urls (from search output)")
    p_scrape.add_argument("--keyword", default=None, help="search keyword (helps find posts)")
    p_scrape.add_argument("--output", default=None)
    p_scrape.add_argument("--no-headless", action="store_true", help="show browser window")
    p_scrape.add_argument("--no-resume", action="store_true", help="ignore checkpoint, re-scrape all")

    p_format = sub.add_parser("format", help="format scraped content with LLM")
    p_format.add_argument("--input", required=True, help="json file with scraped posts")
    p_format.add_argument("--output", default=None)

    p_classify = sub.add_parser("classify", help="classify scraped posts")
    p_classify.add_argument("--input", required=True, help="json file with posts")
    p_classify.add_argument("--output", default=None)

    p_build = sub.add_parser("build", help="build knowledge base from posts")
    p_build.add_argument("--input", required=True, help="json file with classified posts")
    p_build.add_argument("--output", default=None)

    p_img = sub.add_parser("search-images", help="search images by text query")
    p_img.add_argument("query", help="search query (Chinese or English)")
    p_img.add_argument("--count", type=int, default=5)
    p_img.add_argument("--output", default=None)

    p_ask = sub.add_parser("ask", help="ask a question using RAG over the knowledge base")
    p_ask.add_argument("question", help="question to answer (Chinese or English)")
    p_ask.add_argument("--top-k", type=int, default=5)
    p_ask.add_argument("--output", default=None)

    p_run = sub.add_parser("run", help="run full workflow: search -> scrape -> classify -> build")
    p_run.add_argument("--keywords", required=True, help="search keywords (comma-separated)")
    p_run.add_argument("--count", type=int, default=20)
    p_run.add_argument("--no-headless", action="store_true", help="show browser window")
    p_run.add_argument("--no-resume", action="store_true", help="ignore checkpoint, re-scrape all")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return

    handlers = {
        "search": cmd_search,
        "scrape": cmd_scrape,
        "format": cmd_format,
        "classify": cmd_classify,
        "build": cmd_build,
        "search-images": cmd_search_images,
        "ask": cmd_ask,
        "run": cmd_run,
    }
    handler = handlers.get(args.command)
    if handler:
        handler(args)

    # cleanup prompt
    _cleanup_prompt()


def _cleanup_prompt() -> None:
    """ask user if debug/temp files should be cleaned up"""
    import glob as _glob
    temp_patterns = [
        "output/debug_*",
        "output/note_raw_*",
        "output/ocr_result.txt",
        "output/note_content.txt",
    ]
    temp_files = []
    for pat in temp_patterns:
        temp_files.extend(_glob.glob(pat, recursive=False))

    if not temp_files:
        return

    print(f"\n发现 {len(temp_files)} 个调试/临时文件:")
    for f in temp_files:
        size = Path(f).stat().st_size
        print(f"  {f} ({size} bytes)")

    try:
        answer = input("是否删除这些调试文件？[y/N] ").strip().lower()
        if answer in ("y", "yes"):
            for f in temp_files:
                Path(f).unlink(missing_ok=True)
            print(f"已删除 {len(temp_files)} 个文件。")
        else:
            print("保留调试文件。")
    except (EOFError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    main()