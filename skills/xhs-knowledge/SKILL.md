---
name: xhs-knowledge
description: 小红书知识库采集——根据关键词搜索、抓取内容、OCR识别、AI分类格式化、构建本地知识库
type: skill
---

# 小红书知识库采集

## 触发方式

- `/xhs-search <关键词>` — 搜索并构建知识库（默认5篇）
- `/xhs-search <关键词> --count 20` — 指定抓取数量
- `/xhs-search <关键词> --count 5 --no-headless` — 显示浏览器窗口

## 执行流程

1. 使用 Playwright 模拟浏览器在小红书搜索关键词
2. 模拟点击逐一打开帖子面板，抓取标题/正文/标签/评论/互动数据
3. 下载帖子内容图片，过滤头像和图标
4. 对有图片的帖子自动运行 PaddleOCR 提取文字
5. 调用 DeepSeek API 格式化内容（修复OCR错误/结构化/分离评论区）
6. 调用 DeepSeek API 分类、生成摘要、提取关键词和实体
7. 使用 Markdown 模板生成结构化知识库文档

## 知识库输出路径

`output/knowledge_base/`

```
output/knowledge_base/
├── INDEX.md              # 总索引
├── metadata.json         # 统计信息
└── categories/
    └── {分类}/
        ├── _index.md     # 分类索引
        └── {帖子}.md     # 单篇知识文档（frontmatter + 正文 + 图片链接）
```

## 约束

- 仅操作当前项目目录下的文件
- 遇到 "Login required" 时提示用户用 `--no-headless` 登录
- 运行结束询问是否清理调试文件
