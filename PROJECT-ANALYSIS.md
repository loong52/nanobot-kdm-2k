
# nanobot 项目全面解析

## 项目概述

**nanobot**（包名 `nanobot-ai`，v0.2.2）是一个轻量级开源 AI Agent 框架，使用 Python（后端）+ React/TypeScript（前端）构建。本项目是上游 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) 的一个 fork（kdm-2k 变体）。核心架构围绕一个小型 Agent 循环展开，从多个聊天平台接收消息，调用大语言模型（LLM）执行工具，并管理会话记忆。

---

## 1. 顶层目录结构

```
nanobot-kdm-2k/
├── .agent/               # AI 编码代理项目指南（design.md, security.md, gotchas.md）
├── .github/              # GitHub CI 工作流 + Issue 模板
├── _other/               # 杂项参考材料
├── case/                 # README 演示图片（GIF）
├── docs/                 # 综合项目文档（50+ 个 Markdown 文件）
├── images/               # README 封面图片（亮色/暗色）和架构图
├── nanobot/              # 主 Python 包源码
├── runtime/              # 本地运行时数据（配置、审计追踪、WebUI 会话日志等）
├── scripts/              # 安装脚本（Shell + PowerShell）和频道依赖安装器
├── tests/                # Python 测试套件（镜像 nanobot/ 包结构）
├── webui/                # React/TypeScript WebUI 源码（Vite + React 18 + Tailwind + shadcn/ui）
├── AGENTS.md             # 主要 AI 编码代理指南（中文，约 150 行）
├── CLAUDE.md             # 重定向至 AGENTS.md（仅一行："@AGENTS.md"）
├── COMMUNICATION.md      # 联系信息（飞书、微信、Discord）
├── CONTRIBUTING.md       # 贡献流程与 PR 指南
├── Dockerfile            # 多阶段 Docker 镜像（node webui-builder + uv Python 运行时）
├── KDM-QUICKSTART.md     # 当前 fork 的快速入门指南
├── LICENSE               # MIT 许可证
├── README.md             # 项目主 README（~397 行）
├── SECURITY.md           # 安全策略
├── THIRD_PARTY_NOTICES.md # 第三方许可证声明
├── conftest.py           # 跨套件的 pytest 夹具（Windows CA 证书包优化）
├── docker-compose.yml    # Docker Compose（gateway + API + CLI 服务）
├── docker-compose.bwrap.yml # Docker Compose（含 bubblewrap 沙箱）
├── entrypoint.sh         # Docker 入口点（Render-ready，用户权限降级）
├── hatch_build.py        # Hatchling 构建钩子（WebUI 构建集成）
├── pyproject.toml        # Python 项目配置（~185 行）
├── render.yaml           # Render Blueprint 部署定义
└── render-config.json    # Render 部署网关配置模板
```

---

## 2. Python 包结构（`nanobot/`）

### 2.1 核心 Agent 子系统

| 子包/文件 | 用途 |
|-----------|------|
| `nanobot/agent/loop.py` | **AgentLoop** — 中心处理引擎，7 状态状态机：`RESTORE → COMPACT → COMMAND → BUILD → RUN → SAVE → RESPOND → DONE` |
| `nanobot/agent/runner.py` | **AgentRunner** — LLM 对话循环执行引擎，处理流式/非流式调用、工具执行、错误恢复 |
| `nanobot/agent/memory.py` | **MemoryStore** — 持久化记忆（MEMORY.md/SOUL.md/USER.md/history.jsonl）+ Dream 两阶段记忆固化 |
| `nanobot/agent/context.py` | **ContextBuilder** — 组装完整提示词上下文（系统提示词 + 记忆 + 技能 + 历史） |
| `nanobot/agent/hook.py` | **AgentHook** — Agent 生命周期钩子接口 |
| `nanobot/agent/subagent.py` | **SubagentManager** — 子代理的生成和管理 |
| `nanobot/agent/skills.py` | 技能加载（从 `nanobot/skills/` 和文件系统） |
| `nanobot/agent/model_presets.py` | 模型预设解析 |
| `nanobot/agent/model_runtime.py` | LLM 运行时解析（provider + model + context_window） |
| `nanobot/agent/autocompact.py` | **AutoCompact** — 空闲会话主动压缩 |
| `nanobot/agent/context_governance.py` | **ContextGovernor** — 发送模型前的消息修复/压缩 |

**AgentLoop 关键设计特点：**
- **每会话串行化：** `_dispatch()` 获取每个 session key 的 `asyncio.Lock`，确保每会话一次只能有一个 turn
- **并发门控：** 信号量（`NANOBOT_MAX_CONCURRENT_REQUESTS`，默认 3）限制总并发 LLM 调用
- **Turn 中途注入：** 对活跃会话到达的消息路由到每个会话的 `asyncio.Queue`（最大 20 条），而非创建竞争任务
- **检查点/恢复：** 在每次工具执行步骤后将部分 turn 状态持久化到会话元数据。任务取消（如 `/stop`）时，检查点物化到会话历史中
- **自动化 Turn 协调：** CronTurnCoordinator 和 LocalTriggerTurnCoordinator 管理延迟的自动化 turn

### 2.2 LLM 提供者（`nanobot/providers/`）

| 文件 | 用途 |
|------|------|
| `base.py` | 抽象 `LLMProvider` 基类、`LLMResponse`、`ToolCallRequest`、`GenerationSettings` |
| `registry.py` | `PROVIDERS` 元组 — **40+ 提供者**的唯一定义源 |
| `factory.py` | `make_provider()`、`provider_signature()` — 从配置构建提供者 |
| `openai_compat_provider.py` | `OpenAICompatProvider` — 通用 OpenAI 兼容后端（大多数提供者使用） |
| `anthropic_provider.py` | `AnthropicProvider` — 原生 Anthropic SDK，支持提示词缓存 |
| `azure_openai_provider.py` | `AzureOpenAIProvider` |
| `bedrock_provider.py` | `BedrockProvider` — AWS Bedrock Converse API |
| `openai_codex_provider.py` | `OpenAICodexProvider` — OAuth 认证 |
| `github_copilot_provider.py` | `GitHubCopilotProvider` — OAuth 认证 |
| `fallback_provider.py` | `FallbackProvider` — 主提供者 + 回退链 |
| `image_generation.py` | 通过 LLM 提供者的图像生成 |
| `observed_call.py` | 提供者遥测/可观察性 |
| `transcription.py` | 音频转录提供者 |

**核心抽象：** `LLMProvider` 基类定义：
- `chat()` / `chat_stream()` — 核心 LLM 调用
- `chat_with_retry()` / `chat_stream_with_retry()` — 带指数退避的重试机制（1s、2s、4s）+ 持久模式（上限 60s）+ 图片剥离重试
- 角色交替强制 + 错误分类（瞬态 vs 非瞬态）

### 2.3 频道（`nanobot/channels/`）

**17 个聊天平台集成：** dingtalk、discord、email、feishu、matrix、mattermost、mochat、msteams、napcat、qq、signal、slack、telegram、websocket、wecom、weixin、whatsapp

每个频道是 `nanobot/channels/<name>/` 下的自包含包：
- `manifest.py` — 导出 `ChannelPlugin(...)` 描述符
- `runtime.py` — `BaseChannel` 子类
- 可选的 `webui/` — 设置 UI 组件
- `tests/` — 频道专用测试

**ChannelManager** — 通过 `pkgutil` 发现插件，从配置构建实例，并管理：
- 入站消息发布到 MessageBus
- 出站消息从总线消费并路由到正确的频道
- 连续的流式增量合并 + 去重
- 运行时启用/禁用频道

### 2.4 工具（`nanobot/agent/tools/`）

**20+ 内置工具：**

| 文件 | 工具 | 功能 |
|------|------|------|
| `filesystem.py` | ReadFileTool, WriteFileTool, EditFileTool | 工作区范围内的文件 I/O |
| `shell.py` | ExecTool | 沙箱化的 shell 命令执行 |
| `web.py` | WebFetchTool, WebSearchTool | 带 SSRF 保护的网页请求 |
| `message.py` | MessageTool | 向频道发送消息 |
| `spawn.py` | SpawnTool | 启动子代理 |
| `await_subagents.py` | AwaitSubagentsTool | 等待子代理完成 |
| `mcp.py` | MCP 工具集成 | 连接外部 MCP 服务器 |
| `cron.py` | CronTool | Cron 任务管理 |
| `long_task.py` | LongTaskTool | 长期运行任务管理 |
| `image_generation.py` | ImageGenerationTool | 图像生成 |
| `search.py` | SearchTool | 文件内容搜索 |
| `apply_patch.py` | ApplyPatchTool | 应用文件补丁 |
| `self.py` | MyTool | 运行时状态自省 |
| `cli_apps.py` | CLITool | 运行 CLI 应用包 |

**ToolLoader** 通过 `pkgutil.iter_modules` 发现工具子类 + `nanobot.tools` 入口点插件。

**ToolRegistry** 管理工具注册、查找、执行和 LLM 函数定义生成。

### 2.5 消息总线（`nanobot/bus/`）

解耦频道和 Agent 核心的异步消息系统：

- **`queue.py`** — `MessageBus`：通过一对 `asyncio.Queue` 进行解耦通信
- **`events.py`** — 核心事件类型：`InboundMessage`、`OutboundMessage`
- **`outbound_events.py`** — 带类型的出站事件：`ProgressEvent`、`StreamDeltaEvent`、`StreamedResponseEvent`
- **`runtime_events.py`** — `RuntimeEventBus`：在进程内的发布/订阅，用于 Agent 状态通知

### 2.6 其他关键子系统

| 子系统 | 位置 | 用途 |
|--------|------|------|
| **会话管理** | `nanobot/session/` | JSONL 文件支持的会话持久化（工作区/会话/），LRU 缓存（128 个强引用），原子写入 + fsync |
| **配置** | `nanobot/config/` | 基于 Pydantic 的配置加载/保存，`${VAR}` 环境变量插值，配置迁移 |
| **审计** | `nanobot/audit/` | 持久的 Agent 审计追踪（v1 证据层），25+ 文件 |
| **Cron 服务** | `nanobot/cron/` | 基于 Cron 的定时 Turn 调度 |
| **命令路由** | `nanobot/command/` | `/` 斜杠命令路由 + 内置命令处理器 |
| **配对** | `nanobot/pairing/` | 基于代码的 DM 发送者审批系统 |
| **安全** | `nanobot/security/` | SSRF 保护（LAN IP 拦截）+ 工作区边界强制执行 |
| **技能** | `nanobot/skills/` | 11 个内置技能（cron、memory、github、image-generation 等） |
| **API 服务器** | `nanobot/api/` | 兼容 OpenAI 的 HTTP API（`/v1/chat/completions`、`/v1/models`） |
| **网关** | `nanobot/gateway/` | 为 WebUI + API + WebSocket 提供服务的网关进程 |
| **WebUI 服务** | `nanobot/webui/` | 网关端 WebUI 支持服务（25+ 文件） |
| **SDK** | `nanobot/sdk/` | 用于外部集成的 Python SDK |
| **触发器** | `nanobot/triggers/` | 用于目标驱动 Turn 的本地触发器系统 |
| **工具类** | `nanobot/utils/` | 共享工具类（~20 文件） |

---

## 3. WebUI（`webui/`）

### 3.1 技术栈

- **构建工具：** Vite 5 + TypeScript 5.7
- **UI 框架：** React 18 + Tailwind CSS 3
- **UI 组件：** shadcn/ui 基础组件（Radix UI）+ 自定义组件
- **Markdown：** react-markdown + react-syntax-highlighter + KaTeX（数学公式）+ remark-gfm
- **图形：** @xyflow/react（审计追踪节点图）+ elkjs（图形布局）
- **国际化：** i18next + react-i18next（10 种语言环境）
- **测试：** Vitest 2 + happy-dom + Testing Library

### 3.2 源码结构

```
webui/src/
├── main.tsx                          # React 入口点
├── App.tsx                           # 根组件（~2200 行）
├── globals.css                       # 全局样式
├── components/
│   ├── ui/                           # shadcn/ui 基础组件
│   ├── settings/                     # 设置页面（设置视图、频道设置、技能目录、令牌热力图）
│   └── thread/                       # 主聊天线程（编写器、消息、提示词导航等）
├── hooks/                            # 自定义 React Hooks（~15 个）
├── lib/                              # 核心库（~25 个文件）
├── i18n/                             # 国际化（10 种语言环境）
├── channel-plugins/                  # 频道 UI 插件系统
├── providers/                        # React 上下文提供者
├── workers/                          # Web Workers（审计布局、图片编码）
└── tests/                            # ~40+ 个 Vitest 测试文件
```

### 3.3 关键架构设计

- **无外部状态库：** 所有状态通过 React 内置的 `useState`/`useReducer`/`useRef` 管理
- **WebSocket 通信：** `NanobotClient` 类封装全部 WebSocket 通信，支持自动令牌刷新
- **引导流程：** 获取 `/webui/bootstrap` → 接收短期令牌 → 派生 WebSocket URL → 连接
- **哈希路由：** `#/new`、`#/chat/<key>`、`#/settings`、`#/apps`、`#/automations`、`#/skills`、`#/traces/<id>`
- **构建输出：** `../nanobot/web/dist/`（通过 `pyproject.toml` 随 Python 包一起发布）

---

## 4. CLI（`nanobot/cli/`）

| 命令 | 描述 |
|------|------|
| `nanobot onboard` | 交云式配置 + 工作区初始化（支持 `--wizard` 模式） |
| `nanobot agent` | 直接交互式或单消息 Agent 交互（基于 prompt_toolkit） |
| `nanobot serve` | 兼容 OpenAI 的 API 服务器 |
| `nanobot webui` | 准备 WebUI、启动网关、打开浏览器 |
| `nanobot trigger` | 发送本地触发器消息 |
| `nanobot gateway` | 网关子命令组（启动/状态/日志/停止/重启/安装服务） |
| `nanobot audit` | 审计子命令组 |

---

## 5. 测试

### 5.1 结构

`tests/` 镜像 `nanobot/` 包结构。每个频道在 `nanobot/channels/<name>/tests/` 下也有自己的测试。

### 5.2 配置

- **框架：** pytest，搭配 `asyncio_mode = "auto"`
- **测试发现：** `tests/` + `nanobot/channels/`
- **默认排除标记：** `not slow`
- **覆盖率目标：** 75%

---

## 6. 核心数据流

```
聊天频道（Telegram、Discord、Slack 等 17 个）
        │
        ▼
   MessageBus（通过 asyncio.Queue 发送 InboundMessage）
        │
        ▼
   AgentLoop（状态机：RESTORE → COMPACT → COMMAND → BUILD → RUN → SAVE → RESPOND → DONE）
        │
        ├── ContextBuilder（系统提示词 + 记忆 + 技能 + 历史）
        ├── AgentRunner（LLM 对话循环 + 工具执行）
        ├── MemoryStore（MEMORY.md、SOUL.md、USER.md、history.jsonl + Dream 固化）
        ├── SessionManager（JSONL 会话持久化 + LRU 缓存）
        ├── ToolRegistry（20+ 个通过 pkgutil + 入口点发现的内置工具）
        ├── CommandRouter（/ 斜杠命令调度）
        └── SubagentManager（异步子代理管理）
        │
        ├── RuntimeEventBus（Turn 生命周期事件）
        ├── CronService（计划的 Turn 心跳 + Dream 固化）
        ├── AuditRuntime（审计追踪记录）
        │
        ├──► WebUI 通过 WebSocket 频道（全双工 JSON 帧）
        ├──► OpenAI API 服务器（HTTP /v1/chat/completions）
        └──► CLI（直接的 process_direct 调用或基于总线的交互模式）
```

## 7. 部署

- **Docker：** 使用 Docker Compose 的多服务部署（gateway :8765 + API :8900 + CLI），`cap_drop: ALL`
- **Render：** 通过 `render.yaml` 一键部署的 Render Blueprint + 持久化磁盘
- **服务安装：** systemd 用户服务 / macOS LaunchAgent 安装
- **沙箱：** 可选的 bubblewrap 沙箱支持

## 8. 代码风格

- Python 3.11+，全程使用 asyncio
- 行长度：100
- 代码检查：`ruff`，规则 E、F、I、N、W（忽略 E501）
- pytest，搭配 `asyncio_mode = "auto"`
- 不运行 `ruff format` — 项目明确避免大范围格式化造成的历史噪声
