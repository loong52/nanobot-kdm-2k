# 上下文分层与 Agent 状态栏实施方案

日期：2026-08-09

基线：`origin/main@359ebfa0`

范围：只覆盖长任务恢复、工具失败避免盲重试、多子 Agent 状态。本方案不涉及外部操作幂等、Cron 或通用工具执行配额。

## 1. 目标和已确认决策

本方案将稳定指令、动态事实和请求态状态分层，并由代码从现有耐久状态生成短状态投影。状态栏用于减少模型扫描长历史的成本，不是 UI 装饰、记忆系统或安全边界。

以下决策已经确认，实施不得重新解释：

- 失败计数属于同一条真实用户请求；内部 continuation 继承，只有新的真实用户消息重置。
- active Goal 时分为两个区域：当前 owner Run 的 required 子任务状态，以及当前 active Goal 的整体汇总。前者服务完成 barrier，后者服务跨 turn 恢复。
- Agent Status 是 transient model-request overlay，只存在于一次模型请求；不写入模型历史、session transcript、checkpoint 或 WebUI。
- 仅在 active Goal、当前 logical user request 有重复失败，或当前 owner Run 存在 required 子 Agent 时注入；普通问答和一次成功工具调用不注入。
- provider 无法合法承载 overlay 时，省略并记录审计/指标；不得伪造 role 或使 provider 配置失效。
- 首版不增加 WebUI 可见状态栏，既有 Goal 和轨迹展示不变。
- 每次状态生成必须留下 `status_revision`、`status_schema_version`、`source_event_ids`、`generated_at`、`scope`。本期审计不将状态内容视为敏感信息，但正文仍有固定长度上限。

## 2. 非目标

- 不用 LLM 批量总结历史再写回为状态真相；
- 不用状态栏替代原始 history、Goal、completion barrier 或安全策略；
- 不按工具名在整个 session 或 Goal 内做通用计数/硬拒绝；
- 不为了缓存把行为规则、安全规则或稳定 Skill 指令移出 system；
- 不新增与 `goal_state`、`goal_orchestration`、Audit 并列的 Goal/子 Agent 状态机；
- 不在缺少 provider token 证据时声称 KV Cache 命中、成本或延迟得到改善。

## 3. 当前实现事实

### 3.1 Context 和 Runtime Context

`ContextBuilder.build_system_prompt()` 当前把身份、bootstrap 文件、工具契约、Memory、always skills、skill
索引、Recent History 和 archived summary 合并为一个 system 消息。Recent History、Memory 和 summary 的变化会改变整段 system。

现有 `nanobot/runtime_context.py` 的语义是“持久化的当前 user prompt 附加内容”：它会拼接当前 user 内容，`AgentLoop._persist_user_message_early()` 会将它及 marker 写入 session，模型重放时默认仍可见。WebUI/SDK 只是展示时剥离副本。

因此，现有 `RuntimeContextProvider` 继续服务既有 Goal、CLI App 等能力，但**绝不能作为 Agent Status 的承载接口**。

### 3.2 边界术语

| 名称 | 定义 | 状态栏用途 |
| --- | --- | --- |
| 真实用户请求 | 外部用户的一条 user 入站消息 | 失败 ledger 的唯一重置边界 |
| logical user request | 真实用户请求及其内部 continuation | 失败 ledger 的持久化作用域 |
| Turn | AgentLoop 对一条入站消息的处理 | 审计关联，不重置失败 |
| Run | 一次 `AgentRunner.run()` | owner Run required 子任务作用域 |
| iteration | Runner 的一次循环槽位 | 展示执行位置，不等于工具次数 |
| model call | 一次 provider 请求 | overlay 的实际生命周期 |

`max_iterations` 是 iteration 预算。工具批次完成后 Runner 写入内存 tool result 并进入下一 iteration，因此同一 Run 中的实时状态必须在下一 model call 前重建。

### 3.3 已有权威来源

| 事实 | 权威来源 | 状态投影职责 |
| --- | --- | --- |
| active Goal、目标和 Goal 状态 | `goal_state.py` / session metadata | 跨 turn 恢复 |
| required task、replacement、终态、delivery phase | `GoalOrchestrationStore` | Goal obligation 和 barrier |
| 当前 owner Run 的 required task | `select_owner(session_key, run_id)` | 当前 Run completion guard |
| 当前进程任务句柄 | `SubagentManager` | 等待/取消，不是重启真相 |
| 工具终态和恢复关系 | Runner hook 与 Audit | 失败事件证据 |
| 中断模型/工具调用 | `runtime_checkpoint` | 历史修复 |

Agent Status 只读取这些状态。唯一新增的耐久业务数据是小型 logical-request failure ledger；它不拥有 Goal 或子 Agent 生命周期。

## 4. 目标架构

### 4.1 三层上下文

```text
稳定 system 前缀
  身份、行为规则、安全规则、工具契约、稳定 Skill 指令

持久化 history
  原始 user/assistant/tool 对话、既有 runtime context、压缩摘要

transient request overlay
  当前 model call 的 Agent Status，发送后立即丢弃
```

必须留在 system 的内容包括稳定身份、SOUL/AGENTS 中的行为和安全规则、工具契约、稳定 always Skill 操作说明。bootstrap 文件变化导致 system 缓存失效是正确行为。

可作为动态事实处理的内容包括 Recent History、仅含事实的 Memory、archived summary、Goal 进度、失败 ledger 和子任务状态。若动态内容含有指令，必须留在 system 或其原始 user/history 位置，不能因为缓存收益提升不可信输入的权限。

### 4.2 Transient overlay 协议

引入内部概念，具体类型名可以调整，但语义不得调整：

```text
AgentStatusFacts       从耐久事实读取的结构化快照
AgentStatusOverlay     限长渲染结果和审计元数据
ProviderOverlayResult  applied | omitted_unsupported | omitted_invalid
```

每次 model call 的顺序：

```text
canonical messages
  -> context governance 的 model view
  -> 读取最新 Goal、owner Run、failure ledger 形成 facts
  -> 生成本次 status revision 和 overlay
  -> provider 生成合法 request copy
  -> 发送模型请求
  -> 丢弃 request copy 与 overlay 正文
```

禁止修改 `AgentRunner.messages`、`initial_messages`、session messages、runtime checkpoint 和 `RuntimeContextBlock`。这样多次 tool iteration 不会累计多条状态消息。

状态必须在每次模型请求前生成，不能只在 user turn 开始时冻结。工具完成、子 Agent 回传和 pending queue 入站处理后，下一请求必须读取最新 revision。

### 4.3 Provider 适配和降级

在 `LLMProvider` 上建立显式、可测试的 transient overlay 能力。Runner 不得自行向所有 provider 追加通用 user 消息。

| Provider | 必须验证 | 不支持时行为 |
| --- | --- | --- |
| Anthropic | tool result 后消息顺序、system/tools cache marker、overlay 不跨稳定 cache prefix | 省略并审计 |
| OpenAI-compatible | 每个 provider spec 的连续 role、tool 后 user、文本和多模态规则 | 省略并审计 |
| OpenAI Codex | Responses `input` 中 function output 后的表示、`instructions` 和 tool schema 缓存键 | 省略并保留正常请求 |

任何适配器不得把 assistant/tool 内容改成 user 来容纳状态。现有 OpenAI-compatible role 修复不能作为 overlay 合法性的依据。

### 4.4 Failure ledger

在 session metadata 保存一个小型、版本化 ledger，例如：

```json
{
  "schema_version": 1,
  "logical_user_request_id": "opaque-id",
  "revision": 4,
  "failures": {
    "operation-fingerprint": {
      "tool_name": "read_file",
      "consecutive_failures": 2,
      "last_error_class": "file_not_found",
      "last_outcome": "error",
      "source_event_ids": ["..."],
      "updated_at": "..."
    }
  }
}
```

规则：

- fingerprint 由工具名和规范化后的完整参数生成；不得用工具名、路径前缀或 Goal ID 代替；
- 新真实 user 入站创建新的 logical request 并原子清除旧 ledger；system、subagent result 和 internal continuation 不得重置；
- continuation metadata 显式携带 logical request ID；
- 同 fingerprint 成功时清除连续失败；参数不同的操作互不影响；
- cancelled、崩溃中断、结果未知标为 `interrupted/unknown`，不得当作可安全重放的普通失败；
- 无法安全生成 fingerprint 的工具不进入“相同操作重复”统计。

优先复用已有 `tool_operation_evidence()` 的 retry key、错误分类和 Audit 关联，避免另写参数比较逻辑。

### 4.5 Goal 与子 Agent 双分区

状态栏只渲染机器派生字段，不持久化状态文本。推荐的模型输入格式：

```text
[Agent Status]
Owner Run required: succeeded=1 running=1 failed=0; completion=blocked
Active Goal required: succeeded=3 running=1 failed=1 lost=0; delivery_pending=1
Repeated failure: read_file same-operation failures=2; class=file_not_found
[/Agent Status]
```

约束：

- owner Run 只读取 `select_owner()` 的 required obligation，并使用当前 completion guard 的同一 `run_id`；
- active Goal 只统计 root obligation。replacement 链不重复计数，必须反映真实 `delivery_phase`；
- 不将 `SubagentManager` 内存缓存与 Goal 全部任务混成一个总数；
- 状态栏不能解除或替代 completion guard；required 子任务未满足时仍由执行层阻止完成；
- 首版不渲染 Goal objective、绝对工作区路径、外部 ID、原始参数、完整工具输出或异常堆栈。

### 4.6 审计、revision 和并发顺序

每个 overlay 生成以下元数据：

```text
status_revision
status_schema_version
source_event_ids
generated_at
scope = logical_request | owner_run | active_goal
overlay_result = applied | omitted_unsupported | omitted_invalid
```

本期允许既有 model-request Audit 保存完整请求；新增状态审计记录仍不得把状态正文、原始参数或用户正文作为必要字段。

工具批次、子 Agent 回传和入站消息的顺序必须是：

1. 工具 terminal outcome 产生后，更新并保存 failure ledger/revision；
2. 关联来源工具/Audit 事件 ID；
3. 写入既有 runtime checkpoint；
4. drain 当前 Runner 的 pending injection；
5. 下一 model call 读取最新耐久事实并生成 overlay。

并发工具可以对应多个来源事件，但一次 model call 只能使用一个已提交 revision。读取失败、Goal 不完整或 provider 不支持时省略该区域，不中断 Run。

## 5. Prompt Cache 策略

### 5.1 System 分段

将 `ContextBuilder` 重构为显式 system sections，至少能够计算 stable system digest、dynamic fact digest、tool schema digest、token 数和缓存失效原因。

首批保留全部原始上下文。Recent History 可在验证后迁移到动态事实输入；Memory 和 archived summary 必须先区分事实与指令。状态投影不是删除原始上下文的许可。

### 5.2 Provider 缓存规则

- Anthropic：仅稳定 system section 作为长期 cache prefix；overlay 永远位于动态末尾；验证工具 schema marker。
- OpenAI-compatible：仅明确声明支持的 spec 接收缓存控制字段；本地 hash 稳定不等于远端命中。
- OpenAI Codex：`prompt_cache_key` 由稳定 system digest、provider/model、tool schema digest 和格式版本组合；不能纳入每次变化的 user/overlay，也不能忽略 schema 变化。

每个 model call 记录 stable/dynamic/tool digest、overlay token、输入 token、cached token（若 provider 提供）和 overlay 省略原因。provider 无 cached token 时必须标记 `unknown`。

## 6. 分 PR 实施

### PR1：transient 失败状态垂直切片

目标：证明 overlay 不进入历史，且同一 logical request 的重复失败会在下一 model call 前可见。

- 增加 logical user request ID、failure ledger、revision 和纯渲染函数；
- 工具 terminal hook 更新 ledger；continuation 继承 ID；新真实 user 重置；
- 首批只实现经 wire test 验证的 OpenAI Codex overlay；其他 provider 明确省略；
- 不改 system 分层，不改 Goal/子 Agent 投影，不新增失败硬拒绝；
- 既有安全拒绝与 completion guard 继续强制。

建议改动：`agent/runner.py`、`agent/loop.py`、`agent/hook.py`、`session/turn_continuation.py`、新增状态模块、`providers/base.py`、`providers/openai_codex_provider.py`、`audit/hook.py`、`audit/schema.py` 和聚焦测试。

### PR2：Goal 与 required 子 Agent 双分区

目标：在 PR1 overlay 协议上只读 `goal_state` 与 `goal_orchestration`，实现长任务恢复和 owner Run/Goal 汇总。

- 投影 owner Run、Goal root obligation、replacement、delivery phase 和 lost；
- 复用现有 completion guard，不复制 barrier；
- 覆盖子 Agent 回传、owner Run 切换、进程重启和 late delivery。

### PR3：system 分层与完整 provider 缓存矩阵

目标：将缓存收益与状态栏功能分开验证。

- 拆分 stable/dynamic system sections，评估 Recent History、Memory、summary；
- 增加 Anthropic、OpenAI-compatible、Codex wire-level 测试；
- 完成 Codex tool schema digest 缓存键；
- 用 provider usage 和真实 Gateway 场景证明效果，而非只比较最终回答。

## 7. 测试和真实验收

必须新增或扩展以下断言：

- 同工具不同参数/目标不合并；同参数跨 continuation 继承；新真实 user 重置；
- 每次 model request 最多一个 overlay；canonical messages、session、history、checkpoint 均无 overlay；
- provider 不支持时正常省略；覆盖连续 user、tool 后 user、多模态 user；
- 工具取消、异常、进程重启均有确定 ledger 终态，不发生盲重试；
- owner Run 与 active Goal 分区，replacement 不重复计数，delivery 状态正确；
- 状态栏与 `required_gate`/completion guard 不一致时，guard 仍阻止完成；
- 旧 session 的 runtime-context marker 保持既有重放和展示剥离语义；
- 状态超限、字段缺失、revision 冲突和 provider 省略都有确定降级；
- Anthropic、OpenAI-compatible、Codex 的请求体、marker 和缓存键符合边界。

起始测试命令：

```bash
pytest -q \
  tests/agent/test_context_prompt_cache.py \
  tests/agent/test_runtime_context.py \
  tests/agent/test_loop_runner_integration.py \
  tests/agent/test_session_manager_history.py \
  tests/providers/test_openai_codex_provider.py \
  tests/session/test_turn_continuation.py
```

实现新增文件后，对受影响 Python 路径运行 `ruff check`。改动 `loop.py` 或 `runner.py` 必须增加聚焦集成测试。

真实 Gateway 验收提示词：

```text
/goal [CTXSTATUS-20260809-A] 先用 read_file 读取不存在的
__ctx_status_missing__.txt。失败后不得以相同参数重试；改用 list_dir
确认可用文件。不得写入或修改任何文件。完成后只报告实际结果。
```

期望证据：同一 trace 出现真实 `read_file` 失败、状态 revision 更新、下一模型请求和 `list_dir`；失败后没有同参数 `read_file`；审计能关联 revision 和来源事件；session、WebUI transcript、标题和用户回答均没有 Agent Status 正文。交付时记录构建标识、会话 URL、trace URL、实际结果和未覆盖风险。

## 8. 批准门槛

编码前，实施者必须把本方案与当前源码、测试和相关提交逐项核对。发现 provider wire 约束或既有权威状态与方案冲突时，先记录证据并更新方案或 PR 说明，不得静默新建平行状态机。

本方案经用户确认后按 PR1、PR2、PR3 执行。每个 PR 独立提交、推送并维护同一个中文 PR；未经用户明确确认不得合并 `main`。
