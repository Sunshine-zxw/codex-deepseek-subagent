# 兼容性与安全边界

## 支持范围

- macOS
- Windows
- Python 3.11+
- ChatGPT/Codex 桌面应用至少启动过一次
- DeepSeek 官方 API，或 OpenAI Responses wire API 兼容的第三方 API / 中转站
- 默认模型 `deepseek-v4-flash`，也允许中转站使用自定义模型 ID
- reasoning effort：`low` / `high` / `max`
- multi-agent：`auto` / `v1` / `v2`

只有 `/chat/completions` 兼容并不足以保证能作为 Codex 原生子 Agent。本项目最终以 Codex 内置运行时的直连测试和真实 `spawn_agent` 验收为准。

## 配置位置

默认 `CODEX_HOME` 为 `~/.codex`：

- Codex 配置：`$CODEX_HOME/config.toml`
- 合并模型目录：`$CODEX_HOME/models-with-deepseek.json`
- 自定义角色：`$CODEX_HOME/agents/DeepSeek.toml`
- Provider profile：`$CODEX_HOME/codex-deepseek-subagent/provider-profile.json`
- 管理状态与备份：`$CODEX_HOME/codex-deepseek-subagent/`
- 系统凭据：
  - 官方 DeepSeek：`codex-deepseek-api-key`
  - 自定义 Provider：根据 `provider + base_url` 生成独立凭据目标
  - macOS：Keychain
  - Windows：Credential Manager

程序不修改顶层 `model` 或顶层 `model_provider`，主任务仍使用用户原来的模型和 ChatGPT/Codex 登录方式。

## 两层管理器

本 fork 保留上游：

```text
scripts/codex_deepseek.py
```

作为事务、备份、模型目录、系统凭据、桌面运行时与 SQLite 路由验收的稳定底座。

推荐入口是：

```text
scripts/codex_provider_manager.py
```

它会在运行前加载当前 Provider profile，并把模型、Provider、base URL、reasoning effort 和 multi-agent 策略注入底座。这样可以尽量少改上游核心逻辑，降低以后同步上游时的冲突。

## Provider profile

profile 持久化以下字段：

```json
{
  "mode": "custom",
  "base_url": "https://example.com/v1/",
  "model": "deepseek-v4-flash",
  "provider": "deepseek_proxy",
  "provider_name": "DeepSeek Proxy",
  "role": "DeepSeek",
  "reasoning_effort": "high",
  "multi_agent_version": "auto",
  "backend": "external"
}
```

`status` / `test` / `repair` 必须使用该 profile。自定义 Provider 配置完成后，不应因为再次运行管理命令而被恢复成官方 URL。

## 自定义 Provider 的模型目录

Codex 需要模型目录条目来识别外部模型。对于 DeepSeek V4 Flash 的中转站，本 fork复用上游从 DeepSeek 官方 Codex setup 取得的 V4 Flash 模型能力模板，并把 `slug` 替换为 profile 中的实际模型 ID。

这意味着本 fork 的“自定义模型 ID”主要针对：

- DeepSeek V4 Flash 官方模型；
- DeepSeek V4 Flash 的代理 / 中转站别名；
- 与 DeepSeek V4 Flash 能力契约一致的后端。

它不是一个任意模型目录生成器。若第三方模型与 DeepSeek V4 Flash 的上下文、tool call 或 reasoning 行为不同，不能仅靠改模型 ID 保证兼容。

## Responses 协议边界

Provider 写入：

```toml
wire_api = "responses"
```

因此中转站至少要正确处理 Codex 实际发送的 Responses 请求、流式事件和工具调用。一个服务即使宣传“OpenAI compatible”，如果只实现 `/chat/completions`，仍可能无法通过 `test`。

验收优先相信真实运行结果，不相信服务商的兼容性标签。

## Multi-Agent v1 / v2

### `auto` 策略

当前：

```text
backend=external -> v1
backend=openai   -> v2
```

`backend` 表示中转站背后的最终协议处理方，而不是 URL 域名。

### 为什么 external 默认 v1

当前 Codex multi-agent v2 的跨 Provider 派发可能携带 OpenAI 专有的加密 `agent_message`。DeepSeek、Kimi、GLM、Ollama 或普通 Responses 中转后端可以兼容公开 API 结构，但无法据此推断它们能处理 OpenAI 内部加密 payload。

因此：

```text
OpenAI 父 Agent -> DeepSeek 官方       -> v1
OpenAI 父 Agent -> 中转站 -> DeepSeek  -> v1
```

如果某中转站只是透明代理并最终把请求完整转发给 OpenAI 后端，可显式设置：

```text
backend=openai
```

让 `auto` 选择 v2。

用户也可以强制 `--multi-agent-version v2`，但 `external + v2` 会返回警告，并且不应把测试失败描述成 DeepSeek 模型本身不支持子代理。

## Reasoning effort

支持：

```text
low
high
max
```

默认 `high`。

有效值同时作用于：

1. `DeepSeek.toml` 的 `model_reasoning_effort`；
2. `codex exec` 直连测试；
3. `spawn_agent` 后查询的 SQLite `threads.reasoning_effort`。

这是对上游仓库 issue #3 的完整处理。仅修改 Agent TOML 而直连测试仍硬编码 `high` 不算完成。

## 原生派发验收

`setup` 或 `test` 会通过桌面内置运行时创建隔离验收会话。

必须同时满足：

1. 自定义 Provider 直连成功；
2. 新父任务真实调用 `spawn_agent(agent_type="DeepSeek", fork_turns="none")`；
3. 子 Agent 返回精确口令 `NATIVE_DEEPSEEK_OK`；
4. `$CODEX_HOME/state_*.sqlite` 中对应子线程满足：

```text
model_provider = <profile.provider>
model = <profile.model>
reasoning_effort = <profile.reasoning_effort>
agent_role = DeepSeek
```

只有这四项一致，才能称为真实跨 Provider 原生子 Agent。不能以子 Agent 自述代替数据库路由证据。

## `resume_agent` 已知问题与 workaround

上游 Codex 当前仍有一个独立恢复问题：

- https://github.com/openai/codex/issues/26718
- `resume_agent restores subagent history but not original model/reasoning settings`

该问题描述：恢复子 Agent rollout/history 时，resume path 可能从当前调用者 Config 创建会话，而没有把原子线程持久化的 `TurnContextItem` 中 model / effort 等运行设置重新应用到 Config。

对于跨 Provider DeepSeek，这可能表现为：

```text
第一次 spawn：
model_provider = deepseek
model = deepseek-v4-flash

resume 后：
错误继承父会话 / OpenAI 配置
```

随后就可能出现类似“当前 ChatGPT 账户不支持 deepseek-v4-flash”的错误，因为模型名被交给了错误的 Provider。

这与本项目上游 issue #2 的现象高度一致。

### 当前 workaround

在 Codex 上游修复前：

```text
不要 resume_agent(DeepSeek child)
```

改为：

```text
DeepSeek child -> 返回 compact handoff
parent         -> 保存 handoff
parent         -> fresh spawn DeepSeek
new child      -> handoff + remaining task
```

生成的 DeepSeek Agent 指令已经要求在可能继续工作的任务结束前输出 handoff，包括：

- 已完成工作；
- 剩余工作；
- 相关文件 / symbol；
- blocker。

这不是对 Codex 内核的修复，而是避免恢复时 Provider 丢失的安全策略。将来 `openai/codex#26718` 修复并通过本项目真实路由测试后，可以重新评估允许 `resume_agent`。

## Windows 与文件锁

上游仓库 issue #1 曾报告原脚本无条件导入 Unix-only `fcntl`。

当前底座已经采用：

```text
macOS / Unix -> fcntl
Windows      -> msvcrt
```

并通过同一 `operation_lock` 实现跨平台进程锁。本 fork 不重新引入无条件 `fcntl` 依赖。

Windows 桌面 Codex 自动发现顺序仍由底座负责；常见安装路径和 PATH 都找不到时，可以设置：

```text
CODEX_DESKTOP_BIN
```

## API Key

API Key 从标准输入读取，不写入：

- CLI 参数；
- provider profile；
- `config.toml`；
- 模型目录；
- 临时文件；
- 测试结果；
- 日志摘要。

自定义 Provider 不检查 `sk-` 前缀，因为中转站 Key 命名没有统一规范。只要求：

- 非空；
- 不含换行；
- 长度合理。

不同自定义 `provider + base_url` 使用不同的系统凭据 target，减少切换中转站时误用旧 Key。

## 配置事务

写入前创建带时间戳的备份。底座继续负责：

- 文件锁；
- TOML / JSON 解析；
- 原子写入；
- manifest；
- 冲突检测；
- 失败回滚；
- 卸载恢复。

Provider profile 只在成功完成 `setup` / `repair` 后保存。失败的候选配置不应成为后续 `status` 的默认 profile。

## 视觉输入

DeepSeek V4 Flash 子 Agent 按文本 Agent 使用。父 Agent 必须先检查图片、视频和截图，把必要事实写成文字任务包；子 Agent 不应声称自己看过视觉材料。

## 上游 issue 对应关系

### oil-oil/codex-deepseek-subagent#1

Windows `fcntl`：当前上游底座已解决，本 fork 保留现有条件导入与 `msvcrt` 锁。

### oil-oil/codex-deepseek-subagent#2

恢复 DeepSeek 后 Provider/模型错误：根因属于 Codex resume path；使用 fresh spawn + handoff workaround，并跟踪 `openai/codex#26718`。

### oil-oil/codex-deepseek-subagent#3

reasoning effort：本 fork 已支持 `low` / `high` / `max`，profile 持久化，并统一用于 Agent 配置、直连和 SQLite 验收。
