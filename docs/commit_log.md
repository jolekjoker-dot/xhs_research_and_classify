# Commit 记录

用于快速定位回退目标。每个 commit 记录改动范围、影响文件、回退说明。

---

## 2026-05-22

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
