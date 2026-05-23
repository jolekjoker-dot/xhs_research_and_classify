# 优化方案

## 一、知识图谱

### 1.1 当前问题

知识以独立 Markdown 文件存储，帖子之间无关联。但分类器已提取 `keywords`、`entities`、`category`、`subcategory`，这些是构建图谱的天然素材。

### 1.2 方案：Neo4j + 轻量 HTML 可视化

**核心技术栈**：

| 层 | 技术 | 用途 |
|----|------|------|
| 图数据库 | Neo4j Community Edition (Docker) | 存储节点和边，Cypher 查询 |
| Python 驱动 | `neo4j` (官方包) | 构建图谱、执行查询 |
| 启动方式 | `docker-compose.yml` 一键启动 | 免手动安装 Java/Neo4j |
| 可视化（主） | Neo4j Browser (内置) | 开发调试、Cypher 交互式探查 |
| 可视化（辅） | 独立 HTML (ECharts 力导向图) | 零依赖预览，可分享给非技术人员 |

**Docker Compose 配置**：

```yaml
# docker-compose.yml
version: "3.8"
services:
  neo4j:
    image: neo4j:5-community
    ports:
      - "7474:7474"   # HTTP (Browser)
      - "7687:7687"   # Bolt (driver)
    environment:
      NEO4J_AUTH: neo4j/password
      NEO4J_PLUGINS: "[\"apoc\"]"
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs

volumes:
  neo4j_data:
  neo4j_logs:
```

**图结构（与之前一致，存储引擎换为 Neo4j）**：

```
节点类型 (:Post, :Entity, :Keyword, :Category)

(:Category {name: "技术/Agent"})
(:Post {id: "69ad4bb9", title: "...", quality_score: 8, publish_date: "2026-03-01"})
(:Entity {name: "字节跳动", type: "company"})
(:Keyword {name: "Agent"})

边类型:
  (:Post)-[:BELONGS_TO]->(:Category)
  (:Post)-[:HAS_KEYWORD]->(:Keyword)
  (:Post)-[:MENTIONS]->(:Entity)
  (:Post)-[:SIMILAR_TO {weight: 0.85}]->(:Post)
  (:Post)-[:SUPPORTS]->(:Post)
  (:Post)-[:CONTRADICTS]->(:Post)
```

### 1.3 多跳查询示例

Cypher 的优势：一次查询穿透多层关系，NetworkX 需要手动遍历。

```cypher
-- Q1: "字节跳动的 Agent 团队在招什么人？面试问什么？"
MATCH (e:Entity {name: "字节跳动"})<-[:MENTIONS]-(p:Post)-[:HAS_KEYWORD]->(k:Keyword)
WHERE k.name IN ["面试", "招聘", "面经"]
RETURN p.title, p.category, k.name, p.quality_score
ORDER BY p.quality_score DESC

-- Q2: "从'Agent面试题'出发，找到所有相关知识，按主题聚类"
MATCH (start:Post {id: "69ad4bb9"})-[:SIMILAR_TO*1..3]-(related:Post)
MATCH (related)-[:BELONGS_TO]->(c:Category)
MATCH (related)-[:MENTIONS]->(e:Entity)
RETURN related.title, c.name, collect(DISTINCT e.name) as entities
LIMIT 30

-- Q3: "哪两类话题之间通过共同实体产生了桥接？"
MATCH (c1:Category)<-[:BELONGS_TO]-(p1:Post)-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(p2:Post)-[:BELONGS_TO]->(c2:Category)
WHERE c1.name <> c2.name
RETURN c1.name, c2.name, count(*) AS bridge_count, collect(DISTINCT e.name) AS shared_entities
ORDER BY bridge_count DESC

-- Q4: "2026年Q1大厂Agent面试趋势"
MATCH (e:Entity {type: "company"})<-[:MENTIONS]-(p:Post)-[:HAS_KEYWORD]->(k:Keyword)
WHERE k.name IN ["面试", "Agent"] AND p.publish_date >= "2026-01-01" AND p.publish_date < "2026-04-01"
RETURN e.name, count(p) AS post_count, avg(p.quality_score) AS avg_quality
ORDER BY post_count DESC
```

### 1.4 实现要点

```python
# src/knowledge_base/graph.py 核心接口

from neo4j import GraphDatabase

class KnowledgeGraph:
    def __init__(self, uri="bolt://localhost:7687", user="neo4j", password="password"):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    # ========== 写入 ==========

    def build(self, posts: list[ClassifiedPost]) -> dict:
        """全量构建图谱，返回 {nodes_created, edges_created}"""
        with self.driver.session() as session:
            return session.execute_write(self._build_tx, posts)

    def upsert_post(self, post: ClassifiedPost) -> None:
        """增量插入/更新单篇帖子及其关联"""

    # ========== 查询 ==========

    def find_related(self, post_id: str, top_k: int = 5) -> list[dict]:
        """SIMILAR_TO 边 + 共享实体/关键词，按权重排序"""

    def get_entity_network(self, entity_name: str, depth: int = 2) -> dict:
        """以实体为中心的多跳子图 {nodes, edges}，供前端渲染"""

    def get_topic_clusters(self) -> list[dict]:
        """Louvain 社区发现（需 APOC 插件），返回聚类结果"""

    def search(self, query: str) -> list[dict]:
        """模糊匹配节点名称 + 关键词 + 实体，返回匹配的帖子"""

    def get_bridge_topics(self) -> list[dict]:
        """查找桥接两个不同类别的共享实体（跨领域知识发现）"""

    # ========== 导出 ==========

    def export_subgraph(self, post_ids: list[str], path: str) -> None:
        """导出指定帖子的子图为 JSON，供 ECharts HTML 消费"""

    def close(self):
        self.driver.close()
```

### 1.5 双轨可视化

| 场景 | 方案 | 操作 |
|------|------|------|
| 开发调试、自由探查 | **Neo4j Browser** (`localhost:7474`) | 直接在 UI 里写 Cypher，结果自动渲染为图 |
| 分享给非技术人员 | **独立 HTML** (ECharts 力导向图) | 双击打开，拖拽浏览，搜索高亮 |
| 嵌入 INDEX.md | **Mermaid 图** | 生成类目-帖子层级关系图，GitHub 原生渲染 |

**独立 HTML 可视化**：

```
pipeline 构建完成后:
  graph.export_subgraph(all_post_ids, "output/knowledge_base/graph_viz.json")

启动脚本:
  python -m http.server 8080 --directory output/knowledge_base/
  # 浏览器打开 http://localhost:8080/graph.html

graph.html 读取 graph_viz.json，用 ECharts 渲染:
  - 节点按 Category 着色
  - 节点大小按 quality_score 缩放
  - 点击节点高亮邻域
  - 搜索框模糊匹配节点名并居中
  - 支持缩放、拖拽、全屏
```

### 1.6 与现有流程集成

原 pipeline 流程不变，构建阶段增加一步：

```
搜索 → 抓取 → 格式化 → 分类 → 构建MD → 构建图谱 → 导出可视化JSON
                                        ↑ 新增这一步
```

`cmd_run()` 在 `build_knowledge_base()` 之后调用 `graph.build(posts)` + `graph.export_subgraph(...)`。

Neo4j Docker 容器作为常驻服务（`docker compose up -d`），不随每次 pipeline 启停。即使 Neo4j 未启动，pipeline 也能正常完成前几步，图谱构建步骤降级为 warn 日志 + 跳过。

---

## 二、并行化加速

### 2.1 LLM 调用并行化（P0，收益最大）

**当前**：`formatter.py` 和 `classifier.py` 逐篇串行调用 DeepSeek API。
10 篇帖子 = 20 次串行调用 ≈ 350 秒。

**改为**：用 `concurrent.futures.ThreadPoolExecutor`（max_workers=5）并发调用，耗时降到 ~70 秒。

**改动位置**：

- `src/classify/formatter.py` — `format_posts()` 增加 `max_workers` 参数，内部用 `ThreadPoolExecutor.map()`
- `src/classify/classifier.py` — `classify_posts()` 同上

```python
# formatter.py 改造示意
from concurrent.futures import ThreadPoolExecutor, as_completed

def format_posts(posts: list[XHSPost], max_workers: int = 5) -> list[XHSPost]:
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_post = {executor.submit(_format_single, p): p for p in posts}
        for future in as_completed(future_to_post):
            results.append(future.result())
    return results
```

**注意**：需确认 DeepSeek API 的 rate limit，建议 max_workers 默认 5，通过 config 可调。

### 2.2 OCR 批处理（P1）

**当前**：对一篇帖子的多张图片逐张调用 PaddleOCR。

**改为**：PaddleOCR 的 `ocr()` 方法原生支持传入图片列表，内部会 batch 推理。

```python
# ocr.py 改造示意
def ocr_images_batch(image_paths: list[str]) -> dict[str, str]:
    """一次传多张图，PaddleOCR 内部 batch 推理"""
    results = ocr_engine.ocr(image_paths)  # 传入列表而非单张
    return {path: _extract_text(result) for path, result in zip(image_paths, results)}
```

图片 OCR 可以和格式化/分类并行：OCR 结果只需在格式化之前就绪，不需要阻塞抓取。

### 2.3 抓取并行化（P2）

**当前**：`config.py` 中 `scrape_max_concurrent = 1`，逐篇串行抓取。

**改为**：同一浏览器实例内打开多个 page/tab，`asyncio.gather` 控制并发。

```python
# scraper.py 改造示意
async def scrape_posts_browser(browser, results: list[SearchResult], max_concurrent: int = 3):
    semaphore = asyncio.Semaphore(max_concurrent)

    async def scrape_one(result):
        async with semaphore:
            page = await browser.new_page()
            try:
                return await _scrape_single_post(page, result)
            finally:
                await page.close()

    return await asyncio.gather(*[scrape_one(r) for r in results])
```

**注意**：并发数控制在 2-3，过高会触发小红书反爬。

### 2.4 关键词搜索并行化（P2）

**当前**：多个关键词在 `search_batch()` 中顺序搜索，之间有 5-10 秒延迟。

**改为**：每个关键词开独立 browser context 并行搜索。

```python
# searcher.py 改造示意
async def search_batch_parallel(keywords: list[str], count: int = 10) -> list[SearchResult]:
    async def search_one(kw):
        context = await browser.new_context()
        results = await _search_single_keyword(context, kw, count)
        await context.close()
        return results

    all_results = await asyncio.gather(*[search_one(kw) for kw in keywords])
    return _deduplicate([r for batch in all_results for r in batch])
```

---

## 三、增量索引

### 3.1 ChromaDB 增量索引（P0）

**当前**：`indexer.py` 的 `build_index()` 每次删除并重建整个集合。

**改为**：检查已有文档 ID，只对新文档计算 embedding 并插入。

```python
# indexer.py 改造示意
def build_index_incremental(docs: list[dict], collection_name: str = "xhs_kb"):
    collection = client.get_or_create_collection(collection_name)

    existing_ids = set(collection.get()["ids"]) if collection.count() > 0 else set()

    new_docs = [d for d in docs if d["id"] not in existing_ids]

    if new_docs:
        collection.add(
            ids=[d["id"] for d in new_docs],
            documents=[d["content"] for d in new_docs],
            metadatas=[d["metadata"] for d in new_docs],
            embeddings=[compute_embedding(d["content"]) for d in new_docs],
        )

    return len(new_docs), len(existing_ids)
```

同时保留 `build_index_full()` 用于首次构建或强制重建场景。

---

## 四、Pipeline 流水线化（P2，架构改动）

### 4.1 当前架构

```
搜索(全部) → 抓取(全部) → 格式化(全部) → 分类(全部) → 构建(全部)
```

每阶段必须等上一阶段完全结束。

### 4.2 流水线化架构

```
搜索 → [Queue] → 抓取(3并发) → [Queue] → 格式化+分类(5并发) → [Queue] → 构建
                           ↘ OCR(batch) ↗
```

用 `queue.Queue` 连接各阶段，搜到一篇就可以开始抓取，抓完一篇就可以开始格式化+分类。

```python
# cli.py cmd_run 改造示意
from queue import Queue
from threading import Thread

def cmd_run_pipelined(keywords, count):
    search_queue = Queue(maxsize=20)
    scrape_queue = Queue(maxsize=20)
    classify_queue = Queue(maxsize=20)

    # 生产者-消费者模式
    searcher_thread = Thread(target=search_producer, args=(keywords, search_queue))
    scraper_thread = Thread(target=scrape_worker, args=(search_queue, scrape_queue))
    llm_thread = Thread(target=llm_worker, args=(scrape_queue, classify_queue))
    builder_thread = Thread(target=builder_worker, args=(classify_queue,))

    for t in [searcher_thread, scraper_thread, llm_thread, builder_thread]:
        t.start()
    for t in [searcher_thread, scraper_thread, llm_thread, builder_thread]:
        t.join()
```

---

## 五、其他优化

### 5.1 LLM 响应缓存

相同正文（SHA256 hash 相同）跳过格式化+分类，直接复用缓存。

```python
# 缓存存储: output/cache/llm_format_{hash}.json
# 缓存存储: output/cache/llm_classify_{hash}.json
```

### 5.2 protobuf 加速

当前 `xiaohongshu.py` 入口强制设置 `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python`（纯 Python 实现）。如果 PaddleOCR 版本兼容，去掉此限制后 C++ 版 protobuf 快 10-50 倍。

```python
# 改为条件性设置：
try:
    import google.protobuf.internal.api_implementation
    if google.protobuf.internal.api_implementation.Type() != "cpp":
        os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "cpp"
except ImportError:
    os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
```

### 5.3 断点续传改进

当前已有 checkpoint 机制（`output/checkpoint_scrape.json`），但仅覆盖抓取阶段。可以扩展到全流程：每个阶段完成后记录进度，中断后从上次阶段继续。

### 5.5 图片去重

同一张图片可能出现在多篇帖子中（转发/引用场景）。在下载前计算图片 hash，跳过已存在的图片，节省存储和带宽。

---

## 五-BIS、检索重排（RRF + Cross-Encoder）

### 5B.1 当前问题

当前检索使用 **Bi-Encoder（GTE embedding）**：query 和文档分别独立编码为向量，通过余弦相似度排序。这是一种"双塔"架构——query 和 doc 彼此看不见，无法捕捉交互语义。

具体问题：
- keyword search 和 semantic search 的分数分布不同（keyword 是 [0,1] 匹配度，semantic 是余弦距离），直接平均不合理
- 语义相关但内容浅的帖子可能排在关键词精确匹配但质量高的前面
- 没有精排步骤，粗排结果直接返回给用户

### 5B.2 方案：RRF 融合 + Cross-Encoder 重排

检索链路从粗排一步到位改为两段式：

```
当前:  keyword + semantic → 简单平均 → top-5

改进:
  Step 1: keyword + semantic → RRF 融合 → 粗排 top-20
  Step 2: RRF top-20 → bge-reranker (Cross-Encoder) → 精排 top-5
```

**RRF (Reciprocal Rank Fusion)**：

```
RRF_score = Σ 1/(k + rank_i)    k 取 60
```

RRF 不看原始分值，只看排名位置。keyword 返回 10 条排 1-10，semantic 返回 10 条排 1-10——两个不可比较的分数分布，通过 RRF 统一为"在两个列表中各自的排名位置"。天然适合多路召回融合，比简单平均更稳健。

**bge-reranker-v2-m3 (Cross-Encoder)**：

| | Bi-Encoder (当前) | Cross-Encoder (新增) |
|---|---|---|
| 原理 | query/doc 独立编码→向量→余弦 | [query, doc] 拼接→模型→分数 |
| 交互 | 无，query 和 doc 彼此看不见 | 有，每个 token 都能看到对方的 token |
| 速度 | 快，doc 向量可预计算 | 慢，每对 query-doc 需完整推理 |
| 精度 | 中等 | 高——能理解同义词、否定、上下文 |
| 用法 | 全量粗排 | 仅对 top-20 精排 |

### 5B.3 实现要点

```python
# src/kb_agent/reranker.py（新文件）
from FlagEmbedding import FlagReranker

class ReRanker:
    def __init__(self):
        self._model = None

    @property
    def model(self):
        if self._model is None:
            self._model = FlagReranker(
                "BAAI/bge-reranker-v2-m3",
                use_fp16=True,
            )
        return self._model

    def rerank(self, query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
        """对候选文档重排序，返回 top-k"""
        if not candidates:
            return []
        pairs = [(query, c["document"]) for c in candidates]
        scores = self.model.compute_score(pairs)
        ranked = sorted(zip(candidates, scores), key=lambda x: -x[1])
        return [r for r, _ in ranked[:top_k]]
```

```python
# rag_engine.py hybrid_search 改造示意
def hybrid_search(query: str, top_k: int = 20, rerank: bool = True) -> list[dict]:
    kw_results = keyword_search(query, top_k=top_k)
    sem_results = semantic_search(query, top_k=top_k)

    # RRF 融合替代简单平均
    merged = _rrf_fusion(kw_results, sem_results, k=60)
    merged = merged[:top_k]

    if rerank:
        merged = _get_reranker().rerank(query, merged, top_k=5)

    return merged

def _rrf_fusion(list_a: list, list_b: list, k: int = 60) -> list:
    """Reciprocal Rank Fusion"""
    scores: dict[str, float] = {}
    for rank, item in enumerate(list_a):
        doc_id = item.get("id", item.get("path", str(rank)))
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
    for rank, item in enumerate(list_b):
        doc_id = item.get("id", item.get("path", str(rank)))
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    # resolve back to actual items from the higher-ranked source
    lookup = {item.get("id", item.get("path", str(i))): item
              for i, item in enumerate(list_a + list_b)}
    return [lookup[doc_id] for doc_id, _ in ranked if doc_id in lookup]
```

### 5B.4 改动范围

| 文件 | 改动 |
|------|------|
| `src/kb_agent/reranker.py`（新） | ReRanker 类，加载 bge-reranker-v2-m3，提供 `rerank()` |
| `src/kb_agent/rag_engine.py` | `hybrid_search` 改为 RRF 融合；新增 `rerank` 参数 |
| `src/kb_agent/searcher.py` | 检索前默认启用重排 |
| `src/config.py` | 新增 `rerank_enabled: bool = True` |

### 5B.5 新增依赖

```bash
pip install FlagEmbedding
```

### 5B.6 性能影响

- 模型首次加载：~30s，~500MB
- 每次检索额外耗时：~0.1-0.2s（仅对 20 对做 Cross-Encoder 推理）
- 检索质量：显著提升

---

## 五-TER、检索评测方案

### 5C.1 评测数据集

从知识库中选取 5 个典型 query，人工标注每个 query 的相关帖子 ID 集合：

| Query | 描述 | 相关帖数 |
|-------|------|---------|
| Agent面试经验 | 查找 Agent 岗位面试相关的面经和经验分享 | 5+ |
| 大厂大模型面经 | 大厂大模型岗位的面试题目和总结 | 5+ |
| 字节跳动面试 | 字节跳动公司的面试经验帖子 | 3+ |
| Agent开发技能 | Agent 开发需要掌握的技能讨论 | 2+ |
| 腾讯面试 | 腾讯公司的面试经验帖子 | 3+ |

标注格式（`tests/test_retrieval.py` 中的 `QUERY_LABELS`）：

```python
QUERY_LABELS = {
    "Agent面试经验": {
        "relevant": ["69c39f58", "69ad4bb9", "69ef3c9e", ...],
    },
    ...
}
```

### 5C.2 评测指标

| 指标 | 含义 | 公式 |
|------|------|------|
| **Recall@20** | 粗排 top-20 命中了多少相关帖 | `found_relevant / total_relevant` |
| **MRR** | 第一条相关帖的排名倒数 | `1 / rank_of_first_relevant` |
| **nDCG@5** | 精排前 5 的排序质量 | `DCG / IDCG`（相关=1，无关=0） |

### 5C.3 对比维度

四组并行跑同一 query，横向对比：

```
keyword_only      → 单路关键词召回基线
semantic_only     → 单路语义召回基线
RRF (hybrid)      → RRF 融合，不重排
RRF + rerank      → RRF 融合 + bge-reranker 重排
```

### 5C.4 运行方式

```bash
python -m pytest tests/test_retrieval.py -v -s
```

输出每 query 的四列对比表 + 重排前后 MRR/nDCG 增益。

### 5C.5 预期结果

| 对比 | 预期 |
|------|------|
| semantic vs keyword | semantic Recall 更高（语义泛化） |
| RRF vs semantic | RRF Recall 持平或更好（双路互补） |
| RRF+rerank vs RRF | MRR +10~20%，nDCG +10~20%（精排提升排序质量） |
| 速度 | semantic/keyword 并行，RRF 无额外开销，rerank ~0.1-0.2s |

---

## 五-QUATER、文搜图（文字查图片）

### 5D.1 当前问题

抓取时下载了大量帖子图片到 `output/knowledge_base/images/`，OCR 提取的文字已经融入帖子正文。但图片本身无法被直接检索——用户搜"Agent架构图"只能找到帖子，不能直接定位到那张图。

### 5D.2 方案

为每张图片建立独立索引，用 OCR 周围的上下文文字作为图片的"描述"：

```
MD 文件:  ...正文... ![img_00.jpg](../../images/img_00.jpg) ...更多文字...

ChromaDB xhs_images 集合:
  document: 图片周围 300 字上下文
  metadata: {image_path, post_title, post_category, post_url}
  embedding: GTE 同模型
```

检索链路：`text query → GTE embedding → xhs_images 向量匹配 → top-k 图片 + 来源`

### 5D.3 使用场景

1. **MCP 工具**：`search_images("Agent架构图")` → 返回匹配图片及来源帖子
2. **命令行**：`python xiaohongshu.py search-images --query "Agent架构图"`
3. **可视化**：未来可在 graph.html 旁加图片墙页面

### 5D.4 返回格式

默认返回 top-5 张图片，可调至 20：

```
搜索: "Agent架构图"
  ↓
1. images/69d51830_00.jpg — 相关性 0.89
   来源: 《腾讯Agent应用开发一面》
   上下文: "...面试官让我画Agent架构，ReAct模式..."

2. images/69b4daa5_01.jpg — 相关性 0.76
   来源: 《字节跳动Agent开发一面》
   上下文: "...多Agent协作，每个Agent有独立的tool set..."
```

### 5D.5 改动范围

| 文件 | 改动 |
|------|------|
| `src/kb_agent/image_indexer.py`（新） | `build_image_index()` — 解析 MD 中每张图片，建 ChromaDB `xhs_images` 集合 |
| `src/kb_agent/rag_engine.py` | 新增 `image_search(query, top_k)` |
| `src/kb_agent/searcher.py` | 新增 `search_images()` + `format_image_results()` |
| `mcp_server/xhs_server.py` | 新增 `search_images` MCP 工具 |
| `src/cli.py` | `cmd_run` pipeline 新增图片索引步骤；新增 `search-images` 子命令 |
| `src/config.py` | 新增 `image_search_enabled: bool = True` |

### 5D.6 预估

- 工时：~2h
- 新增依赖：无（复用 ChromaDB + GTE embedding）
- 索引规模：与图片数量成正比（当前 ~50 张，每条记录 ~300 字上下文）

---

## 六、实施优先级

| 优先级 | 改动项 | 预估工时 | 影响范围 | 效果 |
|--------|--------|---------|---------|------|
| **P0** | LLM 调用并行化 | ~1h | formatter.py, classifier.py, config.py | 3-5x 加速端到端 |
| **P0** | ChromaDB 增量索引 | ~0.5h | indexer.py | 避免重复 embedding 计算 |
| **P1** | OCR 批处理 | ~0.5h | ocr.py | 多图帖子显著加速 |
| **P1** | 知识图谱 Neo4j + ECharts 可视化 | ~8h | 新增 graph.py, docker-compose.yml, graph.html | 多跳查询 + 交互式可视化 |
| **P2** | 抓取并行化 | ~2h | scraper.py | 2-3x 加速抓取阶段 |
| **P2** | Pipeline 流水线化 | ~4h | cli.py + 各模块 | 端到端加速 |
| **P2** | LLM 响应缓存 | ~1h | formatter.py, classifier.py | 重复搜索场景有收益 |
| **P3** | RRF 多路融合 | ~0.5h | rag_engine.py | 检索排序更合理 |
| **P3** | bge-reranker 重排 | ~1h | 新增 reranker.py, rag_engine.py, searcher.py, config.py | 检索精准度质变 |
| **P3** | 搜索并行化 | ~1h | searcher.py | 多关键词场景加速 |
| **P4** | 检索评测脚本 + 标注 | ~0.5h | tests/test_retrieval.py | 量化改进效果 |
| **P5** | 文搜图 | ~2h | 新增 image_indexer.py, rag_engine.py, searcher.py, cli.py, mcp_server | 文字搜索图片 |
| **P6** | protobuf 加速 | ~0.5h | xiaohongshu.py | 底层序列化加速 |
| **P6** | 图片去重 | ~0.5h | scraper.py | 节省存储和带宽 |

---

## 七、按阶段实施建议

**第一阶段（1-2h）**：LLM 并行化 + ChromaDB 增量索引
- 改动小，收益大，风险低
- 端到端耗时从 ~6min 降到 ~2min（10 篇帖子场景）

**第二阶段（8-9h）**：知识图谱（Neo4j + ECharts 可视化）+ OCR 批处理
- 知识图谱：新增 `docker-compose.yml`（Neo4j）、`src/knowledge_base/graph.py`（Cypher 查询）、`output/knowledge_base/graph.html`（ECharts 可视化）
- OCR 批处理让图片密集型帖子处理更快
- 功能层面最大增量，多跳查询 + 双轨可视化

**第三阶段（6-8h）**：Pipeline 流水线化 + 抓取并行化
- 端到端进一步加速
- 需要较多测试（反爬风险）

**第四阶段（0.5h）**：检索评测脚本 + 标注
- 量化 P3 改进效果，跑评测脚本看 Recall/MRR/nDCG 对比

**第五阶段（2h）**：文搜图
- OCR 文字 → 图片索引，文字搜出匹配图片

**第六阶段（1h）**：protobuf 加速 + 图片去重
- 锦上添花，长久运行收益累积
