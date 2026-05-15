# 小红书知识库构建 Workflow — 设计与实施文档

## 一、项目概述

### 1.1 目标
构建一个自动化 workflow，实现：
1. 根据关键词在小红书搜索内容
2. 抓取帖子正文、图片、元数据
3. 对内容进行 AI 分类整理
4. 生成本地结构化文档，构建可检索的知识库
5. **封装为 Claude Code 子代理**，可通过 Agent 或 Skill 调用
6. **构建知识库检索 Agent**，基于 RAG 对本地知识库进行语义搜索

### 1.2 核心挑战
| 挑战 | 说明 | 应对策略 |
|------|------|----------|
| 反爬机制 | XHS 有强反爬，普通 HTTP 请求会被封 | 使用 Playwright MCP 模拟真实浏览器 |
| 登录墙 | 大部分内容需要登录后查看 | 浏览器复用已登录的 Profile |
| 动态渲染 | 内容通过 JS 动态加载 | Playwright 等待渲染完成 |
| 内容结构多样化 | 图文、视频、合集等不同形式 | 统一的抽象模型 + 分类型处理器 |
| 速率限制 | 频繁请求会触发风控 | 随机延迟 + 请求间隔控制 |

---

## 二、系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         外部调用入口                                     │
│  ┌──────────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │  CLI 直接调用         │  │  Claude Agent     │  │  Slash Command   │  │
│  │  python xhs_kb.py    │  │  子代理方式调用    │  │  /xhs-search ...  │  │
│  └─────────┬────────────┘  └────────┬─────────┘  └────────┬─────────┘  │
│            └────────────────────────┼─────────────────────┘             │
└─────────────────────────────────────┼───────────────────────────────────┘
                                      │
┌─────────────────────────────────────▼───────────────────────────────────┐
│                         Workflow 编排层（5步）                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │  Search   │→│  Scrape   │→│  Format   │→│ Classify  │→│ Knowledge │  │
│  │  Module   │  │  Module   │  │  Module   │  │  Module   │  │   Base    │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └────┬─────┘  │
└───────────────────────────────────────────────────┼─────────────────────┘
                                                    │
                      ┌─────────────────────────────┼─────────────────────┐
                      │                             ▼                      │
                      │  ┌──────────┐  ┌──────────────────────────────┐  │
                      │  │ 知识库    │  │  KB Agent (RAG 检索)          │  │
                      │  │ Markdown  │←─│  ┌─────────┐ ┌───────────┐  │  │
                      │  │ 文档存储  │  │  │ Vector  │ │ Omega     │  │  │
                      │  └──────────┘  │  │ Store   │ │ Memory    │  │  │
                      │                │  └─────────┘ └───────────┘  │  │
                      │                └──────────────────────────────┘  │
                      │                      知识库消费层                  │
                      └──────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                         MCP / Skills 调用层                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
│  │Playwright│ │ Firecrawl│ │ Filesys  │ │  Omega   │ │  Sequential  │ │
│  │   MCP    │ │   MCP    │ │   MCP    │ │  Memory  │ │  Thinking    │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 模块职责

#### 2.2.1 Search Module（搜索模块）
- **输入**: 关键词列表、搜索数量限制、排序方式
- **输出**: 帖子 URL 列表 + 基础元信息（标题、摘要、作者、发布时间）
- **MCP 依赖**: Playwright MCP（浏览器搜索）
- **实现方式**: 
  - 方案A: Playwright MCP 打开 XHS 搜索页，输入关键词，解析搜索结果列表
  - 方案B: 使用已有的 XHS API（如 xiaohongshu-api 等开源库）

#### 2.2.2 Scrape Module（抓取模块）
- **输入**: 帖子 URL 列表
- **输出**: 结构化内容对象（标题、正文、图片URL、标签、互动数据）
- **MCP 依赖**: Playwright MCP 或 Firecrawl MCP
- **实现方式**:
  - 逐个访问帖子 URL
  - 等待页面渲染完成
  - 提取 DOM 中的正文内容
  - 下载关联图片到本地

#### 2.2.3 Classify Module（分类模块）
- **输入**: 结构化内容对象列表
- **输出**: 带分类标签、摘要、关键词的内容对象
- **实现方式**:
  - 使用 LLM（DeepSeek API）进行内容分类
  - 预定义分类体系（可配置）
  - 提取关键实体和概念
  - 生成中文摘要

#### 2.2.4 Knowledge Base Module（知识库模块）
- **输入**: 分类后的内容对象列表
- **输出**: 本地文件系统中的结构化文档
- **MCP 依赖**: Filesystem MCP
- **实现方式**:
  - 按分类创建目录结构
  - 每篇帖子生成一个 Markdown 文件
  - 生成分类索引 README.md
  - 生成总索引 INDEX.md
  - 可选：使用 Omega-Memory MCP 构建语义搜索

---

## 三、MCP 与 Skills 使用方案

### 3.1 现有可用 MCP

| MCP Server | 用途 | 在此项目中的角色 |
|------------|------|-----------------|
| **playwright** | 浏览器自动化 | **核心**: 搜索、浏览、内容抓取 |
| **firecrawl** | Web 爬虫 | **辅助**: 抓取静态内容页面 |
| **filesystem** | 文件系统操作 | **核心**: 写入本地知识库文档 |
| **omega-memory** | 语义记忆存储 | **可选**: 构建语义搜索知识图谱 |
| **sequential-thinking** | 链式推理 | **可选**: 复杂分类决策 |

### 3.2 现有可用 Skills

| Skill | 用途 | 在此项目中的角色 |
|-------|------|-----------------|
| **python-patterns** | Python 最佳实践 | 指导代码架构 |
| **backend-patterns** | 后端架构模式 | Workflow 设计参考 |
| **code-reviewer** | 代码审查 | 每步实施后的质量检查 |
| **tdd-guide** | 测试驱动开发 | 编写测试用例 |

### 3.3 MCP 调用方式

在 Claude Code 中通过 Agent 调用 MCP 工具：

```python
# 1. 通过 Playwright MCP 搜索小红书
# Agent: general-purpose + playwright MCP
# Prompt: "打开 xiaohongshu.com/explore，搜索关键词'{keyword}'，
#          等待搜索结果加载，提取前{n}条结果的标题、链接、摘要"

# 2. 通过 Filesystem MCP 写入知识库
# Agent: general-purpose + filesystem MCP  
# Prompt: "在 {output_dir}/{category}/ 下创建 {title}.md，
#          写入格式化的知识文档内容"

# 3. 通过 Omega-Memory MCP 存储语义记忆
# Agent: general-purpose + omega-memory MCP
# Prompt: "将这篇内容以知识条目存入 omega-memory，包含标题、分类、摘要、正文"
```

---

## 四、子代理封装方案（Sub-Agent）

### 4.1 目标
将小红书知识库 Workflow 封装为一个 Claude Code 子代理，使其可以被其他 Agent 通过 `Agent` 工具调用，或通过 `/xhs-search` 斜杠命令直接触发。

### 4.2 封装方式对比

| 方式 | 路径 | 适用场景 | 优先级 |
|------|------|----------|--------|
| **Skill（斜杠命令）** | `.claude/skills/xhs-knowledge/` | 用户手动触发搜索 | 推荐 |
| **Agent 定义** | `.claude/agents/xhs-agent.md` | 被其他 Agent 以子代理方式调用 | 推荐 |
| **CLI + Agent 包装** | 本项目 `cli.py` + Agent prompt | 命令行直接执行 | 保留 |

### 4.3 Skill 定义（`/xhs-search` 斜杠命令）

文件位置：`.claude/skills/xhs-knowledge/SKILL.md`

```markdown
---
name: xhs-knowledge
description: 小红书知识库采集——根据关键词搜索、抓取内容、AI分类、构建本地知识库
type: skill
---

# 小红书知识库采集

## 触发方式
- `/xhs-search <关键词>` — 搜索并抓取
- `/xhs-search <关键词> --count 20` — 指定抓取数量
- `/xhs-update` — 更新已有知识库

## 执行流程
1. 使用 Playwright MCP 在小红书搜索关键词
2. 逐一打开帖子页面，抓取正文和元数据
3. 调用 DeepSeek API 对内容进行分类和摘要
4. 使用 Filesystem MCP 写入本地知识库 Markdown 文件
```

### 4.4 Agent 定义（子代理方式）

文件位置：`.claude/agents/xhs-knowledge-agent.md`

```markdown
---
name: xhs-knowledge-agent
description: 小红书知识库子代理——接收关键词，搜索小红书内容，抓取正文，AI分类，构建本地知识库文档
tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
  - Agent
mcp_servers:
  - playwright
  - filesystem
  - omega-memory
---

# 小红书知识库采集代理

## 职责
1. 接收关键词搜索小红书
2. 使用 Playwright MCP 抓取帖子内容
3. 调用 LLM 分类和摘要
4. 使用 Filesystem MCP 写入结构化 Markdown 知识库

## 知识库输出路径
`{工作目录}/output/knowledge_base/`

## 约束
- 请求间隔 >= 5 秒，避免触发风控
- 文件仅写入当前工作目录下
- 所有 MCP 调用限定在当前项目范围
```

### 4.5 Python 子代理入口

文件位置：`src/agent.py`

```python
"""Claude Code 子代理入口 —— 供 Agent 工具直接调用"""
from dataclasses import dataclass
from src.search import XHSSearcher
from src.scrape import XHSScraper
from src.classify import ContentClassifier
from src.knowledge_base import KnowledgeBaseBuilder

@dataclass
class XHSKnowledgeAgent:
    """小红书知识库子代理"""
    output_dir: str = "./output/knowledge_base"
    
    async def search_and_build(
        self, 
        keywords: list[str], 
        count: int = 20
    ) -> dict:
        """搜索并构建知识库，返回执行报告"""
        ...
    
    async def update_knowledge_base(self) -> dict:
        """增量更新已有知识库"""
        ...
```

---

## 五、知识库检索 Agent（KB Agent + RAG）

### 5.1 目标
基于已构建的本地 Markdown 知识库，搭建一个检索 Agent，支持：
1. **关键词匹配** — 基于文件内容的全文搜索（Grep / Glob）
2. **语义搜索** — 基于向量化的 RAG 检索
3. **混合检索** — 关键词 + 语义融合排序

### 5.2 可行性分析

| 方案 | 依赖 | 优势 | 劣势 | 是否可行 |
|------|------|------|------|----------|
| **Grep 全文搜索** | 无（已有 Grep 工具） | 零成本，实时 | 无语义理解 | ✅ 立即可用 |
| **Omega-Memory MCP** | omega-memory 已配置 | 语义搜索，知识图谱 | 需要额外导入步骤 | ✅ 已具备 |
| **ChromaDB 向量库** | `pip install chromadb` | 本地持久化，快 | 需要 embedding 模型 | ✅ 轻量方案 |
| **FAISS 向量库** | `pip install faiss-cpu` | Meta 出品，高效 | 索引管理复杂 | ✅ 备选方案 |
| **LlamaIndex** | `pip install llama-index` | 完整 RAG 框架 | 较重 | ⚠️ 按需引入 |

### 5.3 推荐方案：三层检索架构

```
┌────────────────────────────────────────────────────────────┐
│                    用户查询: "Python 性能优化"               │
└──────────────────────────┬─────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────┐
│  第一层: Grep 关键词快速匹配 (毫秒级)                         │
│  搜索 output/knowledge_base/**/*.md 中的关键词               │
│  返回: 标题匹配 + 正文片段匹配                                │
└──────────────────────────┬─────────────────────────────────┘
                           │ 结果不足 / 需要语义理解
┌──────────────────────────▼─────────────────────────────────┐
│  第二层: ChromaDB 向量语义搜索 (秒级)                        │
│  使用 DeepSeek Embedding 或本地模型生成向量                  │
│  返回: 语义相似内容 Top-K                                    │
└──────────────────────────┬─────────────────────────────────┘
                           │ 需要知识图谱关联
┌──────────────────────────▼─────────────────────────────────┐
│  第三层: Omega-Memory 知识图谱检索                           │
│  关联概念、实体、相关主题                                    │
│  返回: 知识图谱漫游结果                                      │
└────────────────────────────────────────────────────────────┘
```

### 5.4 KB Agent 定义

文件位置：`.claude/agents/kb-search-agent.md`

```markdown
---
name: kb-search-agent
description: 本地知识库检索代理——支持关键词搜索和语义搜索，搜索范围限定在当前项目目录
tools:
  - Read
  - Grep
  - Glob
mcp_servers:
  - filesystem
  - omega-memory
---

# 本地知识库检索代理

## 职责
1. 接收自然语言查询
2. 在 `output/knowledge_base/` 目录下执行多层检索
3. 整合结果并返回结构化答案
4. 引用原始文档路径和行号

## 搜索范围约束
- **仅限当前工作目录** (`d:/software/work/trae/trae_project/workflow/find_knowledge/`)
- 不允许访问工作目录外的任何文件
- 所有路径使用相对路径

## 检索策略
1. 先用 Grep 做快速关键词匹配
2. 如结果不足，使用 ChromaDB 向量搜索
3. 如用户需要深度分析，使用 Omega-Memory 知识图谱

## 输出格式
```markdown
## 检索结果: "{查询}"

### 匹配文档 (共 N 篇)
1. **[文档标题](相对路径)** — 相关度: 95%
   - 摘要: ...
   - 分类: ...
   - 来源: 小红书 @作者名

### 关键发现
- 发现1
- 发现2

### 相关概念
- 概念A → 概念B → 概念C
```
```

### 5.5 RAG 实现方案

```python
# src/kb_agent/rag_engine.py

from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class RAGEngine:
    """本地知识库 RAG 引擎"""
    kb_path: Path                    # 知识库根目录
    embed_model: str = "deepseek"    # embedding 模型
    vector_store: str = "chromadb"   # 向量存储后端
    
    def build_index(self):
        """从 Markdown 知识库构建向量索引"""
        # 1. 遍历所有 .md 文件
        # 2. 按 frontmatter + 正文分段
        # 3. 调用 embedding API 生成向量
        # 4. 存入 ChromaDB
        
    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """语义搜索"""
        # 1. 将查询转为向量
        # 2. 在 ChromaDB 中检索 Top-K
        # 3. 返回带 source path 的结果
        
    def hybrid_search(
        self, query: str, top_k: int = 10
    ) -> list[dict]:
        """混合检索：向量 + 关键词融合"""
        # 1. 向量搜索结果
        # 2. Grep 关键词匹配结果
        # 3. RRF (Reciprocal Rank Fusion) 融合排序
```

### 5.6 知识库导入流程

```
小红书帖子  →  Markdown 文档  →  ChromaDB  →  Omega-Memory
              (Filesystem MCP)   (向量索引)   (知识图谱)

导入时机：
- 每次新抓取完成后自动触发
- 支持手动触发: python cli.py reindex
```

---

## 六、更新后的项目文件结构

```
find_knowledge/
├── implementation_plan.md      # 本文档（设计与实施计划）
├── implementation_log.md       # 实施日志
├── xiaohongshu.py              # 主入口（待实现）
├── src/
│   ├── __init__.py
│   ├── config.py               # 配置管理
│   ├── models.py               # 数据模型
│   ├── logger.py               # 日志系统
│   ├── cli.py                  # CLI 入口
│   ├── agent.py                # 子代理入口
│   ├── search/
│   │   ├── __init__.py
│   │   └── searcher.py         # 搜索模块
│   ├── scrape/
│   │   ├── __init__.py
│   │   ├── scraper.py          # 抓取模块
│   │   └── ocr.py              # PaddleOCR 图片文字提取
│   ├── classify/
│   │   ├── __init__.py
│   │   ├── classifier.py       # AI 分类模块
│   │   └── formatter.py        # LLM 内容格式化
│   ├── knowledge_base/
│   │   ├── __init__.py
│   │   └── builder.py          # 知识库构建模块
│   └── kb_agent/               # 知识库检索 Agent
│       ├── __init__.py
│       ├── rag_engine.py       # RAG 检索引擎
│       ├── searcher.py         # 多层检索协调
│       └── indexer.py          # 向量索引构建
├── config/
│   └── categories.yaml         # 分类体系配置
├── agents/                     # Agent 定义文件
│   ├── xhs-knowledge-agent.md  # 小红书采集子代理
│   └── kb-search-agent.md      # 知识库检索代理
├── skills/
│   └── xhs-knowledge/
│       └── SKILL.md            # /xhs-search 斜杠命令定义
├── tests/
│   ├── test_searcher.py        # 15 tests
│   ├── test_scraper.py         # 27 tests
│   ├── test_classifier.py      # 12 tests
│   ├── test_builder.py         # 13 tests
│   └── test_rag_engine.py      # (待实施)
├── output/
│   └── knowledge_base/         # 知识库输出目录
├── requirements.txt
└── README.md
```

---

## 七、数据模型设计（补充）

### 7.1 核心数据结构

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class XHSPost:
    """小红书帖子"""
    url: str                          # 帖子链接
    post_id: str                      # 帖子ID
    title: str                        # 标题
    content: str                      # 正文内容
    author_name: str                  # 作者昵称
    author_id: str                    # 作者ID
    publish_time: Optional[datetime]  # 发布时间
    like_count: int = 0               # 点赞数
    collect_count: int = 0            # 收藏数
    comment_count: int = 0            # 评论数
    tags: list[str] = field(default_factory=list)     # 标签
    image_urls: list[str] = field(default_factory=list)  # 图片URL
    note_type: str = "normal"         # 帖子类型: normal/video

@dataclass
class ClassifiedPost:
    """分类后的帖子"""
    post: XHSPost
    category: str                     # 主分类
    sub_category: str = ""            # 子分类
    summary: str = ""                 # AI生成摘要
    keywords: list[str] = field(default_factory=list)  # 提取的关键词
    entities: list[str] = field(default_factory=list)  # 实体识别
    sentiment: str = "neutral"        # 情感倾向
    quality_score: float = 0.0        # 内容质量评分
```

### 7.2 为什么用 Markdown 存储知识库

| 优势 | 说明 |
|------|------|
| **AI 原生格式** | LLM 训练数据多为 Markdown，RAG 按 `##` 标题分块效果最佳 |
| **人可直接阅读** | IDE/记事本/任何编辑器打开即读，无需额外工具 |
| **Git 可版本管理** | 纯文本，diff 清晰，可追踪知识库变更历史 |
| **跨平台可移植** | 拷贝文件夹即可迁移，不依赖数据库 |
| **生态兼容** | frontmatter 存元数据 + Obsidian/Notion/MkDocs 直接导入浏览 |
| **结构化** | 标题层级 + 代码块 + 图片链接，既给人看也给程序解析 |

对比：JSON 长文本不可读、SQLite 需要工具、PDF 不易检索、纯向量库丢失原文。

### 7.3 知识库目录结构

```
knowledge_base/
├── INDEX.md                    # 总索引（按分类、关键词、时间索引）
├── config.yaml                 # 分类体系配置
├── categories/
│   ├── 技术编程/
│   │   ├── _index.md           # 分类索引
│   │   ├── post_xxxxx.md       # 单篇知识文档
│   │   └── ...
│   ├── 产品设计/
│   ├── 商业分析/
│   ├── 生活方式/
│   └── ...
├── images/
│   ├── post_xxxxx_01.jpg
│   └── ...
└── metadata.json               # 全局元数据（抓取时间、统计信息）
```

### 7.4 单篇知识文档模板

```markdown
---
title: "{{ post.title }}"
url: "{{ post.url }}"
author: "{{ post.author_name }}"
publish_time: "{{ post.publish_time }}"
category: "{{ category }}"
sub_category: "{{ sub_category }}"
tags: [{{ tags | join(', ') }}]
keywords: [{{ keywords | join(', ') }}]
likes: {{ post.like_count }}
collects: {{ post.collect_count }}
scraped_at: "{{ scraped_at }}"
---

# {{ post.title }}

## 摘要
{{ summary }}

## 正文
{{ post.content }}

## 关键信息
- **实体**: {{ entities | join(', ') }}
- **情感**: {{ sentiment }}
- **质量评分**: {{ quality_score }}/10

## 原文链接
[查看原文]({{ post.url }})
```

---

## 八、实施计划（共 10 个 Phase）

### Phase 0: 环境准备与验证 🟢 已完成

| # | 任务 | 验收标准 | 状态 |
|---|------|----------|------|
| 0.1 | 确认 Playwright MCP 可用性 | 能通过 MCP 打开浏览器并访问网页 | 🟢 |
| 0.2 | 确认 Filesystem MCP 可用性 | 能通过 MCP 读写本地文件 | 🟢 |
| 0.3 | 确认 DeepSeek API 可用（用于 AI 分类） | 能调用 API 并获得分类结果 | 🟢 |
| 0.4 | 安装项目 Python 依赖 | `pip install` 无报错 | 🟢 |
| 0.5 | 准备小红书测试账号（如需要） | 能登录 XHS 网页版 | 🟢 |

### Phase 1: 基础框架搭建 🟢 已完成

| # | 任务 | 验收标准 | 状态 |
|---|------|----------|------|
| 1.1 | 创建项目目录结构 | 按设计创建目录和文件骨架 | 🟢 |
| 1.2 | 实现数据模型 `models.py` | dataclass 定义完成，类型检查通过 | 🟢 |
| 1.3 | 实现配置管理 `config.py` | 支持 YAML/ENV 配置，分类体系可配置 | 🟢 |
| 1.4 | 实现日志系统 `logger.py` | 结构化日志，支持文件+控制台输出 | 🟢 |
| 1.5 | 实现 CLI 入口 `cli.py` | 支持命令行参数解析 | 🟢 |

### Phase 2: 搜索模块实现 🟢 已完成

| # | 任务 | 验收标准 | 状态 |
|---|------|----------|------|
| 2.1 | 实现 Playwright 浏览器启动与登录态复用 | 能打开 XHS 并保持登录 | ⬜ |
| 2.2 | 实现关键词搜索功能 | 输入关键词，返回搜索结果列表 | ⬜ |
| 2.3 | 实现搜索结果解析 | 提取帖子URL、标题、作者、摘要 | ⬜ |
| 2.4 | 实现滚动加载更多结果 | 模拟滚动获取更多搜索结果 | ⬜ |
| 2.5 | 实现搜索模块的错误处理和重试 | 网络异常、风控拦截自动处理 | ⬜ |
| 2.6 | 编写搜索模块单元测试 | 测试覆盖率 >= 80% | ⬜ |

### Phase 3: 内容抓取模块实现 🟢 已完成

| # | 任务 | 验收标准 | 状态 |
|---|------|----------|------|
| 3.1 | 实现单篇帖子内容提取 | 打开帖子页，提取完整正文 | 🟢 |
| 3.2 | 实现元数据提取 | 提取点赞/收藏/评论/时间/标签 | 🟢 |
| 3.3 | 实现图片下载 | 下载帖子图片到本地存储 | 🟢 |
| 3.4 | 实现批量抓取与速率控制 | 自动间隔请求，避免风控 | 🟢 |
| 3.5 | 实现断点续抓 | 支持中断后从上次位置继续 | 🟢 |
| 3.6 | 编写抓取模块单元测试 | 测试覆盖率 >= 80% | 🟢 |

### Phase 4: AI 分类模块实现 🟢 已完成

| # | 任务 | 验收标准 | 状态 |
|---|------|----------|------|
| 4.1 | 设计分类体系 `categories.yaml` | 完整的分类层级结构 | 🟢 |
| 4.2 | 实现 LLM 分类器 | 调用 DeepSeek API 进行内容分类 | 🟢 |
| 4.3 | 实现摘要生成 | 为每篇帖子生成 100-200 字中文摘要 | 🟢 |
| 4.4 | 实现关键词/实体提取 | 从内容中提取关键信息 | 🟢 |
| 4.5 | 实现内容质量评分 | 基于长度、结构、互动数据评分 | 🟢 |
| 4.6 | 编写分类模块单元测试 | 测试覆盖率 >= 80% | 🟢 |

### Phase 5: 知识库构建模块实现 🟢 已完成

| # | 任务 | 验收标准 | 状态 |
|---|------|----------|------|
| 5.1 | 实现 Markdown 文档生成器 | 按模板生成格式化的 .md 文件 | 🟢 |
| 5.2 | 实现分类目录管理 | 自动创建/更新分类目录结构 | 🟢 |
| 5.3 | 实现 INDEX.md 生成 | 生成带分类、关键词、时间索引的总目录 | 🟢 |
| 5.4 | 实现 metadata.json 维护 | 记录全局抓取统计和元数据 | 🟢 |
| 5.5 | 实现 Omega-Memory 语义存储（可选） | 将内容存入 omega-memory 支持语义搜索 | ⬜ |
| 5.6 | 编写知识库模块单元测试 | 测试覆盖率 >= 80% | 🟢 |

### Phase 6: 集成与端到端测试 🟢 已完成

| # | 任务 | 验收标准 | 状态 |
|---|------|----------|------|
| 6.1 | 串联完整 Workflow 流程 | 从关键词到知识库全流程跑通 | 🟢 |
| 6.2 | E2E 测试：单关键词搜索 | 端到端验证全流程 | 🟢 |
| 6.3 | E2E 测试：多关键词批量搜索 | 并发处理多个关键词 | ⬜ |
| 6.4 | 性能优化 | 单帖抓取 < 10s，批量20帖 < 3min | ⬜ |
| 6.5 | 编写 README 使用文档 | 包含安装、配置、使用说明 | ⬜ |
| 6.6 | 代码审查 & 安全审查 | 使用 code-reviewer / security-reviewer agent | ⬜ |

### Phase 7: 子代理封装 🟢 已完成

| # | 任务 | 验收标准 | 状态 |
|---|------|----------|------|
| 7.1 | 创建 Agent 定义文件 `agents/xhs-knowledge-agent.md` | Agent 可通过 Agent 工具被其他 Agent 调用 | 🟢 |
| 7.2 | 创建 Skill 定义文件 `skills/xhs-knowledge/SKILL.md` | 可通过 `/xhs-search` 斜杠命令触发 | 🟢 |
| 7.3 | 端到端验证：通过 Agent 调用搜索关键词并构建知识库 | 子代理全流程可用 | 🟢 |

### Phase 8: 知识库检索 Agent + RAG 🟢 已完成

| # | 任务 | 验收标准 | 状态 |
|---|------|----------|------|
| 8.1 | 安装 RAG 依赖（chromadb + ModelScope embedding） | `pip install` 无报错 | 🟢 |
| 8.2 | 实现 `src/kb_agent/indexer.py` 向量索引构建器 | 93 chunks 索引到 ChromaDB（512维） | 🟢 |
| 8.3 | 实现 `src/kb_agent/rag_engine.py` RAG 检索引擎 | keyword/semantic/hybrid 三种模式 | 🟢 |
| 8.4 | 实现 `src/kb_agent/searcher.py` 多层检索协调器 | 整合 keyword + semantic → hybrid | 🟢 |
| 8.5 | 实现检索结果格式化输出 | 结构化 Markdown（标题/路径/摘要/相关度） | 🟢 |
| 8.6 | 实现搜索范围约束（仅限当前项目目录） | `_is_in_scope()` 拒绝项目外路径 | 🟢 |
| 8.7 | 编写 RAG 模块单元测试 | 9 tests passed | 🟢 |
| 8.8 | 端到端验证 | 检索结果准确、路径正确 | 🟢 |

### Phase 9: 小红书采集 MCP 封装 🟢 已完成

将搜索+抓取+分类+构建封装为一个 MCP Server，Claude 通过 MCP tool 直接调用。

| # | 任务 | 验收标准 | 状态 |
|---|------|----------|------|
| 9.1 | 创建 MCP Server 骨架 `mcp_server/xhs_server.py` | Python MCP SDK 启动无报错 | ⬜ |
| 9.2 | 注册 `search_xhs` tool | Claude 可通过 `mcp__xhs-kb__search_xhs(keyword, count)` 搜索 | ⬜ |
| 9.3 | 注册 `scrape_post` tool | Claude 可通过 `mcp__xhs-kb__scrape_post(url)` 抓取单帖 | ⬜ |
| 9.4 | 注册 `classify_post` tool | Claude 可通过 `mcp__xhs-kb__classify_post(post_id)` 分类 | ⬜ |
| 9.5 | 注册 `build_kb` tool | Claude 可通过 `mcp__xhs-kb__build_kb(keyword)` 构建知识库 | ⬜ |
| 9.6 | PaddleOCR 按需加载 | build_kb 执行完后释放模型，下次调用时懒加载 | ⬜ |
| 9.7 | 注册到 `mcp-servers.json` | `"xhs-kb"` MCP 出现在可用列表 | ⬜ |
| 9.8 | 端到端验证：Claude 对话触发采集 | 用户说"搜索小红书agent面试"→自动调 MCP →产出知识库 | ⬜ |

### Phase 10: 知识库检索 MCP 封装 🟢 已完成（合并到 xhs-kb Server）

> `search_kb` tool 已集成到 Phase 9 的 `xhs-kb` MCP Server，无需独立 Server。

| # | 任务 | 验收标准 | 状态 |
|---|------|----------|------|
| 10.1 | `search_kb` tool 集成到 xhs-kb Server | Claude 可通过 `mcp__xhs-kb__search_kb(query)` 检索 | 🟢 |
| 10.2 | 端到端验证 | 检索结果准确 | 🟢 |

---

## 九、技术选型与依赖

### 9.1 Python 依赖

```txt
# 核心依赖
pydantic>=2.0          # 数据模型验证
pyyaml>=6.0            # YAML 配置解析
httpx>=0.27            # 异步 HTTP 客户端
rich>=13.0             # 终端美化输出

# AI/LLM
openai>=1.0            # DeepSeek API（兼容 OpenAI SDK）

# RAG 检索引擎
chromadb>=0.5          # 本地向量数据库
sentence-transformers>=3.0  # 本地 embedding 模型（可选，也可用 DeepSeek Embedding API）

# 图像处理
Pillow>=10.0           # 图片处理

# MCP Server（Phase 9-10）
mcp>=1.0               # Python MCP SDK

# 工具库
python-slugify>=8.0    # 文件名安全化
tenacity>=8.0          # 重试机制
```

### 9.2 MCP 调用方式

```python
# 在 Claude Code 环境中通过 Agent 调用 MCP
# 使用 general-purpose agent + 指定 MCP server

# 搜索调用
agent = Agent(
    subagent_type="general-purpose",
    mcp_servers=["playwright"],
    prompt=f"使用 Playwright 打开小红书搜索'{keyword}'..."
)

# 文件写入调用
agent = Agent(
    subagent_type="general-purpose", 
    mcp_servers=["filesystem"],
    prompt=f"将内容写入 {filepath}..."
)
```

---

## 十、风险与应对

| 风险 | 概率 | 影响 | 应对措施 |
|------|------|------|----------|
| XHS 反爬升级导致 Playwright 也无法抓取 | 中 | 高 | 备用方案：使用真实用户 Cookie / 降低频率 |
| 小红书网页版结构变化 | 高 | 中 | 使用语义选择器而非固定CSS路径，定期维护 |
| LLM 分类结果不一致 | 中 | 低 | 设置分类温度=0，使用 few-shot prompt |
| API 调用成本过高 | 低 | 中 | 对短内容使用规则分类，仅长内容使用 LLM |
| 登录态失效 | 高 | 中 | 定期检查登录态，自动提示重新登录 |

---

## 十一、成功指标

- [ ] 单关键词搜索返回 >= 20 条结果
- [ ] 内容抓取完整率 >= 90%（正文 + 元数据）
- [ ] AI 分类准确率 >= 85%（人工抽检）
- [ ] 知识库文档格式规范，可被其他工具解析
- [ ] 全流程自动化，无需人工干预（首次登录除外）
- [ ] `/xhs-search` 斜杠命令可正常触发子代理
- [ ] KB Agent 可通过 Agent 工具被其他 Agent 调用
- [ ] RAG 检索返回 Top-10 语义相关结果，响应时间 < 3s
- [ ] 搜索范围严格限定在当前项目目录内
- [ ] `mcp__xhs-kb__search_xhs` 可在 Claude 对话中触发采集
- [ ] `mcp__kb-search__hybrid_search` 可在 Claude 对话中触发检索
- [ ] MCP 模式下 PaddleOCR 按需加载，build_kb 执行完后自动释放

---

## 十二、后续扩展方向

1. **MCP 采集封装 (Phase 9)**: 将搜索/抓取/分类/构建封装为 MCP Server，Claude 对话中一句话触发采集
2. **MCP 检索封装 (Phase 10)**: 将 KB Agent 封装为 MCP Server，Claude 自动检索本地知识库
3. **定时自动更新**: 使用 cron job 定期搜索新内容并自动更新知识库
4. **多平台支持**: 扩展到知乎、微博、公众号等内容平台
5. **可视化面板**: 基于 Streamlit 构建知识库浏览与搜索界面
6. **增量更新**: 仅抓取新内容，避免重复抓取已存在的帖子

### MCP 完整调用架构（Phase 9-10 完成后）

```
用户在 Claude 对话中:
  "搜索小红书 agent 面试内容"
       │
       ▼
  Claude 自动调用 mcp__xhs-kb__search_xhs("agent面试", count=5)
       │
       ▼
  MCP Server (常驻) → Playwright → PaddleOCR → DeepSeek
       │
       ▼
  output/knowledge_base/ ← 项目目录下

  "本地知识库有 RAG 相关内容吗？"
       │
       ▼
  Claude 自动调用 mcp__kb-search__hybrid_search("RAG", top_k=10)
       │
       ▼
  MCP Server (常驻) → Grep + ChromaDB → Omega-Memory
       │
       ▼
  返回: 结构化结果 + 源文件路径 + 相关度 + 摘要
```

---

## 十三、功能实现顺序清单（逐项推进）

以下是按依赖关系排列的完整实现顺序，每个功能编号对应实施顺序。**建议逐项实现，每完成一项验收后再进入下一项**。

### 第一批：项目骨架（无外部依赖，可立即开始）

```
F-01  创建项目目录结构
      文件: src/__init__.py, src/search/__init__.py, src/scrape/__init__.py,
            src/classify/__init__.py, src/knowledge_base/__init__.py,
            src/kb_agent/__init__.py, config/, tests/, output/, agents/, skills/
      依赖: 无
      验收: 目录结构存在，每个目录有 __init__.py

F-02  实现数据模型
      文件: src/models.py
      内容: XHSPost, ClassifiedPost, SearchResult 数据类
      依赖: F-01
      验收: 可 from src.models import XHSPost, ClassifiedPost

F-03  实现配置管理
      文件: src/config.py, config/categories.yaml
      内容: YAML 加载、环境变量读取、分类体系配置
      依赖: F-01
      验收: Config 类可正确读取 categories.yaml

F-04  实现日志系统
      文件: src/logger.py
      内容: 结构化日志，同时输出到文件和终端
      依赖: F-01
      验收: get_logger(__name__) 返回的 logger 正常输出

F-05  实现 CLI 入口
      文件: src/cli.py, xiaohongshu.py
      内容: search / scrape / classify / build / run 子命令
      依赖: F-03, F-04
      验收: python xiaohongshu.py --help 显示子命令
```

### 第二批：搜索模块（需要 Playwright MCP）

```
F-06  验证 Playwright MCP 可用性
      依赖: F-01
      验收: 通过 Agent + playwright MCP 成功打开网页

F-07  实现搜索模块 — 浏览器管理
      文件: src/search/searcher.py
      内容: 浏览器启动、登录态复用（userDataDir）、Cookie 管理
      依赖: F-02, F-06
      验收: 无头/有头模式打开小红书，保持登录态

F-08  实现搜索模块 — 关键词搜索
      文件: src/search/searcher.py（追加）
      内容: 打开搜索页、输入关键词、等待结果加载
      依赖: F-07
      验收: 输入 "Python" 能看到搜索结果列表

F-09  实现搜索模块 — 结果解析
      文件: src/search/searcher.py（追加）
      内容: 提取帖子URL、标题、作者、摘要、封面图URL
      依赖: F-08
      验收: 返回 list[SearchResult]，每条包含 url/title/author

F-10  实现搜索模块 — 滚动加载
      文件: src/search/searcher.py（追加）
      内容: 模拟滚动到底部、等待新内容、设置最大滚动次数
      依赖: F-09
      验收: 一次搜索返回 >= 20 条结果

F-11  搜索模块错误处理
      文件: src/search/searcher.py（追加）
      内容: 网络超时重试、风控页面检测、验证码识别提示
      依赖: F-10
      验收: 遇到错误时抛出自定义异常并有重试日志

F-12  搜索模块单元测试
      文件: tests/test_searcher.py
      依赖: F-11
      验收: pytest 通过，覆盖率 >= 80%
```

### 第三批：内容抓取模块（需要 Playwright MCP）

```
F-13  实现抓取模块 — 单帖内容提取
      文件: src/scrape/scraper.py
      内容: 打开帖子URL、等待渲染、提取正文文本
      依赖: F-02, F-06
      验收: 传入帖子URL，返回包含正文的 XHSPost

F-14  实现抓取模块 — 元数据提取
      文件: src/scrape/scraper.py（追加）
      内容: 提取点赞/收藏/评论数、发布时间、标签
      依赖: F-13
      验收: XHSPost 中所有字段均有值

F-15  实现抓取模块 — 图片下载
      文件: src/scrape/scraper.py（追加）
      内容: 提取图片URL列表、下载到 output/knowledge_base/images/
      依赖: F-13
      验收: 图片文件保存到本地，文件可正常打开

F-16  实现抓取模块 — 批量抓取与速率控制
      文件: src/scrape/scraper.py（追加）
      内容: 遍历URL列表、随机延迟 3~8 秒、并发限制
      依赖: F-14
      验收: 批量抓取 10 篇帖子，请求间隔 >= 3 秒

F-17  实现抓取模块 — 断点续抓
      文件: src/scrape/scraper.py（追加）
      内容: 已抓取URL缓存、中断后从上次位置继续
      依赖: F-16
      验收: 中断后重新运行，跳过已抓取的帖子

F-18  抓取模块单元测试
      文件: tests/test_scraper.py
      依赖: F-17
      验收: pytest 通过，覆盖率 >= 80%
```

### 第四批：AI 分类模块（需要 DeepSeek API）

```
F-19  验证 DeepSeek API 可用性
      依赖: F-03
      验收: openai Python SDK 连接 DeepSeek 成功，返回响应

F-20  设计分类体系
      文件: config/categories.yaml
      内容: 主分类 + 子分类层级结构（如: 技术编程/后端/Python）
      依赖: F-03
      验收: YAML 加载无报错，分类结构 >= 5 个主分类

F-21  实现 LLM 分类器
      文件: src/classify/classifier.py
      内容: 调用 DeepSeek API、传入分类体系、返回主/子分类
      依赖: F-02, F-19, F-20
      验收: 输入帖子内容，返回正确的分类标签

F-22  实现摘要生成
      文件: src/classify/classifier.py（追加）
      内容: 调用 DeepSeek API 生成 100-200 字中文摘要
      依赖: F-21
      验收: 生成的摘要简练、准确反映原文核心

F-23  实现关键词/实体提取
      文件: src/classify/classifier.py（追加）
      内容: 提取 5-10 个关键词、命名实体识别
      依赖: F-21
      验收: 关键词与原文内容高度相关

F-24  实现内容质量评分
      文件: src/classify/classifier.py（追加）
      内容: 基于正文长度、结构完整性、互动数据打分 0-10
      依赖: F-14, F-21
      验收: 高质量帖 > 7分，低质量帖 < 3分

F-25  分类模块单元测试
      文件: tests/test_classifier.py
      依赖: F-24
      验收: pytest 通过，覆盖率 >= 80%
```

### 第五批：知识库构建模块（需要 Filesystem MCP）

```
F-26  验证 Filesystem MCP 可用性
      依赖: F-01
      验收: 通过 Agent + filesystem MCP 成功创建/读取文件

F-27  实现 Markdown 文档生成器
      文件: src/knowledge_base/builder.py
      内容: 按模板生成带 frontmatter 的 .md 文件
      依赖: F-02, F-26
      验收: 生成的 .md 文件包含 frontmatter + 正文，格式正确

F-28  实现分类目录管理
      文件: src/knowledge_base/builder.py（追加）
      内容: 自动按分类创建子目录、写入 _index.md
      依赖: F-27
      验收: output/knowledge_base/categories/技术编程/ 目录自动创建

F-29  实现 INDEX.md 总索引生成
      文件: src/knowledge_base/builder.py（追加）
      内容: 按分类/关键词/时间三维索引
      依赖: F-28
      验收: INDEX.md 包含所有帖子的索引链接

F-30  实现 metadata.json 维护
      文件: src/knowledge_base/builder.py（追加）
      内容: 记录抓取时间、帖子总数、分类统计
      依赖: F-28
      验收: metadata.json 包含正确的时间戳和统计数据

F-31  知识库构建模块单元测试
      文件: tests/test_builder.py
      依赖: F-30
      验收: pytest 通过，覆盖率 >= 80%
```

### 第六批：Workflow 集成

```
F-32  串联完整 Workflow
      文件: src/cli.py（追加）, src/agent.py
      内容: 实现 `run` 命令，串联 Search → Scrape → Classify → Build
      依赖: F-11, F-17, F-24, F-30
      验收: 运行 python cli.py run --keywords "Python" 全流程通过

F-33  端到端测试
      文件: tests/test_e2e.py
      内容: 单关键词全流程、多关键词批量、异常中断恢复
      依赖: F-32
      验收: 所有 E2E 测试通过
```

### 第七批：子代理封装

```
F-34  创建小红书采集 Agent 定义
      文件: agents/xhs-knowledge-agent.md
      内容: frontmatter 配置（工具、MCP、约束）+ 执行指令
      依赖: F-32
      验收: 其他 Agent 可通过 Agent 工具调用此子代理

F-35  创建 Skill 斜杠命令定义
      文件: skills/xhs-knowledge/SKILL.md
      内容: /xhs-search 命令定义 + 使用说明
      依赖: F-32
      验收: /xhs-search <关键词> 可触发搜索

F-36  子代理端到端验证
      依赖: F-34, F-35
      验收: 通过 Agent 调用搜索 → 构建知识库全流程跑通
```

### 第八批：知识库检索 Agent + RAG

```
F-37  安装 RAG 依赖
      内容: pip install chromadb sentence-transformers
      依赖: F-04
      验收: import chromadb 无报错

F-38  实现向量索引构建器
      文件: src/kb_agent/indexer.py
      内容: 遍历 Markdown 文件 → 分段 → embedding → ChromaDB
      依赖: F-30, F-37
      验收: 运行后 ChromaDB 中可查到知识库内容

F-39  实现 RAG 检索引擎
      文件: src/kb_agent/rag_engine.py
      内容: 语义搜索(search)、混合检索(hybrid_search)、RRF 融合
      依赖: F-38
      验收: 输入查询返回 Top-10 结果，含源文件路径和相关度

F-40  实现多层检索协调器
      文件: src/kb_agent/searcher.py
      内容: 第一层 Grep → 第二层 ChromaDB → 第三层 Omega-Memory
      依赖: F-39
      验收: 自动选择检索层级，返回合并排序结果

F-41  创建 KB 检索 Agent 定义
      文件: agents/kb-search-agent.md
      内容: frontmatter + 搜索范围约束（仅限当前目录）
      依赖: F-40
      验收: 其他 Agent 可调用 KB 检索

F-42  实现检索结果格式化
      文件: src/kb_agent/searcher.py（追加）
      内容: Markdown 结构化输出（标题、路径、摘要、相关度）
      依赖: F-41
      验收: 输出格式符合模板规范

F-43  实现 Omega-Memory 知识导入
      文件: src/kb_agent/searcher.py（追加）
      内容: 知识库 Markdown → Omega-Memory 知识图谱
      依赖: F-41
      验收: Omega-Memory 中可语义搜索知识库内容

F-44  RAG 模块单元测试
      文件: tests/test_rag_engine.py
      依赖: F-43
      验收: pytest 通过，覆盖率 >= 80%
```

### 第九批：文档与审查

```
F-45  编写 README 使用文档
      文件: README.md
      内容: 安装、配置、使用示例、常见问题
      依赖: F-33
      验收: 新人可按 README 独立完成安装和首次运行

F-46  代码审查
      使用: code-reviewer agent
      依赖: F-45
      验收: 无 CRITICAL / HIGH 问题

F-47  安全审查
      使用: security-reviewer agent
      依赖: F-45
      验收: 无 CRITICAL 安全问题
```

---

### 依赖关系总览

```
F-01 ──→ F-02 ──→ F-07 ──→ F-08 ──→ F-09 ──→ F-10 ──→ F-11 ──→ F-12
  │                                     │
  ├──→ F-03 ──→ F-05                    └──→ F-13 ──→ F-14 ──→ F-16 ──→ F-17 ──→ F-18
  │              │                                │
  ├──→ F-04      └──→ F-19 ──→ F-21 ──→ F-22     └──→ F-15
  │                       │         │
  ├──→ F-06                │         └──→ F-23
  │                        └──→ F-24 ──→ F-25
  │
  └──→ F-26 ──→ F-27 ──→ F-28 ──→ F-29 ──→ F-30 ──→ F-31
                                               │
                    ┌────────────────────────────┘
                    ▼
F-32 ──→ F-33 ──→ F-34 ──→ F-35 ──→ F-36
  │
  ├──→ F-37 ──→ F-38 ──→ F-39 ──→ F-40 ──→ F-41 ──→ F-42 ──→ F-44
  │                                                │
  │                                                └──→ F-43
  │
  └──→ F-45 ──→ F-46 ──→ F-47
```

---

### 进度追踪表

| # | 功能 | 状态 | 完成日期 | 备注 |
|---|------|------|----------|------|
| F-01 | 创建项目目录结构 | 🟢 | 2026-05-12 | |
| F-02 | 实现数据模型 models.py | 🟢 | 2026-05-12 | |
| F-03 | 实现配置管理 config.py | 🟢 | 2026-05-12 | |
| F-04 | 实现日志系统 logger.py | 🟢 | 2026-05-12 | |
| F-05 | 实现 CLI 入口 cli.py | 🟢 | 2026-05-12 | |
| F-06 | 验证 Playwright MCP | 🟢 | 2026-05-12 | |
| F-07 | 搜索模块—浏览器管理 | 🟢 | 2026-05-12 | |
| F-08 | 搜索模块—关键词搜索 | 🟢 | 2026-05-12 | |
| F-09 | 搜索模块—结果解析 | 🟢 | 2026-05-12 | |
| F-10 | 搜索模块—滚动加载 | 🟢 | 2026-05-12 | |
| F-11 | 搜索模块—错误处理 | 🟢 | 2026-05-12 | |
| F-12 | 搜索模块—单元测试 | 🟢 | 2026-05-12 | 15 tests passed |
| F-13 | 抓取模块—单帖内容提取 | 🟢 | 2026-05-12 | |
| F-14 | 抓取模块—元数据提取 | 🟢 | 2026-05-12 | |
| F-15 | 抓取模块—图片下载 | 🟢 | 2026-05-12 | |
| F-16 | 抓取模块—批量与速率控制 | 🟢 | 2026-05-12 | |
| F-17 | 抓取模块—断点续抓 | 🟢 | 2026-05-12 | |
| F-18 | 抓取模块—单元测试 | 🟢 | 2026-05-12 | 27 tests passed |
| F-19 | 验证 DeepSeek API | 🟢 | 2026-05-12 | |
| F-20 | 设计分类体系 categories.yaml | 🟢 | 2026-05-12 | |
| F-21 | LLM 分类器 | 🟢 | 2026-05-12 | |
| F-22 | 摘要生成 | 🟢 | 2026-05-12 | 与分类合并一次调用 |
| F-23 | 关键词/实体提取 | 🟢 | 2026-05-12 | |
| F-24 | 内容质量评分 | 🟢 | 2026-05-12 | |
| F-25 | 分类模块—单元测试 | 🟢 | 2026-05-12 | 12 tests passed |
| F-26 | 验证 Filesystem MCP | 🟢 | 2026-05-12 | |
| F-27 | Markdown 文档生成器 | 🟢 | 2026-05-12 | frontmatter + 正文 |
| F-28 | 分类目录管理 | 🟢 | 2026-05-12 | + _index.md |
| F-29 | INDEX.md 生成 | 🟢 | 2026-05-12 | 分类/关键词/时间索引 |
| F-30 | metadata.json 维护 | 🟢 | 2026-05-12 | 统计+情感分布 |
| F-31 | 知识库模块—单元测试 | 🟢 | 2026-05-12 | 13 tests passed |
| F-32 | OCR 模块—PaddleOCR 集成 | 🟢 | 2026-05-12 | 中文识别，自动跳过<200px小图 |
| F-33 | OCR 触发逻辑—有内容图即OCR | 🟢 | 2026-05-12 | 描述+图片同时存在时合并来源 |
| F-34 | 内容格式化模块 formatter.py | 🟢 | 2026-05-12 | OCR断行修复/错字纠正/结构化Q&A |
| F-35 | MD图片链接修复+相对路径 | 🟢 | 2026-05-12 | ../../images/xxx.jpg 正斜杠 |
| F-36 | 日志累计追加 output/run.log | 🟢 | 2026-05-12 | 追加模式替代时间戳文件 |
| F-37 | 调试文件清理提示 | 🟢 | 2026-05-12 | 运行结束询问 y/N |
| F-38 | 串联完整 Workflow（5步） | 🟢 | 2026-05-12 | Search→Scrape→Format→Classify→Build |
| F-39 | Agent 定义 xhs-knowledge-agent | 🟢 | 2026-05-12 | |
| F-40 | Skill 定义 /xhs-search | 🟢 | 2026-05-12 | |
| F-41 | 子代理端到端验证 | 🟢 | 2026-05-12 | Agent 工具调用成功 |
| F-42 | 安装 RAG 依赖 (chromadb+ModelScope) | 🟢 | 2026-05-12 | |
| F-43 | 向量索引构建器 indexer.py | 🟢 | 2026-05-12 | 93 chunks, 512dim |
| F-44 | RAG 检索引擎 rag_engine.py | 🟢 | 2026-05-12 | keyword/semantic/hybrid |
| F-45 | 多层检索协调器 searcher.py | 🟢 | 2026-05-12 | + 格式化 + 范围约束 |
| F-46 | RAG 模块—单元测试 | 🟢 | 2026-05-12 | 9 tests passed |
| F-47 | RAG 端到端验证 | 🟢 | 2026-05-12 | 检索结果准确 |
| F-56 | README 使用文档 | 🟢 | 2026-05-13 | |
| F-57 | 代码审查 | ⬜ | | |
| F-58 | 安全审查 | ⬜ | | |
| F-48 | xhs MCP—创建 Server 骨架 | 🟢 | 2026-05-13 | mcp_server/xhs_server.py |
| F-49 | xhs MCP—run_pipeline tool | 🟢 | 2026-05-13 | 全流程一键调用 |
| F-50 | xhs MCP—search_xhs tool | 🟢 | 2026-05-13 | 关键词搜索 |
| F-51 | xhs MCP—search_kb tool | 🟢 | 2026-05-13 | 本地知识库检索 |
| F-52 | xhs MCP—PaddleOCR 按需加载/释放 | 🟢 | 2026-05-13 | asyncio.to_thread 隔离 |
| F-53 | xhs MCP—注册配置 | 🟢 | 2026-05-13 | .claude.json + mcp-servers.json |
| F-54 | xhs MCP—端到端验证 | 🟢 | 2026-05-13 | 3 tools 测试通过 |
| F-55 | kb MCP—合并到 xhs-kb Server | 🟢 | 2026-05-13 | search_kb 已集成 |
| F-55 | kb MCP—已合并到 xhs-kb Server | 🟢 | 2026-05-13 | 独立 Phase 10 不再需要 |

---

## 十四、实施进度总结（2026-05-12）

### 已完成：56/58（97%）

```
Pipeline: Search → Scrape → Format → Classify → Build → Index → Search
           ✅        ✅        ✅        ✅         ✅       ✅       ✅
          6s       90s       20s       15s       1s      10s     <1s
```

### 实际实施与计划差异

| 项目 | 计划 | 实际 |
|------|------|------|
| 帖子访问方式 | 直接 URL 访问 | **模拟点击**（需要 xsec_token） |
| OCR 方案 | 计划不包含 | **PaddleOCR v2**（本地中文识别） |
| OCR 触发 | — | **有内容图就 OCR**，描述+图片合并 |
| 内容格式化 | 计划不包含 | **LLM 格式化**（修复OCR/结构化/分离评论区） |
| 图片过滤 | 全部下载 | **仅轮播区** + 跳过<200px + 头像class/URL |
| 图片链接 | — | **相对路径** `../../images/xxx.jpg` |
| 日志 | 每次独立文件 | **累计追加** `output/run.log` |
| 调试清理 | — | 运行结束 **y/N 交互提示** |

### 当前知识库能力

```bash
python xiaohongshu.py run --keywords "关键词" --count 5
```

| 功能 | 说明 |
|------|------|
| 搜索 | Playwright 模拟浏览器，登录态复用 |
| 抓取（有描述文字） | 提取标题/正文/标签/评论/互动数据 |
| 抓取（纯图片帖） | 提取截图 → OCR 识别文字 → 正文 |
| 抓取（图文混合） | 描述+OCR 双来源 → 格式化合并 |
| 格式化 | LLM 修复OCR断行/错字 + 结构化Q&A |
| 分类 | DeepSeek API 主/子分类 + 摘要 + 关键词 + 实体 + 0-10评分 |
| 知识库输出 | Markdown + frontmatter + 图片链接 + INDEX索引 + metadata.json |
| 日志 | output/run.log 累计追加 |
| 测试 | **62 tests 全绿** |

### 待实施

| 批次 | 内容 | 任务数 |
|------|------|--------|
| Phase 7 | 子代理封装（Skill / Agent 定义） | 3 | 🟢 |
| Phase 8 | KB Agent + RAG（向量检索 + 语义搜索） | 8 | 🟢 |
| Phase 9 | 小红书采集+检索 MCP 封装 | 8 | 🟢 |
| Phase 10 | 知识库检索独立 MCP（已合并到 Phase 9）| — | 🟢 |
| Phase 11 | 文档与审查 | 2 | 🔴 |
| Phase 11 | 文档与审查（README + 代码审查 + 安全审查） | 2 |
