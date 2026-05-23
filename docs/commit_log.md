# Commit 记录

用于快速定位回退目标。每个 commit 记录改动范围、影响文件、回退说明。

---

## 2026-05-22

### `2b38c62` feat: P9 — search method dropdown (5 methods with labels)

搜索和问答新增检索方式下拉框，可选：关键词/语义/RRF/RRF+重排/语义+重排，各有标注。

**影响文件**：`webui/{server,index}.py`、`src/kb_agent/qa.py`、`docs/`

**回退**：`git reset --hard 2b38c62~1`

---

### `e449618` fix: move pipeline section below project intro

将"抓取小红书"移到左栏介绍下方，修复白底卡片样式。

**回退**：`git reset --hard e449618~1`

---

### `602cc98` feat: Web UI light theme + home page split layout

浅色主题 + 对角条纹背景 + 主页左右分栏。

**回退**：`git reset --hard 602cc98~1`

---

### `5db403e` chore: snapshot — Web UI, reclassify fix, docs updates

全量快照，含多个未提交修改。

**回退**：`git reset --hard 5db403e~1`

---

### `30ba759` feat: Web UI — local MD document viewer + lightbox fix

搜索结果点击查看本地 MD 文档（摘要/正文/图片文字/关键信息），修复灯箱关闭问题。

**回退**：`git reset --hard 30ba759~1`

---

### `2ba09c2` feat: Web UI polish — markdown rendering + image lightbox

marked.js 渲染 RAG 答案 Markdown，图片点击放大灯箱。

**回退**：`git reset --hard 2ba09c2~1`

---

### `cda67a1` feat: P8 — Web UI (4-tab layout: search, QA, graph, image search)

**改动范围**：Web UI 可视化界面（3 个文件）

**影响文件**：`webui/server.py`（新增）、`webui/index.html`（新增）、`docs/`

**回退**：`git reset --hard cda67a1~1`

---

### `d0106f2` feat: P7 — RAG QA system (search + answer + sources)

**改动范围**：RAG 问答（5 个文件）

**影响文件**：`src/kb_agent/qa.py`（新增）、`src/cli.py`、`mcp_server/xhs_server.py`、`docs/`

**回退**：`git reset --hard d0106f2~1`

---

### `d6f437c` feat: P5 — text-to-image search

**改动范围**：文搜图功能（8 个文件）

**影响文件**：`src/kb_agent/image_indexer.py`（新增）、`rag_engine.py`、`searcher.py`、`cli.py`、`config.py`、`mcp_server/xhs_server.py`、`docs/`

**回退**：`git reset --hard d6f437c~1`

---

### `c727c4a` feat: P4 — evaluation script + annotated labels + sem+rerank baseline

**改动范围**：检索评测框架 + 5 个标注 query + sem+rerank 对比维度

**影响文件**（3 个）：`tests/test_retrieval.py`（新增）、`docs/optimization_plan.md`、`docs/commit_log.md`

**回退**：`git reset --hard c727c4a~1`

---

### `4e46de3` feat: P3 — RRF fusion + bge-reranker + search parallelization

**改动范围**：检索链路升级（RRF 融合 + Cross-Encoder 重排 + 并行搜索）

**影响文件**（4 个）：`src/kb_agent/rag_engine.py`（RRF+并行+重排调用）、`src/kb_agent/reranker.py`（新增 Cross-Encoder）、`src/kb_agent/searcher.py`（config 读取）、`src/config.py`（rerank_enabled）

**回退**：`git reset --hard 4e46de3~1`

---

### `1dcfa1a` feat: P2 + Xiaomi API provider + retrieval rerank plan

**改动范围**：Pipeline 流式 + LLM 缓存 + 小米 provider + .env + 重排方案

**影响文件**（9 个）：src/classify/{formatter,classifier}.py（缓存+推理模型支持）, src/cli.py（流式 pipeline）, src/config.py（provider 抽象+dotenv）, src/scrape/{scraper,ocr}.py, .gitignore

**回退**：`git reset --hard 1dcfa1a~1`

---

### `6ad41d4` feat: P1 optimization — OCR batch inference + knowledge graph (Neo4j + ECharts)

**改动范围**：OCR 批处理 + 知识图谱（Neo4j Cypher 查询 + ECharts 可视化）

**影响文件**（7 个）：

| 文件 | 改动 |
|------|------|
| `src/scrape/ocr.py` | `ocr_images()` 改为一次传列表 `ocr([paths...])` batch 推理，异常时降级逐张 |
| `src/knowledge_base/graph.py`（新） | KnowledgeGraph 类：Neo4j build/search/find_related/entity_network，无 Neo4j 时 fallback 解析 MD |
| `src/config.py` | 新增 `neo4j_uri/user/password` 配置项 |
| `src/cli.py` | pipeline 新增第 6 步：构建知识图谱 + 导出可视化 JSON |
| `docker-compose.yml`（新） | Neo4j 5 Community 一键启动 |
| `output/knowledge_base/graph.html`（新） | ECharts 力导向图，支持搜索/拖拽/分类着色 |
| `docs/commit_log.md`（新） | commit 记录文档 |

**回退命令**：
```bash
git reset --hard 6ad41d4~1   # 回到 P1 之前（保留 P0）
```

---

### `13fbf70` feat: P0 optimization — LLM parallelization + ChromaDB incremental indexing

**改动范围**：LLM 调用并发 + 向量索引增量

**影响文件**（6 个）：

| 文件 | 改动 |
|------|------|
| `src/classify/formatter.py` | `format_posts()` 从 `for` 循环改为 `ThreadPoolExecutor`，默认 5 worker |
| `src/classify/classifier.py` | `classify_posts()` 同上，worker 异常时降级为 fallback 分类 |
| `src/config.py` | 新增 `llm_max_workers: int = 5` |
| `src/cli.py` | `cmd_run()` 从 config 读取 `llm_max_workers` 并传入 |
| `src/kb_agent/indexer.py` | `build_index()` 改为增量模式（默认跳过已有 chunk），新增 `rebuild` 参数，返回类型从 `int` 改为 `dict` |
| `docs/optimization_plan.md` | 新增优化方案文档 |

**回退命令**：
```bash
git revert 13fbf70           # 保留历史，生成反向提交
# 或
git reset --hard 13fbf70~1   # 直接回退到 P0 之前（会丢失 P0 之后的提交）
```

---

### `f96bab3` chore: checkpoint before optimization refactoring

**改动范围**：全量快照——知识库数据（8 篇新帖子 + 对应图片）、搜索缓存、运行时日志

**影响文件**（47 个）：全部在 `output/` 和根目录 `简历_问答.md`

**回退命令**：
```bash
git reset --hard f96bab3   # 回到优化开始前的干净状态
```

---

### `f02e87a` first commit

**改动范围**：项目初始代码

**回退命令**：
```bash
git reset --hard f02e87a   # 回到最初
```
