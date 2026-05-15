# 小红书知识库 Workflow

根据关键词搜索小红书内容 → 抓取正文/图片 → PaddleOCR 识别 → LLM 格式化 → AI 分类 → 构建本地 Markdown 知识库。

## 快速开始

```bash
# 安装依赖
pip install paddlepaddle paddleocr openai pyyaml httpx playwright Pillow chromadb jieba modelscope sentence-transformers

# 安装 Playwright 浏览器（或使用系统 Chrome）
playwright install chromium

# 一键搜索并构建知识库
python xiaohongshu.py run --keywords "agent面试" --count 5
```

## CLI 命令

| 命令 | 说明 |
|------|------|
| `python xiaohongshu.py run --keywords "关键词" --count 5` | **全流程**：搜索→抓取→格式化→分类→构建 |
| `python xiaohongshu.py search "关键词" --count 10` | 仅搜索，结果存入 `output/search_results.json` |
| `python xiaohongshu.py scrape --input output/search_results.json` | 仅抓取 |
| `python xiaohongshu.py format --input output/scraped_posts.json` | 仅格式化 |
| `python xiaohongshu.py classify --input output/scraped_posts.json` | 仅分类 |
| `python xiaohongshu.py build --input output/classified_posts.json` | 仅构建知识库 |

### 常用参数

| 参数 | 说明 |
|------|------|
| `--count N` | 抓取数量（默认 20） |
| `--no-headless` | 显示浏览器窗口（首次登录/过验证码时使用） |
| `--no-resume` | 忽略断点缓存，重抓全部 |
| `--keywords "A,B,C"` | 多关键词，逗号分隔 |

## 首次使用

需要登录小红书：

```bash
python xiaohongshu.py search "测试" --count 1 --no-headless
```

在打开的浏览器中扫码登录，登录态保存在 `~/.xhs_browser_profile/`。之后可正常使用 headless 模式。

遇到验证码时同样用 `--no-headless` 手动过验证。

## Pipeline

```
Search → Scrape → Format → Classify → Build
  6s      90s      20s       15s       1s

  搜索     模拟      LLM      DeepSeek   Markdown
  结果     点击      修复      分类      知识库
          抓取      OCR错字   摘要      输出
          OCR      结构化    关键词
```

## 知识库输出

```
output/knowledge_base/
├── INDEX.md                    # 总索引（按分类/关键词/时间）
├── metadata.json               # 统计信息
├── categories/
│   ├── 技术编程/
│   │   ├── _index.md           # 分类索引
│   │   └── 帖子标题_xxx.md     # 单篇文档
│   └── 职业发展/
│       └── ...
└── images/
    └── *.jpg                   # 帖子图片
```

单篇文档包含：YAML frontmatter + AI 摘要 + 格式化正文 + 图片提取文字 + 图片链接 + 实体 + 原文链接。

## 本地知识库检索

```bash
# 构建向量索引
python -c "from src.kb_agent.indexer import build_index; build_index()"

# 搜索
python -c "
from src.kb_agent.searcher import search, format_results
from pathlib import Path
results = search('你感兴趣的关键词', top_k=5)
Path('output/search_demo.md').write_text(format_results(results, '你的查询'))
print(f'找到 {len(results)} 条 → output/search_demo.md')
"
```

检索支持三种模式：
- **关键词匹配**：基于 frontmatter 的 `tags` + `keywords` 字段，使用 jieba 中文分词
- **语义搜索**：ChromaDB 向量相似度（512 维，ModelScope 中文模型）
- **混合检索**：关键词 + 语义融合排序

## MCP Server

项目已封装为 MCP Server（`mcp_server/xhs_server.py`），注册为 `xhs-kb`。

三个 MCP Tool：

| Tool | 说明 |
|------|------|
| `run_pipeline(keyword, count)` | 全流程采集 |
| `search_xhs(keyword, count)` | 搜索小红书 |
| `search_kb(query, top_k)` | 搜索本地知识库 |

Claude Code 对话中直接使用（自然语言，Claude 自动判断调用哪个 MCP）：

| 你对 Claude 说的话 | 自动调用的 MCP Tool | 等效 CLI 命令 |
|------|------|------|
| "搜索小红书 agent 面试的内容，抓 10 篇" | `run_pipeline(keyword="agent面试", count=10)` | `python xiaohongshu.py run --keywords "agent面试" --count 10` |
| "帮我在小红书找一下 Python 性能优化的帖子，抓 3 篇" | `run_pipeline(keyword="Python性能优化", count=3)` | `python xiaohongshu.py run --keywords "Python性能优化" --count 3` |
| "搜一下小红书大模型面试经验，存到本地知识库" | `run_pipeline(keyword="大模型面试", count=5)` | `python xiaohongshu.py run --keywords "大模型面试" --count 5` |
| "搜一下小红书 Rust 后端面经，抓 5 篇" | `run_pipeline(keyword="Rust后端面经", count=5)` | `python xiaohongshu.py run --keywords "Rust后端面经" --count 5` |
| "帮我查查小红书上有哪些 agent 面试相关的内容" | `search_xhs(keyword="agent面试", count=5)` | `python xiaohongshu.py search "agent面试" --count 5` |
| "小红书上有哪些讲 MCP 协议的帖子？给我搜一下" | `search_xhs(keyword="MCP协议", count=5)` | `python xiaohongshu.py search "MCP协议" --count 5` |
| "本地知识库里有没有关于 RAG 和向量检索的内容？" | `search_kb(query="RAG 向量检索", top_k=5)` | `python -c "from src.kb_agent.searcher import search; ..."` |
| "知识库中有哪些 agent 开发相关的面试题？" | `search_kb(query="agent开发 面试题", top_k=5)` | 同上 |
| "帮我查一下知识库，Embedding 模型怎么选型？" | `search_kb(query="Embedding 选型", top_k=5)` | 同上 |
| "之前抓的帖子里有没有提到 Function Calling 的？" | `search_kb(query="Function Calling", top_k=5)` | 同上 |
| "帮我找找知识库里关于多 Agent 协作的内容" | `search_kb(query="多Agent 协作", top_k=5)` | 同上 |
| "查一下本地有没有关于 DPO 训练和梯度爆炸的内容" | `search_kb(query="DPO 梯度爆炸", top_k=5)` | 同上 |

## 测试

```bash
python -m pytest tests/ -v
```

## 项目结构

```
find_knowledge/
├── xiaohongshu.py              # CLI 入口
├── README.md                   # 本文档
├── implementation_plan.md      # 实施计划与进度
├── requirements.txt
├── src/
│   ├── models.py               # 数据模型
│   ├── config.py               # 配置管理
│   ├── logger.py               # 日志系统
│   ├── cli.py                  # CLI 命令
│   ├── search/searcher.py      # 搜索模块
│   ├── scrape/
│   │   ├── scraper.py          # 抓取模块
│   │   └── ocr.py              # PaddleOCR
│   ├── classify/
│   │   ├── classifier.py       # AI 分类
│   │   └── formatter.py        # LLM 格式化
│   ├── knowledge_base/
│   │   └── builder.py          # 知识库构建
│   └── kb_agent/
│       ├── indexer.py          # 向量索引
│       ├── rag_engine.py       # RAG 引擎
│       └── searcher.py         # 检索协调
├── mcp_server/
│   └── xhs_server.py           # MCP Server
├── agents/
│   └── xhs-knowledge-agent.md  # 子代理定义
├── skills/
│   └── xhs-knowledge/SKILL.md  # /xhs-search 命令
├── config/
│   └── categories.yaml         # 分类体系
├── tests/
│   ├── test_searcher.py
│   ├── test_scraper.py
│   ├── test_classifier.py
│   ├── test_builder.py
│   └── test_rag_engine.py
└── output/
    ├── run.log                 # 累计日志
    ├── chroma_db/              # 向量数据库
    └── knowledge_base/         # 知识库输出
```
