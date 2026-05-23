"""Web UI server — single-page app with API endpoints for all KB features

Usage:
    python webui/server.py
    # or: python webui/server.py --port 8080

Then open http://localhost:8080 in browser.
"""

import json
import os as _os
import sys
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
_os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")


class APIHandler(SimpleHTTPRequestHandler):
    """Serves static files from project root + API endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path.startswith("/api/"):
            self._handle_api_get(parsed)
        elif parsed.path == "/" or parsed.path == "":
            self.path = "/webui/index.html"
            super().do_GET()
        elif parsed.path == "/graph.html":
            self.path = "/output/knowledge_base/graph.html"
            super().do_GET()
        elif parsed.path == "/graph_viz.json":
            self.path = "/output/knowledge_base/graph_viz.json"
            super().do_GET()
        elif parsed.path.startswith("/images/"):
            self.path = "/output/knowledge_base" + parsed.path
            super().do_GET()
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path.startswith("/api/"):
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
            try:
                params = json.loads(body)
            except json.JSONDecodeError:
                params = {}
            self._handle_api_post(parsed, params)
        else:
            super().do_POST()

    def _handle_api_get(self, parsed):
        qs = parse_qs(parsed.query)
        path = parsed.path

        try:
            if path == "/api/search":
                result = self._search(qs)
            elif path == "/api/ask":
                result = self._ask(qs)
            elif path == "/api/search-images":
                result = self._search_images(qs)
            elif path == "/api/doc":
                result = self._doc(qs)
            else:
                self._json({"error": "Unknown endpoint"}, 404)
                return
            self._json(result)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _handle_api_post(self, parsed, params):
        path = parsed.path

        try:
            if path == "/api/pipeline":
                result = self._pipeline(params)
            else:
                self._json({"error": "Unknown endpoint"}, 404)
                return
            self._json(result)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # ── API implementations ─────────────────────────────────

    def _search(self, qs):
        query = qs.get("query", [""])[0]
        top_k = int(qs.get("top_k", ["5"])[0])
        from src.kb_agent.searcher import search, format_results
        results = search(query, top_k=top_k)
        return {
            "results": [{
                "title": r.get("title", ""),
                "path": r.get("path", ""),
                "score": r.get("score", 0),
                "content": r.get("content", "")[:300],
                "category": r.get("category", ""),
                "url": r.get("url", ""),
                "method": r.get("method", ""),
            } for r in results],
            "markdown": format_results(results, query),
        }

    def _ask(self, qs):
        question = qs.get("question", [""])[0]
        top_k = int(qs.get("top_k", ["5"])[0])
        from src.kb_agent.qa import answer_question
        result = answer_question(question, top_k=top_k)
        return {
            "question": question,
            "answer": result["answer"],
            "sources": [{
                "title": s["title"],
                "score": s["score"],
                "url": s.get("url", ""),
            } for s in result["sources"]],
        }

    def _search_images(self, qs):
        query = qs.get("query", [""])[0]
        top_k = int(qs.get("top_k", ["5"])[0])
        from src.kb_agent.searcher import search_images
        results = search_images(query, top_k=top_k)
        return {
            "results": [{
                "image_path": r.get("image_path", ""),
                "kb_path": r.get("kb_path", ""),
                "score": r.get("score", 0),
                "post_title": r.get("post_title", ""),
                "category": r.get("category", ""),
                "content": r.get("content", "")[:200],
            } for r in results],
        }

    def _pipeline(self, params):
        keyword = params.get("keyword", "")
        count = params.get("count", 3)
        from src.search.searcher import search_batch
        all_results = search_batch([keyword], count_per=count, headless=True)
        total = sum(len(v) for v in all_results.values())
        return {"keyword": keyword, "count": count, "results_found": total}

    def _doc(self, qs):
        from urllib.parse import unquote
        path = unquote(qs.get("path", [""])[0])
        if not path:
            return {"error": "missing path"}
        # normalize path separators (frontend sends forward slashes)
        path = path.replace("/", "\\")
        p = PROJECT_ROOT / path
        if not p.exists():
            return {"error": f"file not found: {path}"}
        # safety: ensure resolved path is within project
        if not str(p.resolve()).startswith(str(PROJECT_ROOT.resolve())):
            return {"error": "path outside project"}
        text = p.read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("---", 3)
            if end > 0:
                text = text[end + 3:]
        # Extract only: 摘要, 正文, 关键信息 sections
        sections = []
        for keyword in ["## 摘要", "## 正文", "## 图片提取文字", "## 关键信息"]:
            idx = text.find(keyword)
            if idx >= 0:
                # extract from this heading to the next ## heading
                chunk = text[idx:]
                next_h2 = chunk.find("\n## ", len(keyword) + 1)
                if next_h2 > 0:
                    chunk = chunk[:next_h2]
                sections.append(chunk.strip())
        content = "\n\n".join(sections) if sections else text.strip()
        return {"content": content, "path": path}

    # ── helpers ────────────────────────────────────────────

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # suppress default logging noise
        pass


def main():
    port = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--port" else 8080

    server = HTTPServer(("0.0.0.0", port), APIHandler)
    print(f"\n  Web UI: http://localhost:{port}\n")
    print("  Features:")
    print("    [主页]   Keyword search + pipeline trigger")
    print("    [问答]   RAG QA over knowledge base")
    print("    [图谱]   Knowledge graph visualization")
    print("    [搜图]   Text-to-image search\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
