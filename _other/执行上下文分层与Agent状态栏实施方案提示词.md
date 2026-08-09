# 执行“上下文分层与 Agent 状态栏”实施方案提示词

你正在 nanobot 仓库中实施上下文分层和 Agent Status。目标是提升长任务恢复、避免同一 logical user request 中的盲重试，并准确展示 required 子 Agent 状态；不是增加 WebUI 状态栏，也不是重写 AgentRunner、Goal 或 Audit。

仓库根目录：

```text
/home/kdm/TL-WorkSpace/TL-Project/AIworker/nanobot-kdm-2k
```

## 一、先读方案和当前事实

完整阅读当前目录适用的全部 `AGENTS.md`、`.agent/design.md`、`.agent/security.md`、`.agent/gotchas.md`，再阅读：

```text
_other/上下文分层与Agent状态栏实施方案.md
```

然后至少核对：

```text
nanobot/agent/context.py
nanobot/runtime_context.py
nanobot/agent/loop.py
nanobot/agent/runner.py
nanobot/agent/context_governance.py
nanobot/session/goal_state.py
nanobot/session/goal_orchestration.py
nanobot/session/turn_continuation.py
nanobot/session/manager.py
nanobot/agent/subagent.py
nanobot/agent/hook.py
nanobot/agent/tools/registry.py
nanobot/providers/anthropic_provider.py
nanobot/providers/openai_compat_provider.py
nanobot/providers/openai_codex_provider.py
tests/agent/test_context_prompt_cache.py
tests/agent/test_runtime_context.py
tests/agent/test_loop_runner_integration.py
tests/agent/test_session_manager_history.py
tests/providers/test_openai_codex_provider.py
tests/session/test_turn_continuation.py
```

方案定义目标架构，源码和测试定义当前事实。发生冲突时记录文件、行号、测试和提交证据；优先保护已验证语义；在 PR 中说明取舍。不得静默创造第二套 Goal、子 Agent 或 Audit 状态机。

## 二、开始前检查和 Git 纪律

执行并记录：

```bash
git status --short --branch
git branch --show-current
git log -15 --oneline --decorate
git remote -v
```

遵守：

- 从 `origin/main` 创建独立 ASCII 任务分支；不在默认分支开发；
- 不 fetch、merge、rebase `upstream`；不 stash、reset、clean、覆盖用户修改；
- 每个可验证工作单元使用中文提交并立即推送，持续维护同一个中文 PR；
- 未经用户明确确认不得合并 `main`；不运行 `ruff format`；
- 不改动 `runtime/workspace/`；
- Python 改动运行最接近 pytest 和受影响路径 `ruff check`；修改 `loop.py` 或 `runner.py` 必须补充聚焦集成测试。

## 三、不可违反的架构语义

1. Agent Status 是 transient model-request overlay。它只存在于一次 provider 请求，绝不写入 canonical messages、session transcript、history、runtime checkpoint 或 WebUI。
2. 现有 `RuntimeContextProvider` 是持久化 user 附加上下文，绝不得复用为 Agent Status。
3. failure ledger 按真实用户请求的 logical request 作用域。internal continuation 继承；只有新真实 user 入站重置。system 消息、子 Agent 回传和重启修复不得重置。
4. 重复操作必须使用工具名和规范化完整参数的精确 fingerprint。相同工具名但不同路径、URL 或参数不能相互计数。首版不增加通用硬拒绝。
5. active Goal 时必须分区：当前 owner Run 只用当前 `run_id` 的 required 任务，服务 completion barrier；active Goal 用 Goal root obligation 汇总，服务跨 turn 恢复。
6. 复用 `goal_state`、`GoalOrchestrationStore`、existing completion guard、subagent lifecycle、runtime checkpoint 和 Audit；禁止复制这些状态机。
7. 状态栏只是决策辅助。安全拒绝和 required completion 阻止必须继续由工具执行层、policy 或 completion guard 强制执行。
8. 仅在 active Goal、重复失败或当前 owner Run 存在 required 子 Agent 时注入。普通问答和一次成功工具调用不得注入。
9. provider 无法合法承载 overlay 时，省略并记录 `omitted_unsupported` 或 `omitted_invalid`；不得伪造角色、转换 assistant/tool 历史或让 provider 故障。
10. 首版不增加 WebUI 可见状态栏，不改既有 Goal/轨迹协议。

## 四、System、history 和缓存

稳定 system 必须保留身份、行为规则、安全规则、工具契约和稳定 Skill 指令。bootstrap 文件内容变化应使 system 缓存失效，而不是被移动到 user 文本。

动态事实包括 Recent History、仅含事实的 Memory、archived summary、Goal 进度、failure ledger 和子任务状态。不要为了缓存删除原始历史；先证明状态投影覆盖所需维度。

缓存验证必须以 wire-level provider 请求为准：

- Anthropic：验证 system/tools cache marker 和 overlay 位置；
- OpenAI-compatible：按具体 provider spec 验证 role、多模态和 tool message 合法性；
- OpenAI Codex：缓存键覆盖稳定 system 版本、provider/model、工具 schema digest 和格式版本，不含每次变化的 user/overlay；
- `ToolRegistry` 的稳定排序不等于 schema 版本；schema 改变必须导致 Codex key 改变；
- 无 cached token 时只报告 `unknown`，不得把 hash 稳定当作远端命中。

## 五、状态更新和审计顺序

每次 overlay 生成最小审计元数据：

```text
status_revision
status_schema_version
source_event_ids
generated_at
scope
overlay_result
```

状态正文有限长；首版不渲染绝对工作区路径、外部 ID、原始工具参数、完整工具输出或堆栈。当前任务不把审计保存状态正文视为敏感信息问题，但状态正文不能成为唯一业务真相。

顺序必须是：

```text
工具 terminal outcome
  -> 持久化 failure ledger/revision
  -> 关联来源事件
  -> 既有 checkpoint
  -> drain pending user/subagent injection
  -> 下一 model call 重读 facts 并生成 request overlay
```

并发工具可对应多个来源事件，但一次 model call 只能使用一个已提交 revision。取消、崩溃或结果未知必须是 `interrupted/unknown`，不得自动重放。

## 六、实施顺序

严格拆为可审查 PR：

### PR1：失败状态垂直切片

- 增加 logical request ID、版本化 failure ledger 和纯渲染函数；
- 在工具 terminal 边界更新 ledger；continuation 继承；新真实 user 重置；
- 每个 model call 生成并丢弃 request overlay；
- 首批只支持通过 wire test 的 OpenAI Codex；其他 provider 明确省略；
- 不改 system 分层，不改 Goal/子 Agent 投影，不新增失败硬拒绝。

### PR2：Goal 和 required 子 Agent 双分区

- 从 `goal_state` 和 `goal_orchestration` 只读生成 active Goal/owner Run 两个区域；
- 处理 replacement、delivery phase、子 Agent 回传、owner Run 边界和重启后的 `lost`；
- completion guard 仍是唯一强制完成门槛。

### PR3：上下文分层和 provider 缓存矩阵

- 分离 system stable/dynamic sections，评估 Recent History、Memory 和 summary；
- 完成 Anthropic、OpenAI-compatible、Codex 的 wire-level 测试；
- 完成 Codex 工具 schema digest 缓存键和实际指标验证。

不要跨 PR 混入无关重构、外部操作幂等、Cron 改造或 WebUI 产品改造。

## 七、必须覆盖的测试

至少验证：

- 同工具不同参数不会合并；同参数跨 continuation 继承；新 user 重置；
- canonical history、session、checkpoint、WebUI transcript 不含 overlay；多次 iteration 不累积 overlay；
- Anthropic/OpenAI-compatible/Codex 对连续 role、tool 后状态、多模态内容的真实 wire 请求；不支持时省略；
- 工具取消、进程崩溃、Gateway 重启、旧 runtime context marker 均有兼容处理；
- 状态生成和模型请求之间出现新 user 消息或子 Agent 回传时，下一请求使用新 revision；
- owner Run 与 Goal 汇总不混数；replacement 不重复；delivery/barrier/Audit 不一致时以执行层为准；
- 状态错误、陈旧、重复、超长、来源缺失时有确定降级；
- 静态 system/tool schema 变更和稳定重复请求的缓存键/marker 边界。

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

## 八、真实 Gateway 验收

完成涉及 Agent 流程、审计或 provider 的 PR 后，按仓库规则重建固定长期 Gateway，核对构建标识、容器、挂载和实际 workspace。不得另起端口或修改 `runtime/workspace/`。

在新的 WebUI 会话执行：

```text
/goal [CTXSTATUS-20260809-A] 先用 read_file 读取不存在的
__ctx_status_missing__.txt。失败后不得以相同参数重试；改用 list_dir
确认可用文件。不得写入或修改任何文件。完成后只报告实际结果。
```

验收必须说明构建标识、会话和 trace URL、真实失败、status revision/来源事件、纠正后的 `list_dir`、无同参数重试、无用户可见状态正文，以及未覆盖风险。

开始编码前，先输出能力矩阵和 PR1 文件边界；完成实现后再提交、推送、创建或更新 PR。未经用户明确确认，不得合并 `main`。
