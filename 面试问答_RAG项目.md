# 面试问答 —— 小红书 RAG 知识库项目

---

## 一、RAG 基础与架构

### Q1: 请介绍一下你这个项目的整体架构？

**答：** 这是一个从零构建的 RAG（检索增强生成）知识库系统，专门针对小红书平台上 AI Agent / LLM 面试相关内容。整体分为两条主线：

**数据摄入管线（5 个阶段）：**
1. **搜索** —— Playwright 驱动浏览器，在 xiaohongshu.com 搜索指定关键词，解析帖子卡片
2. **抓取** —— 模拟点击打开帖子详情，提取正文/评论，下载图片并用 PaddleOCR 提取图中文字
3. **格式化** —— 调用 DeepSeek Chat 清理 OCR 文本（修复断行/错字），按 Markdown 结构化
4. **分类** —— LLM 对内容进行分类（6 大类 30+ 子类）、摘要、关键词提取、情感分析
5. **构建** —— 生成 Markdown 知识库文件（带 YAML frontmatter），输出 INDEX.md + metadata.json

**检索管线（2 个阶段）：**
6. **索引** —— 按 `##` 标题分块 → ModelScope 512 维嵌入 → 存入 ChromaDB
7. **搜索** —— 三层检索：关键字（jieba 分词） + 语义（余弦相似度） + 混合（融合排序）

整个系统通过 CLI 命令（`xiaohongshu.py run/search/scrape/classify/build`）和 MCP 服务器对外暴露。

---

### Q2: 什么是 RAG？为什么选择 RAG 而不是微调？

**答：** RAG（Retrieval-Augmented Generation）是在 LLM 生成回答之前，先从外部知识库中检索相关文档，将检索结果作为上下文注入 Prompt，让 LLM 基于检索到的知识进行生成。

**选择 RAG 而非微调的原因：**
- **知识时效性：** RAG 可以随时更新知识库（重新抓取/构建），无需重新训练
- **可解释性：** RAG 返回具体来源（URL、帖子），用户可以验证答案的可信度
- **幻觉控制：** 检索结果约束了 LLM 的输出范围，减少编造
- **成本：** 微调需要大量标注数据和 GPU 资源，RAG 只需向量索引
- **本项目场景：** 面试题是持续更新的，新帖不断出现，RAG 天然适合

---

### Q3: 你的检索系统采用了哪几层检索策略？各自优缺点是什么？

**答：** 我实现了三层检索，通过 `rag_engine.py` 中的 `search_mode` 参数切换：

| 层级 | 方法 | 优点 | 缺点 |
|------|------|------|------|
| **关键字检索** | jieba 分词匹配 frontmatter 中的 tags/keywords | 精确匹配，速度快，可解释 | 无法理解语义，同义词/近义词会漏掉 |
| **语义检索** | ChromaDB 余弦相似度搜索 512 维向量 | 理解语义，能匹配同义表达 | 可能返回不相关但词汇相似的内容 |
| **混合检索** | `(keyword_score + semantic_score) / 2` 融合排序 | 取长补短，准确率和召回率最优 | 实现复杂，需要调权重 |

核心融合逻辑：
```python
# 混合得分 = (关键字得分 + 语义得分) / 2
combined_scores = {}
for path, score in keyword_results.items():
    combined_scores[path] = (score + semantic_scores.get(path, 0)) / 2
for path, score in semantic_results.items():
    if path not in combined_scores:
        combined_scores[path] = score / 2  # 仅语义命中时折半
```

最终的分数归一化到 0-1，并且做了路径级去重。

---

## 二、向量数据库

### Q4: 为什么选择 ChromaDB 而不是 FAISS / Milvus / Pinecone？

**答：**

| 维度 | ChromaDB | FAISS | Milvus | Pinecone |
|------|----------|-------|--------|----------|
| **部署复杂度** | `pip install`，零配置 | 需自己管理索引文件 | 需要 Docker/集群 | SaaS，需要网络 |
| **持久化** | 内置，自动 | 需手动序列化 | 内置 | 云端 |
| **规模** | 适合中小规模（<100K 文档） | 适合大规模 | 适合超大规模 | 任意规模 |
| **元数据过滤** | 内置支持 | 需自己实现 | 内置 | 内置 |
| **中文生态** | 通用 | 通用 | 通用 | 无国内节点 |

**本项目选择 ChromaDB 的理由：**
- 知识库规模在百~千篇文档级别，ChromaDB 完全够用
- 需要本地离线运行，Pinecone 依赖网络不可接受
- 需要简单的元数据过滤（按 category、tags），ChromaDB 内置支持
- 持久化到 `output/chroma_db/`，Git 友好，可随项目分发

---

### Q5: ChromaDB 中你的 collection 设计是怎样的？元数据存了什么？

**答：** Collection 设计如下：

```python
collection = client.get_or_create_collection(
    name="xhs_knowledge",
    metadata={"hnsw:space": "cosine"}  # 余弦距离
)

collection.add(
    ids=[chunk_id],           # 格式: "{post_id}_chunk_{index}"
    embeddings=[vector],       # 512 维浮点向量
    documents=[chunk_text],    # 原始文本块
    metadatas=[{
        "post_id": post_id,
        "title": title,
        "category": category,       # 主分类
        "sub_category": sub_category,  # 子分类
        "tags": ",".join(tags),
        "keywords": ",".join(keywords),
        "source_url": url,
        "chunk_section": section,   # 如 "摘要"、"正文"、"关键信息"
        "chunk_index": i            # 块序号
    }]
)
```

**设计考虑：**
- `hnsw:space": "cosine"` —— 使用余弦相似度，对文本嵌入更合适
- 元数据携带完整的分类和来源信息，检索时可以直接展示来源
- `chunk_section` 记录块的来源段落，便于定位到文档的具体区域
- ID 设计为 `{post_id}_chunk_{i}`，保证唯一且可追溯

---

## 三、嵌入模型

### Q6: 你用的嵌入模型是什么？为什么选它？

**答：** 使用的是 ModelScope 上的 `iic/nlp_gte_sentence-embedding_chinese-small`。

**技术参数：**
- 维度：512
- 类型：sentence-transformer（基于 GTE 架构）
- 语言：中文优化
- 来源：ModelScope（阿里云镜像，国内可访问）

**选择理由：**
1. **中文优化 ——** GTE（General Text Embedding）系列在中文语义理解上表现优秀，C-MTEB 榜单排名靠前
2. **国内可访问 ——** ModelScope 镜像不受 HuggingFace 网络限制，下载稳定
3. **维度适中 ——** 512 维在精度和存储成本之间取得平衡，比 768/1024 维更轻量
4. **本地部署 ——** 不需要调用付费 Embedding API（如 OpenAI text-embedding-3），无使用量限制
5. **small 版本 ——** 推理速度快，适合本项目规模的实时检索

**加载方式：**
```python
# 延迟加载，避免启动时阻塞
from modelscope.models import Model
from modelscope.pipelines import pipeline

self._model = Model.from_pretrained(
    "iic/nlp_gte_sentence-embedding_chinese-small"
)
self._pipeline = pipeline(
    "sentence-embedding",
    model=self._model
)
```

---

### Q7: 如果让你换一个嵌入模型，你会考虑哪些？

**答：** 会从以下几个维度评估：

**更强中文能力：**
- `BAAI/bge-large-zh-v1.5`（1024 维）—— C-MTEB 榜首，但维度更高、推理更慢
- `text2vec-large-chinese`（1024 维）—— 基于 CoSENT，中文语义匹配强

**多语言场景：**
- `intfloat/multilingual-e5-large`（1024 维）—— 支持 100+ 语言
- OpenAI `text-embedding-3-large` —— 效果最好，但有 API 成本

**轻量化：**
- `all-MiniLM-L6-v2`（384 维）—— 英文为主，极致速度
- `BAAI/bge-small-zh-v1.5`（512 维）—— 同维度下 BGE 系列更优

**选型标准：** MTEB/C-MTEB 排名 → 维度 → 推理速度 → 部署便利性 → 成本

---

## 四、分块策略

### Q8: 你的文档分块（Chunking）策略是什么？为什么这样设计？

**答：** 采用**基于 Markdown 标题的结构化分块**，在 `indexer.py` 的 `_parse_md()` 函数中实现：

```python
def _parse_md(self, content: str) -> list[dict]:
    """按 ## 标题拆分 Markdown 文档为多个块"""
    sections = re.split(r'\n(?=## )', content)
    chunks = []
    for i, section in enumerate(sections):
        # 提取段落标题（如 "## 摘要"）
        heading_match = re.match(r'^## (.+)$', section, re.MULTILINE)
        heading = heading_match.group(1) if heading_match else "正文"

        if section.strip():
            chunks.append({
                "section": heading,       # 块所属段落
                "content": section.strip(),
                "chunk_index": i
            })
    return chunks
```

**为什么不用其他策略：**

| 策略 | 本项目适用性 |
|------|-------------|
| **固定长度分块**（如 512 token） | 不适用 —— 会切断语义完整的问答对 |
| **语义分块**（按相似度边界切） | 过度设计 —— 知识库文档本身有结构 |
| **递归分块** | 不适用 —— Markdown 标题已经提供了自然边界 |
| **按标题分块（本项目）** | 最合适 —— 每篇文档按 `## 摘要`、`## 正文`、`## 关键信息` 分块，每个块语义独立 |

**核心优势：**
- 每个 `##` 段落是语义完整的最小单元
- 不会在句子中间切断
- 检索结果可以精确到文档的具体段落（如 "正文" vs "关键信息"）
- GPT/LLM 原生理解 Markdown 结构

---

### Q9: 分块大小对检索效果有什么影响？你会如何调优？

**答：**

**分块太小：**
- 语义信息不完整，嵌入质量下降
- 检索到的片段缺少上下文，LLM 难以理解
- 但会更精确地定位到具体句子

**分块太大：**
- 单个块包含多个主题，检索精度下降
- 嵌入向量被稀释，"平均化"导致区分度降低
- 但提供更完整的上下文

**本项目的折衷：** 每个 `##` 段落通常在 100-500 字，是检索的黄金区间。

**如果调优，我会：**
1. **分析实际块大小分布** —— 统计当前分块后的字符数分布，找出异常大/小的段落
2. **合并过小的块** —— 相邻的短段落（<50 字）合并
3. **拆分过大的块** —— 超长段落（>1000 字）用递归分块或按句号/换行再拆
4. **A/B 测试** —— 构造 10-20 个测试查询，对比不同策略的 hit@3/hit@5
5. **加入重叠** —— 块与块之间保留 10-20% 的内容重叠，避免边界信息丢失

---

## 五、检索增强生成

### Q10: 你的 RAG 引擎具体是怎么工作的？从查询到结果的全流程是什么？

**答：** 完整流程如下（`rag_engine.py` + `searcher.py`）：

```
用户查询: "如何准备 AI Agent 面试？"
    │
    ▼
[1] 查询理解
    - jieba 分词: ["如何", "准备", "AI", "Agent", "面试"]
    - 提取关键词用于关键字匹配
    │
    ▼
[2] 关键字检索（并行）
    - 扫描所有 .md 文件的 frontmatter
    - 用 jieba 匹配 tags 和 keywords 字段
    - 得分: 匹配到的关键词数 / 总关键词数
    │
    ▼
[3] 语义检索（并行）
    - 查询文本 → ModelScope 嵌入 → 512 维向量
    - ChromaDB cosine 相似度搜索
    - 得分: 1 - cosine_distance/2（映射到 0-1）
    │
    ▼
[4] 混合融合
    - combined_score = (keyword_score + semantic_score) / 2
    - 路径级去重
    - 按分数降序排列
    │
    ▼
[5] 结果格式化
    - 取 top_k 结果
    - 格式化为结构化 Markdown:
      ### [{score:.3f}] {title}
      - 类别: {category} > {sub_category}
      - 路径: {file_path}
      - 来源: {source_url}
      ---
      {content_preview}
    │
    ▼
[6] 安全过滤
    - _is_in_scope(): 确保返回路径在项目目录下
    - 过滤掉 _index.md 等非内容文件
```

**如果是 RAG（带 LLM 回答生成），还会多一步：**

```
[7] 构建 Prompt
    system: "你是一个 AI Agent 面试辅导专家..."
    context: [检索到的 top_k 文档]
    user_query: "如何准备 AI Agent 面试？"
    │
    ▼
[8] LLM 生成
    → 基于检索文档 + 原始查询生成带引用的答案
```

---

### Q11: RAG 中常见的 "检索到但不相关" 问题你是怎么处理的？

**答：** 多层防御策略：

1. **混合检索本身就是第一道防线** —— 关键字检索提供精确匹配的锚点，语义检索提供泛化能力，两者结合天然过滤掉只有一种方法命中的噪声
2. **分数归一化与阈值** —— 语义得分映射到 0-1，只返回高于一定相关度的结果
3. **分类法前置过滤** —— 如果查询隐含了类别倾向（如"技术面"），可以先用分类过滤
4. **元数据辅助判断** —— 检索时利用 ChromaDB 的 `where` 过滤，如 `{"category": "技术编程"}`
5. **Reranker 重排序**（可优化方向）—— 在检索后用 Cross-Encoder 模型（如 `bge-reranker`）对 top N 结果做精排

**当前实现中最关键的机制：**
```python
# 仅语义命中时，得分折半
for path, score in semantic_results.items():
    if path not in combined_scores:
        combined_scores[path] = score / 2
```
这个设计体现了对"单一来源命中"的不信任——只有语义检索命中时，得分打对折，减少了纯语义检索的噪声。

---

## 六、OCR 集成

### Q12: 为什么要集成 OCR？技术选型上 PaddleOCR vs EasyOCR 你是怎么考虑的？

**答：**

**为什么需要 OCR：** 小红书的面试经验帖很多是**图片形式**（长截图、思维导图、聊天记录截图），关键信息在图片里。如果只抓 HTML DOM 文本，会丢失 50%+ 的内容。

**PaddleOCR vs EasyOCR 对比：**

| 维度 | PaddleOCR | EasyOCR |
|------|-----------|---------|
| **中文识别准确率** | 高（专门针对中文训练） | 中（通用多语言） |
| **速度** | 快（有 GPU 加速） | 较慢 |
| **安装大小** | 大（~500MB） | 小（~200MB） |
| **角度分类** | 内置文本方向检测 | 无 |
| **部署难度** | 需要 PaddlePaddle 框架 | 纯 PyTorch |
| **长文本** | 优秀 | 一般 |

**我的策略是回退链（Fallback Chain）：**
```python
try:
    self._ocr = PaddleOCR(use_angle_cls=True, lang='ch')
except Exception:
    try:
        self._ocr = EasyOCR(['ch_sim', 'en'])
    except Exception:
        self._ocr = None  # 禁用 OCR，但不阻塞流程
```

**关键设计点：**
- PaddleOCR 优先（中文效果好），EasyOCR 做后备
- OCR 失败不阻塞整个 Pipeline——图片仍然保存，只是缺少 OCR 文本
- 图片预过滤：最小 200x200 像素、最小 10KB，过滤掉头像/图标
- 多图合并时做了重叠检测，避免拼接重复文本

---

### Q13: OCR 文本质量不高时你怎么处理？

**答：** 在 Pipeline 的**阶段 3（格式化）**中，调用 LLM 专门做 OCR 文本清理：

```python
system_prompt = """
你是一个文本格式化专家。请对以下从小红书帖子中提取的内容进行清理和结构化：

1. 修复 OCR 导致的断行和错字（如 "GlL" → "GIL"）
2. 将问答类内容整理为清晰的 Q&A 格式
3. 去除广告、无关链接和冗余空白
4. 按 Markdown 格式组织内容，使用 ## 标题
5. 保持原有的事实信息不变，不要添加或删减
"""
```

**清理效果示例：**
```
OCR 原始输出:
"Al Agent 面试经 验\nQ：什么是\nAgent的规划能力？\nA：规划能力是指\nAgent能够..."

LLM 清理后:
## 正文
**Q：什么是 Agent 的规划能力？**
**A：** 规划能力是指 Agent 能够将复杂任务分解为子任务，并制定执行顺序和策略的能力。
```

这就是为什么我的 Pipeline 在抓取和分类之间插入了格式化阶段——LLM 的文本理解能力弥补了 OCR 的不足。

---

## 七、LLM 集成

### Q14: 你的 LLM 调用是怎么设计的？为什么选 DeepSeek？

**答：**

**调用方式：** 使用 OpenAI 兼容 SDK，统一抽象：
```python
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["ANTHROPIC_AUTH_TOKEN"],
    base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/v1")
)

response = client.chat.completions.create(
    model=os.environ.get("ANTHROPIC_MODEL", "deepseek-chat"),
    messages=[...],
    temperature=0.3,  # 分类任务用低温
    max_tokens=2000
)
```

**为什么选 DeepSeek：**
1. **中文能力强** —— 对中文内容的理解和生成质量与 GPT-4 接近
2. **成本低** —— API 价格约为 GPT-4 的 1/10
3. **兼容 OpenAI SDK** —— 零迁移成本，随时可以切换到其他兼容 API
4. **国内可访问** —— 不需要代理
5. **上下文窗口大** —— 支持 32K tokens，适合处理长帖子内容

**LLM 在项目中的三个角色：**
- **格式化器（Formatter）：** 清理 OCR 文本，结构化内容（temperature=0.1）
- **分类器（Classifier）：** 分类、摘要、关键词、情感分析（temperature=0.3）
- **RAG 回答生成（计划中）：** 基于检索结果生成答案（temperature=0.7）

---

### Q15: LLM 调用失败时你怎么处理？

**答：** 多层容错机制（在 `classifier.py` 中）：

```python
def classify(self, post: XHSPost) -> ClassifiedPost:
    try:
        # 1. 尝试 LLM 分类
        response = self._call_llm(prompt)
        result = self._parse_json(response)
        return ClassifiedPost(category=result["category"], ...)

    except (APIError, JSONDecodeError) as e:
        # 2. LLM 失败 → 基于规则的回退
        logger.warning(f"LLM分类失败，使用规则回退: {e}")
        return self._rule_based_fallback(post)

def _rule_based_fallback(self, post: XHSPost) -> ClassifiedPost:
    """基于关键词和长度的简单分类"""
    content = post.title + post.body
    # 关键词匹配
    if any(kw in content for kw in ["Python", "代码", "算法"]):
        category = "技术编程"
    elif any(kw in content for kw in ["面试", "简历", "offer"]):
        category = "职业发展"
    else:
        category = "未分类"

    # 质量分基于互动数据估算
    quality = min(10, (post.like_count // 10) + (post.collect_count // 5) + 3)

    return ClassifiedPost(category=category, quality_score=quality, ...)
```

**容错层次：**
1. API 网络错误 → 重试（最多 3 次，指数退避）
2. JSON 解析失败 → 正则提取 + 规则回退
3. 内容过短（<100 chars）→ 直接走规则回退，不浪费 API 调用
4. 全局 API key 未配置 → 标记为未分类，不阻塞 Pipeline

---

## 八、数据管道设计

### Q16: 你的数据 Pipeline 是怎么设计的？有哪些保证数据完整性的机制？

**答：**

**Pipeline 设计模式 —— 阶段性 + 可恢复：**

```
搜索 → 抓取 → 格式化 → 分类 → 构建
  ↓       ↓        ↓        ↓        ↓
JSON    JSON     JSON     JSON     Markdown
文件    文件     文件     文件     文件
```

每个阶段之间有 JSON 文件作为中间产物，带来几个好处：
- **可恢复性：** 任何一个阶段失败，不需要从头开始
- **可调试性：** 可以检查中间产物的质量
- **可独立运行：** 可以单独运行某个阶段（如只重新分类）

**断点续传机制（scraper.py）：**
```python
checkpoint_file = "output/checkpoint_scrape.json"

def load_checkpoint(self) -> set[str]:
    """加载已抓取的 post_id 集合"""
    if os.path.exists(checkpoint_file):
        with open(checkpoint_file) as f:
            return set(json.load(f))
    return set()

def save_checkpoint(self, post_id: str):
    """保存已抓取的 post_id"""
    scraped = self.load_checkpoint()
    scraped.add(post_id)
    with open(checkpoint_file, "w") as f:
        json.dump(list(scraped), f)
```

当 `--resume` 参数为 True 时，自动跳过已抓取的帖子。这在处理大量帖子时至关重要——

**速率限制与反爬：**
- 关键词间延迟 5-10 秒随机
- 帖子间延迟 3-8 秒随机
- 验证码检测 → 提示用户手动处理（`--no-headless`）
- 持久化浏览器 Profile（`~/.xhs_browser_profile/`）保持登录状态

---

### Q17: 如果你要部署到生产环境，这个 Pipeline 有哪些需要改进的地方？

**答：**

1. **异步化：** 当前是同步 Pipeline（Playwright 同步 API），改成异步（`playwright.async_api` + `asyncio`）可以大幅提升吞吐
2. **消息队列：** 用 Redis/RabbitMQ 解耦各阶段，支持分布式执行
3. **OCR 服务化：** 将 PaddleOCR 部署为独立服务（如 Triton Inference Server），避免每次加载模型
4. **LLM 调用限流：** 加入 Token Bucket 限流器，防止 API 超额调用
5. **错误重试策略：** 统一的指数退避重试 + 死信队列
6. **监控告警：** Pipeline 各阶段的 Prometheus 指标（处理数量、耗时、错误率）
7. **增量更新：** 当前是全量 Pipeline，需要增量方案（按时间范围抓取新帖）
8. **数据版本管理：** 知识库内容加版本号，支持回滚

---

## 九、MCP 服务器

### Q18: 你的 MCP 服务器是做什么的？为什么选择 MCP 协议？

**答：** MCP（Model Context Protocol）是 Anthropic 提出的 AI Agent 与外部工具交互的标准协议。我的项目通过 MCP 服务器对外暴露三个工具：

```
mcp_server/xhs_server.py
├── run_pipeline(keyword, count)  —— 全流程，600s 超时
├── search_xhs(keyword, count)    —— 仅搜索，90s 超时
└── search_kb(query, top_k)       —— 知识库搜索，30s 超时
```

**为什么选 MCP：**
- **标准化：** Claude Code 原生支持 MCP 协议，无需自定义集成
- **工具发现：** AI Agent 可以自动发现可用工具及其参数
- **安全性：** 工具级别权限控制，用户可审批每次调用
- **解耦：** 知识库服务独立部署，可以被多个 Agent 共享

**关键技术实现：**
```python
# 使用 asyncio + ThreadPoolExecutor 处理同步阻塞操作
executor = ThreadPoolExecutor(max_workers=4)

async def run_pipeline(keyword: str, count: int = 5):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        executor,
        lambda: run_full_pipeline(keyword, count)  # 同步阻塞操作
    )
    return result
```

MCP 使用 stdio 传输，日志必须输出到文件（`output/mcp_server.log`）而不是 stdout，否则会干扰协议通信。

---

## 十、测试与质量

### Q19: 62 个测试是怎么设计的？如何 Mock 外部依赖？

**答：** 测试按模块分层，使用 `pytest` + `unittest.mock`：

**Mock 策略：**

| 外部依赖 | Mock 方式 | 原因 |
|---------|----------|------|
| Playwright | `unittest.mock.patch` mock 浏览器/page 对象 | 不需要真实浏览器 |
| PaddleOCR/EasyOCR | mock OCR 返回固定文本 | 不需要下载模型 |
| DeepSeek API | mock `openai.OpenAI` 返回固定 JSON | 不需要网络和 API key |
| ChromaDB | mock client/collection | 测试逻辑不测试数据库 |
| ModelScope | mock pipeline 返回固定向量 | 不需要下载模型 |
| 文件系统 | `tmp_path` fixture | 隔离测试环境 |

**关键测试设计示例（test_classifier.py）：**
```python
def test_parse_valid_json_response():
    """测试正常 JSON 解析"""
    classifier = Classifier(api_key="test")
    result = classifier._parse_json('{"category": "技术编程", ...}')
    assert result["category"] == "技术编程"

def test_parse_wrapped_json_with_markdown():
    """测试被 Markdown 包裹的 JSON（LLM 常见输出格式）"""
    response = '```json\n{"category": "职业发展"}\n```'
    result = classifier._parse_json(response)
    assert result["category"] == "职业发展"

def test_parse_invalid_json_fallback():
    """测试非法 JSON 的回退逻辑"""
    result = classifier._parse_json("invalid response")
    assert result == {}  # 返回空字典，触发规则回退
```

**测试数据构造原则：**
- 用 dataclass 构造最小有效对象（`XHSPost(title="test", body="...")`）
- 测试边界情况：空输入、超长文本、特殊字符
- 每个测试只测一个行为（单一职责）

---

## 十一、开放性问题

### Q20: 如果你的知识库从 100 篇扩展到 10 万篇，系统需要做哪些改造？

**答：**

**存储层：**
- Markdown 文件 → 关系数据库（PostgreSQL）存储元数据，对象存储（MinIO/S3）存储 Markdown 正文和图片
- ChromaDB → Milvus 或 Qdrant（分布式向量数据库，支持分片和副本）

**索引层：**
- 单机索引 → 分布式批量索引（Spark/Ray）
- 实时增量索引（新帖子抓取后自动索引，而非手动触发）

**检索层：**
- 加入粗排+精排两阶段：
  - **粗排：** 向量检索 + 倒排索引混合，召回 top 100
  - **精排：** Cross-Encoder Reranker（如 bge-reranker-v2）重排 top 10
- 加入查询改写（Query Rewriting）—— 用 LLM 将用户查询改写为 2-3 个变体，多路召回合并

**Pipeline 层：**
- 调度器从 Python CLI 改为 Airflow/Temporal 工作流引擎
- LLM 调用加入批处理（Batch API），降低成本和限流风险
- OCR 服务化部署，GPU 资源池化

**监控：**
- 检索延迟 P50/P99
- 检索命中率（hit@k）
- 用户反馈闭环（点赞/踩，用作后续微调信号）

---

### Q21: 你在这个项目中遇到的最大技术挑战是什么？怎么解决的？

**答：** 最大的挑战是**小红书的反爬机制**。

小红书有很强的反爬措施：
1. 直接 HTTP 请求会被拦截（需要 `xsec_token` 签名）
2. 未登录状态搜索结果显示不全
3. 频繁请求触发滑块验证码
4. 帖子详情需要 JavaScript 渲染

**我的解决方案 —— "模拟真实用户" 策略：**

1. **浏览器的用户数据目录持久化：** Playwright 使用 `~/.xhs_browser_profile/` 存储 Chrome 用户数据，登录状态跨会话复用，不需要每次操作都登录
2. **模拟点击而非直接 URL：** 不直接访问帖子 URL（需要 token），而是点击搜索结果中的帖子卡片，让小红书自己生成 token 并打开侧边面板
3. **随机延迟：** 所有操作间加入随机延迟（搜索 5-10s，帖子间 3-8s），模拟人类浏览节奏
4. **验证码友好处理：** 检测到验证码时不暴力重试，而是提示用户用 `--no-headless` 模式手动处理
5. **优雅降级：** 抓取失败/验证码/限流都不 crash，记录错误后继续处理剩余帖子

**教训：** 对抗反爬不能只靠技术手段，更重要的是理解平台的正常用户行为模式，让自动化行为看起来像正常用户。

---

### Q22: 你觉得这个项目还有哪些可以做的优化？

**答：**

**技术优化：**
1. **Reranker 精排** —— 在混合检索后加 bge-reranker 做 Cross-Encoder 重排序，提升 top-3 准确率
2. **查询改写** —— 用 LLM 将用户查询扩展为多个变体，多路召回
3. **HyDE（假设文档嵌入）** —— 让 LLM 先生成一个假设答案，用假设答案去检索（比直接查询更好）
4. **检索评估** —— 构建标注数据集，量化 MRR、NDCG、Recall@k
5. **RAGAS 评估框架** —— 评估生成质量（忠实度、相关性、正确性）

**产品优化：**
6. Web UI —— 目前只有 CLI + MCP，做一个搜索界面
7. 用户反馈按钮 —— 每个检索结果旁边加"有用/无用"，用于调优
8. 定时自动抓取 —— Cron 定期搜索新帖并更新知识库
9. 多数据源 —— 除了小红书，加入知乎、微信公众号、即刻等

---

## 十二、进阶技术问题

### Q23: 解释一下你的混合检索中的分数融合策略，为什么用简单平均而不是加权平均？

**答：** 当前使用 `(keyword_score + semantic_score) / 2` 的简单平均。

**为什么当前阶段用简单平均：**
- **没有标注数据来学权重** —— 加权平均（如 `0.7 * keyword + 0.3 * semantic`）需要基于检索评估指标（MRR、NDCG）调参，目前项目没有标注数据集
- **两种方法互相补充** —— 关键字命中时语义分通常也不低；反之，仅语义命中时得分折半（`score / 2`），实际上已经引入了隐式权重
- **简单平均是最小假设** —— 在没有先验知识的情况下，假设两种方法同等重要是最安全的

**如果要引入加权，可以用：**
1. **RRF（Reciprocal Rank Fusion）：**
   ```python
   score = 1 / (k + rank_keyword) + 1 / (k + rank_semantic)
   ```
   不依赖原始分数大小，只看排序位置

2. **学习权重（Learning to Rank）：**
   - 构建标注数据集（query, doc, relevance_label）
   - 用 LambdaMART 或 BERT 学习最优融合权重
   - 甚至可以加入更多特征：文档长度、发布时间、点赞数、类别匹配度

---

### Q24: 你的 embedding 模型的维度是 512，你是怎么评估这个维度是足够的？

**答：**

**理论依据：**
- GTE-small 模型在 C-MTEB 基准上的表现已经接近 BGE-base（768 维），说明 512 维对中文语义编码是足够的
- 信息论上，512 维浮点向量可以编码的信息量远超过知识库几千篇文档所需

**实践验证：**
- 在测试中对比了关键字搜索和语义搜索的结果，语义搜索能正确找到同义表达（如"Agent 面试" vs "AI Agent 面试题"）
- 余弦距离分布：相关文档通常在 0.05-0.4 之间，不相关文档在 0.6-1.0 之间，区分度明显

**如果要量化评估：**
1. 构造标注数据集（20-50 个查询 + 相关文档标注）
2. 计算 Recall@k（k=3,5,10）
3. 对比 384 维（all-MiniLM）、512 维（当前）、768 维（BGE-base）、1024 维（BGE-large）
4. 画 Recall-Dimension 曲线，找到收益递减的拐点

---

### Q25: 你在 Pipeline 的格式化阶段让 LLM 修复 OCR 错误，如何确保 LLM 不会"过度修正"或编造内容？

**答：** 这是 RAG 系统的核心问题之一——"修复"与"篡改"的边界。

**我的控制手段：**

1. **使用低 Temperature（0.1-0.3）：** 降低 LLM 的"创造性"，让它倾向于输出最保守的修正
2. **严格的 System Prompt 约束：**
   ```
   关键原则：
   - 只修复明显的 OCR 错字和断行
   - 不要添加原文没有的信息
   - 不要删除任何事实性内容
   - 不确定的地方保持原样，标注 [不确定: 原文]
   - 结构化时使用 ## 标题，但不要改变内容顺序
   ```
3. **保留原始数据：** OCR 原始文本和 LLM 格式化后的文本都保留在文档中，用户可以对比
4. **格式化失败回退：** 如果 LLM API 不可用，直接使用原始 OCR 文本，标注"未经格式化"

**这也是 RAG 相比微调的优势：** 原始内容始终可追溯（文档中有原文链接和 OCR 原始文本），用户可以去验证。

---

### Q26: 你能画一下整个系统的数据流图吗？

**答：**

```
┌─────────────────────────────────────────────────────────────────────┐
│                          数据摄入管线 (Ingestion Pipeline)            │
│                                                                     │
│  [用户]                     [小红书平台]                              │
│    │                           │                                    │
│    │  python xiaohongshu.py    │                                    │
│    │  run --keywords "agent面试" │                                   │
│    ▼                           ▼                                    │
│  ┌──────────┐   Playwright   ┌──────────┐                          │
│  │ 1. 搜索   │ ─────────────→ │ XHS 搜索  │                          │
│  │ Searcher │ ←───────────── │    结果    │                          │
│  └────┬─────┘                └──────────┘                          │
│       │ search_results.json                                         │
│       ▼                                                            │
│  ┌──────────┐   Playwright   ┌──────────┐                          │
│  │ 2. 抓取   │ ─────────────→ │ XHS 帖子  │                          │
│  │ Scraper  │ ←───────────── │ 详情+图片  │                          │
│  └────┬─────┘                └──────────┘                          │
│       │                                                            │
│       │ 图片 → PaddleOCR → OCR 文本                                  │
│       │ scraped_posts.json                                          │
│       ▼                                                            │
│  ┌──────────┐   DeepSeek API  ┌──────────┐                         │
│  │ 3. 格式化 │ ──────────────→ │ LLM 清理  │                         │
│  │ Formatter│ ←────────────── │ 结构化    │                         │
│  └────┬─────┘                 └──────────┘                         │
│       │ formatted_posts.json                                        │
│       ▼                                                            │
│  ┌──────────┐   DeepSeek API  ┌──────────┐                         │
│  │ 4. 分类   │ ──────────────→ │ LLM 分类  │                         │
│  │Classifier│ ←────────────── │ +摘要+关键 │                         │
│  └────┬─────┘                 │ 词+情感    │                         │
│       │                       └──────────┘                         │
│       │ classified_posts.json                                       │
│       ▼                                                            │
│  ┌──────────┐                                                      │
│  │ 5. 构建   │ ──→ output/knowledge_base/                           │
│  │ Builder  │     ├── INDEX.md                                      │
│  └──────────┘     ├── metadata.json                                │
│                    └── categories/{类别}/{slug}_{post_id}.md         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          检索管线 (Retrieval Pipeline)               │
│                                                                     │
│  ┌──────────┐   ModelScope    ┌──────────┐                         │
│  │ 6. 索引   │ ──────────────→ │ 嵌入模型   │                         │
│  │ Indexer  │ ←────────────── │ 512 维向量 │                         │
│  └────┬─────┘                 └──────────┘                         │
│       │ 分块 → 嵌入 → ChromaDB                                      │
│       ▼                                                            │
│  ┌──────────────────────────────────────────┐                       │
│  │            output/chroma_db/              │                      │
│  │    Collection: "xhs_knowledge"            │                      │
│  │    Metric: cosine                         │                      │
│  │    Dim: 512                               │                      │
│  └──────────────┬───────────────────────────┘                       │
│                 │                                                   │
│                 ▼                                                   │
│  ┌──────────────────────────────────────────┐                       │
│  │  7. 搜索 (Searcher + RAG Engine)          │                      │
│  │                                           │                      │
│  │  查询 ─┬─→ jieba 分词 → 关键字匹配        │                      │
│  │       │         (frontmatter tags/kw)      │                      │
│  │       │                                    │                      │
│  │       ├─→ 嵌入 → ChromaDB 语义搜索         │                      │
│  │       │         (cosine similarity)         │                      │
│  │       │                                    │                      │
│  │       └─→ 混合融合 → 去重 → 排序           │                      │
│  │                 │                          │                      │
│  │                 ▼                          │                      │
│  │         结构化 Markdown 结果               │                      │
│  └──────────────────────────────────────────┘                       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         对外接口层                                   │
│                                                                     │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐      │
│  │ CLI 命令      │  │ MCP Server        │  │ Claude Code       │      │
│  │ xiaohongshu  │  │ run_pipeline     │  │ 子代理 + 技能     │      │
│  │ .py          │  │ search_xhs       │  │ /xhs-search       │      │
│  │              │  │ search_kb        │  │                   │      │
│  └──────────────┘  └──────────────────┘  └──────────────────┘      │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 附录：技术栈速查表

| 层级 | 技术 | 用途 |
|------|------|------|
| **浏览器自动化** | Playwright (Python) | 搜索 + 抓取小红书帖子 |
| **OCR** | PaddleOCR / EasyOCR | 图片文字提取 |
| **分词** | jieba | 中文分词、关键字匹配 |
| **嵌入模型** | ModelScope GTE-small (512d) | 文本向量化 |
| **向量数据库** | ChromaDB | 向量存储 + 语义搜索 |
| **LLM** | DeepSeek Chat (OpenAI SDK) | 格式化、分类、摘要 |
| **配置管理** | YAML + 环境变量 | 分类法、API 配置 |
| **数据格式** | Markdown + YAML frontmatter | 知识库文档存储 |
| **测试** | pytest + unittest.mock | 单元测试（62 个） |
| **MCP 协议** | mcp (Python SDK) | AI Agent 工具暴露 |
| **编排** | Python CLI (argparse) | Pipeline 调度 |
| **包管理** | pip | 直接依赖安装 |
