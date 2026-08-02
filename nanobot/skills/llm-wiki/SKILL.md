---
name: llm-wiki
description: "LLM-maintained linked Markdown wiki that compiles source documents into a compounding knowledge base. Three-layer architecture: raw (immutable sources) → wiki (cross-linked Markdown) → schema (contract). Use when user asks to ingest documents, build a knowledge base, query a wiki, or maintain a linked note system. Triggers on: wiki, knowledge base, ingest, 知识库, 维基, 笔记, llm-wiki."
metadata: {"nanobot":{"emoji":"📚"}}
---

# LLM Wiki

编译优于检索——在摄入时就把原始文档转化为交叉链接的 Markdown 知识库，知识随时间叠加而非每次查询清零。

## 三层架构

| 层 | 目录 | 职责 | 谁写 |
|----|------|------|------|
| **Raw** | `wiki/raw/` | 不可变原始文档 | 用户放入 |
| **Wiki** | `wiki/wiki/` | 交叉链接的 Markdown 页面 | LLM 维护 |
| **Schema** | `wiki/schema.md` | Wiki 的行为契约与约束 | 初始化时生成，按需修订 |

## 目录结构

```
wiki/
├── schema.md              # Wiki 契约（页面类型、链接规则、格式要求）
├── raw/                   # 不可变原始文档（拖入即摄入）
├── wiki/
│   ├── dashboard.md       # 入口仪表盘：概览、最近变更、导航
│   ├── index.md           # 自动维护的全局目录（按主题→页面）
│   ├── sources/           # 每篇资料来源的摘要与元数据
│   ├── entities/          # 人物、组织、工具、系统
│   ├── concepts/          # 概念、方法、定义、框架
│   ├── comparisons/       # 跨来源对比
│   ├── debates.md         # 矛盾与分歧追踪
│   └── queries/           # 可复用的问答结果
└── .git/                  # 完整审计追踪
```

## 核心操作

### 1. 初始化 (`wiki init`)

用户说"初始化 wiki"或"创建知识库"时执行：

1. 创建上述完整目录结构
2. 生成 `schema.md`，包含：
   - 页面类型定义（source/entity/concept/comparison/query）
   - 每种页面的必需字段和格式模板
   - 链接规范（`[[wikilink]]` 语法、链接方向、反链规则）
   - 信任标注体系（`[!source]`、`[!analysis]`、`[!unverified]`、`[!gap]`）
3. 生成 `wiki/dashboard.md` 和 `wiki/index.md` 的初始框架
4. 提交到 git

### 2. 摄入 (`wiki ingest`)

这是核心——把 raw 目录里的文件转化为 wiki 页面。触发词："摄入"、"ingest"、"处理文档"、"消化"。

**流程：**

1. **扫描 raw 目录**，列出所有文件（用 Glob 或 `ls wiki/raw/`）
2. **逐文件处理**（处理一个提交一个，避免全量失败）：
   - 读取原始文档
   - 读取当前 `schema.md` 和相关 wiki 页面以了解已有知识
   - 为这篇来源写 `wiki/wiki/sources/<source-name>.md`（摘要 + 元数据 + 关键声明）
   - 提取实体、概念，写入对应的 `wiki/wiki/entities/` 和 `wiki/wiki/concepts/`
   - 在所有相关页面之间建立 `[[wikilink]]`
3. **发现矛盾时**记录到 `wiki/wiki/debates.md`
4. **更新 `wiki/index.md`**，加入新页面
5. **更新 `wiki/dashboard.md`**，记录最近变更
6. **提交**，commit message 描述摄入了什么

**页面格式约定：**

```markdown
---
source: wiki/raw/my-paper.pdf
ingested: 2026-08-02
type: source
---

# 来源：My Paper Title

## 摘要
[1-2 段总结]

## 关键声明
- [!source] 可直接引用的原文事实
- [!analysis] LLM 的综合判断
- [!unverified] 需人工确认的内容
- [!gap] 已知缺失或待补充

## 相关
- [[entities/author-name]]
- [[concepts/methodology]]
```

**重要原则：**
- 不重复内容——用 `[[wikilink]]` 引用，不在多处写同一件事
- 保持页面短小——超过 200 行就拆分
- 每个声明标注信任级别
- raw 目录下的文件绝不修改

### 3. 查询 (`wiki query`)

用户提问时，搜索 wiki 给出带引用的回答。触发词："查询 wiki"、"wiki query"、"根据知识库"。

**流程：**

1. 用 Grep 搜索 `wiki/wiki/` 目录，找相关页面
2. 精读最相关的 3-5 个页面
3. 综合回答，每个关键声明后标注来源页面（如 `[[sources/paper-name]]`）
4. 如果答案值得复用，写入 `wiki/wiki/queries/` 并更新索引

### 4. 健康检查 (`wiki lint`)

定期或在用户要求时执行。触发词："检查 wiki"、"wiki lint"、"知识库体检"。

**检查项：**

- **死链**：`[[wikilink]]` 指向不存在的页面
- **孤立页**：没有被任何其他页面链接的页面
- **过期页**：相关来源已在 raw 目录中被更新版本替换
- **矛盾**：不同页面声称冲突的事实
- **索引一致性**：`index.md` 是否漏掉了某些页面

用 Grep 搜所有 `[[...]]` 链接，交叉比对实际文件列表。逐项报告问题，让用户决定如何修复。

## 链接维护规则

1. **新增实体/概念时必须更新 `index.md`**
2. **修改页面后检查反链**——用 `grep(pattern="\[\[页面名\]\]", path="wiki/wiki")` 找出所有引用方，确认它们不因本次修改而出错
3. **删除页面前先清空所有指向它的链接**
4. **链接用相对路径**：`[[entities/foo]]` 而非 `[[wiki/wiki/entities/foo]]`

## Git 集成

Wiki 目录本身就是一个 git 仓库（或项目仓库的子目录）。每次摄入或批量修改后提交：

```bash
git add wiki/ && git commit -m "wiki: ingest <source-name>"
```

小提交比大提交好——每个摄入操作一个 commit，方便追溯和回滚。

## 与其他工具协作

- **web_fetch**：摄入 URL 时先用它抓取内容，存入 `wiki/raw/`，再执行 ingest 流程
- **web_search**：查询 wiki 无法回答时，用它补充外部信息后再回答
- **子代理**：大量文档摄入时，可 spawn 子代理并行处理不同文件
