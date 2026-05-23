# 小红书知识库 Workflow

搜索小红书 → 抓取正文/图片 → OCR → LLM 格式化 → AI 分类 → 本地 Markdown 知识库
→ RAG 检索 → 问答 → 知识图谱 → 文搜图 → Web UI

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt
pip install paddlepaddle paddleocr
playwright install chromium

# 配置 API Key（复制 .env.example 为 .env 并填入密钥）
# 支持 DeepSeek / 小米 mimo-v2.5 等任何 OpenAI 兼容接口
ANTHROPIC_PROVIDER=xiaomi  # 或 deepseek

# 启动 Web UI
python webui/server.py
# 浏览器打开 http://localhost:8080

# 一键搜索并构建知识库
python xiaohongshu.py run --keywords "agent面试" --count 5
```

## Web UI

```bash
python webui/server.py  # → http://localhost:8080
```

四 Tab 布局：主页（搜索+抓取）、RAG 问答、知识图谱（ECharts）、文搜图。支持浅色主题、搜索结果展开本地文档、5 种检索方式切换、全链路追踪。

## CLI 命令

| 命令 | 说明 |
|------|------|
| `python xiaohongshu.py run --keywords "词" --count 5` | 全流程：搜索→抓取→格式化→分类→构建 |
| `python xiaohongshu.py search "关键词" --count 10` | 仅搜索 |
| `python xiaohongshu.py scrape --input search_results.json` | 仅抓取 |
| `python xiaohongshu.py ask "问题"` | RAG 问答 |
| `python xiaohongshu.py search-images "描述"` | 文搜图 |
| `python webui/server.py` | 启动 Web UI |

### 常用参数

| 参数 | 说明 |
|------|------|
| `--count N` | 数量（默认 20） |
| `--no-headless` | 显示浏览器（登录/过验证码） |
| `--no-resume` | 忽略断点，重抓全部 |
| `--keywords "A,B,C"` | 多关键词 |

## 首次使用

```bash
python xiaohongshu.py search "测试" --count 1 --no-headless
```

扫码登录，登录态保存在 `~/.xhs_browser_profile/`。

## API Provider 配置

通过 `.env` 文件切换 LLM provider（复制 `.env.example`）：

```ini
ANTHROPIC_PROVIDER=xiaomi   # 或 deepseek
ANTHROPIC_BASE_URL=https://token-plan-cn.xiaomimimo.com/anthropic/v1
ANTHROPIC_MODEL=mimo-v2.5
XIAOMI_API_KEY=你的密钥
```

支持任何 OpenAI 兼容接口。

## Pipeline

```
搜索 → 抓取(并行2worker) → 格式化(5worker) → 分类(5worker) → 构建MD
→ 构建知识图谱 → 导出可视化JSON → 构建图片索引
```

## 知识库输出

```
output/knowledge_base/
├── INDEX.md                 # 总索引
├── metadata.json            # 统计数据
├── graph.html               # ECharts 知识图谱
├── graph_viz.json           # 图谱数据
├── categories/
│   ├── 技术编程/_index.md
│   └── 职业发展/_index.md
└── images/*.jpg
```

## 检索

支持 5 种检索方式，可在 Web UI 下拉切换：

| 方式 | 特点 |
|------|------|
| 关键词匹配 | 速度快，精确匹配 |
| 语义搜索 | GTE embedding 中文语义 |
| 混合 RRF | 关键词+语义双路融合 |
| RRF + Cross-Encoder 重排 | bge-reranker-v2-m3 精排 |
| 语义 + Cross-Encoder 重排 | 综合最优（默认） |

### 向量索引

```bash
# 增量索引（默认）
python -c "from src.kb_agent.indexer import build_index; build_index()"

# 强制重建
python -c "from src.kb_agent.indexer import build_index; build_index(rebuild=True)"

# 图片索引
python -c "from src.kb_agent.image_indexer import build_image_index; build_image_index()"
```

## RAG 问答

```bash
python xiaohongshu.py ask "字节跳动的Agent面试主要考什么？"
```

返回结构化答案 + 来源引用。Web UI 支持追踪面板：检索每步耗时、LLM 上下文/输出字数。

## 文搜图

```bash
python xiaohongshu.py search-images "Agent架构图"
```

OCR 文字 → 图片索引，文字搜出匹配图片。

## 知识图谱

Neo4j（可选）+ ECharts 力导向图。支持 Cypher 多跳查询、实体关联、主题聚类。

```bash
docker compose up -d  # 可选，Neo4j
```

Web UI 直接嵌入图谱页面，支持搜索、拖拽、分类着色。

## 评测

```bash
python -m pytest tests/test_retrieval.py -v -s
```

输出 5 个 query 的 Recall@5、MRR、nDCG@5 对比。

## MCP Server

`mcp_server/xhs_server.py`，5 个 Tool：

| Tool | 说明 |
|------|------|
| `run_pipeline(keyword, count)` | 全流程采集 |
| `search_xhs(keyword, count)` | 搜索小红书 |
| `search_kb(query, top_k)` | 搜索本地知识库 |
| `search_images(query, top_k)` | 文搜图 |
| `ask_kb(question, top_k)` | RAG 问答 |

## 测试

```bash
python -m pytest tests/ -v
```

## 项目结构

```
find_knowledge/
├── xiaohongshu.py              # CLI 入口
├── webui/                      # Web UI
│   ├── server.py               # HTTP server + API
│   └── index.html              # 前端（浅色主题）
├── src/
│   ├── config.py               # 配置 + provider
│   ├── cli.py                  # CLI 命令
│   ├── search/searcher.py      # Playwright 搜索
│   ├── scrape/
│   │   ├── scraper.py          # 抓取（并行）
│   │   └── ocr.py              # PaddleOCR
│   ├── classify/
│   │   ├── formatter.py        # LLM 格式化（缓存）
│   │   └── classifier.py       # AI 分类（缓存）
│   ├── knowledge_base/
│   │   ├── builder.py          # Markdown 构建
│   │   └── graph.py            # Neo4j 知识图谱
│   └── kb_agent/
│       ├── indexer.py          # ChromaDB 向量索引
│       ├── image_indexer.py    # 图片索引
│       ├── rag_engine.py       # RRF + 重排 + 图片搜索
│       ├── searcher.py         # 检索协调
│       ├── reranker.py         # Cross-Encoder 重排
│       ├── qa.py               # RAG 问答
│       └── tracer.py           # 全链路追踪
├── mcp_server/xhs_server.py    # MCP Server
├── config/categories.yaml     # 分类体系
├── docker-compose.yml         # Neo4j
├── tests/
└── output/
    ├── chroma_db/              # 向量数据库
    └── knowledge_base/         # 知识库输出
```
