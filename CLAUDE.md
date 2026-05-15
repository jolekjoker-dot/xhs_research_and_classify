# CLAUDE.md

## MCP 工具优先

当用户请求匹配以下场景时，**必须优先调用专用 MCP 工具**，禁止使用 Grep/Glob/WebSearch 等通用工具替代：

| 用户意图 | 使用工具 | 禁止替代 |
|---------|---------|---------|
| 搜索本地知识库 | `search_kb` | Grep、Glob |
| 搜索小红书内容 | `search_xhs` | WebSearch、WebFetch |
| 采集小红书内容并构建知识库 | `run_pipeline` | 手动分步操作 |

关键词触发：知识库、本地搜索、RAG、向量检索、内容检索、小红书、XHS、采集、pipeline。
