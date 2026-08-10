---
name: codex-deepseek-subagent
description: 配置、检查、测试、修复或管理 Codex 原生第三方子 Agent；支持多个 Provider、多个 Agent、Responses 直连或轻量 Chat Completions bridge、独立凭据、自定义角色/reasoning，以及 per-Agent Tool Call 实测。普通编码任务不要重复运行管理流程。
---

# Codex 多 Provider 子 Agent

本 Skill 只负责第三方 Provider / 子 Agent 的配置与兼容性验收，不承接普通编码任务。

## 唯一主入口

```text
scripts/codex_subagent_cli.py
```

Windows：

```text
py -3 <skill-dir>\scripts\codex_subagent_cli.py ...
```

macOS：

```text
python3 <skill-dir>/scripts/codex_subagent_cli.py ...
```

除排障外，不要直接绕过主入口调用：

```text
codex_subagent_manager.py
codex_provider_manager.py
codex_deepseek.py
codex_transport_bridge.py
```

## 不变量

必须保持：

- 主 Codex 顶层模型和 ChatGPT/Codex 登录不变；
- 第三方模型只作为自定义子 Agent；
- 一个 Provider 可以被多个 Agent 复用；
- API Key 只进入 Credential Manager / Keychain，不写聊天、TOML、registry、日志；
- Skill 新注入的模型 `visibility = "hide"`，不进入主会话模型 picker；
- Codex 原本已有模型保持原 visibility；
- 默认角色 text-only；
- 不为了兼容第三方 Provider 自动启用 `danger-full-access`；
- 第三方 `resume_agent` 仍使用 fresh-spawn + handoff workaround。

## 数据模型

主注册表：

```text
$CODEX_HOME/codex-deepseek-subagent/registry.json
```

Transport / Tool Call 状态：

```text
$CODEX_HOME/codex-deepseek-subagent/transport-state.json
```

Agent：

```text
$CODEX_HOME/agents/<Role>.toml
```

模型目录：

```text
$CODEX_HOME/models-with-subagents.json
```

## Provider Transport

Provider 支持两个 transport：

```text
responses
chat_completions_bridge
```

### `responses`

默认。Codex 直接请求第三方 Provider 的 `/responses`。

只有当该接口真实支持以下链路时才算完整兼容：

```text
Responses
+ streaming
+ tool schema
+ tool call
+ tool result continuation
```

“文本能回复”不足以证明兼容。

### `chat_completions_bridge`

当 Provider 的 Responses 文本正常但 Tool Call 有问题，而 `/chat/completions` 工具调用正常时使用。

运行链路：

```text
Codex Responses
  -> 127.0.0.1 transport bridge
  -> Chat Completions
  -> upstream Provider
```

Bridge：

- Python stdlib；
- 只监听 loopback；
- 默认端口 `48671`；
- 不保存 Key；
- 不引入 Node.js / LiteLLM；
- 仅在至少一个使用中的 Provider 选择 bridge 时需要运行。

不要把所有 Provider 无条件切到 bridge；正常 Responses Provider 保持直连。

## Request Profile

Agent 可选：

```text
auto
default
auto-tool-choice
deepseek-thinking
deepseek-nonthinking
```

规则：

- `default`：普通 OpenAI-compatible Chat Completions；
- `auto-tool-choice`：forced/required function tool choice 降为 `auto`；
- `deepseek-thinking`：显式使用 DeepSeek thinking 参数、reasoning effort 映射并把 forced tool choice 降为 auto；
- `deepseek-nonthinking`：显式关闭 DeepSeek thinking；
- `auto`：只在兼容性已明确时使用自动行为；对中转/转售路径不应仅凭模型名推断其支持 DeepSeek 原生参数。

对于 opencode Go / 其他转售路径里的 DeepSeek，如果不确定其是否接受 DeepSeek 原生 `thinking` 参数，优先：

```text
--request-profile auto-tool-choice
```

只有上游明确支持 DeepSeek 原生 thinking 参数时才显式使用：

```text
--request-profile deepseek-thinking
```

## DeepSeek Tool Call bridge 规则

Bridge 必须保留完整的工具上下文：

```text
assistant reasoning_content
  + tool_calls
  -> tool result
  -> next assistant turn
```

实现要求：

1. Responses tool 定义转换成 Chat Completions function tools；
2. forced tool choice 可按 request profile 规范化；
3. Chat `reasoning_content` 转成 Responses `reasoning` item；
4. Tool Result 下一轮再还原成 assistant `reasoning_content`；
5. tool result 必须紧跟对应 assistant tool_calls；
6. 连续 assistant tool-call 片段需要合并；
7. custom tool 可通过 `{input: string}` function shim 往返转换。

不能只让第一轮 tool call 成功而丢掉下一轮 continuation。

## Per-Agent Tool Call 实测

独立执行：

```text
--json tool-test --role <Role>
```

或：

```text
--json tool-test --all
```

测试必须是真实 Provider 请求，固定两轮：

```text
Round 1
  -> 定义 codex_subagent_probe
  -> 模型调用一次
  -> 参数必须为 {"value": 7}

Round 2
  -> 返回 function_call_output
  -> 模型继续
  -> 最终精确返回 TOOL_PROBE_OK
```

至少记录：

```text
first_response
streaming
tool_call
arguments_json
tool_result_continuation
```

最终结果：

```text
pass / fail
```

并保存至 `transport-state.json`，让 `list` / `status` 可见。

`tool-test` 会产生少量第三方 Provider 调用额度，不要把它伪装成纯静态检查。

## 完整 test

```text
--json test --role <Role>
```

现在完整测试顺序是：

```text
文本直连
  -> Tool Call 两轮实测
  -> native spawn_agent
  -> SQLite 路由元数据验收
```

只有这些证据都成立，才可称该 Agent 适合完整 Codex 工具型子代理。

如果 Tool Call fail，但文本/native spawn pass，应明确报告：

```text
text-compatible, tool-incompatible
```

不要将其标记成完整 ready。

## 基本管理流程

### 1. 先读取

```text
--json list
--json status
```

不要先手工编辑 TOML。

### 2. Provider

新增 Responses Provider：

```text
provider-add
  --provider <id>
  --provider-name <name>
  --base-url <url>
  --transport responses
  --backend external
  --multi-agent-version auto
  --api-key-stdin
```

新增 Chat bridge Provider：

```text
provider-add
  --provider <id>
  --provider-name <name>
  --base-url <upstream-url>
  --transport chat_completions_bridge
  --backend external
  --multi-agent-version auto
  --api-key-stdin
```

API Key 必须从 stdin 传入。

### 3. Agent

```text
agent-add
  --provider <id>
  --model <model-id>
  [--role <Role>]
  [--reasoning-effort <value>]
  [--reasoning-efforts "..."]
  [--request-profile <profile>]
```

常见模型角色名可自动推导；只有用户想自定义调用名时才显式设置 `--role`。

reasoning effort 不限 `low/high/max`，允许 Provider 自定义非空字符串。

### 4. 修改

```text
provider-update ...
agent-update ...
repair
```

Transport 或 request profile 变化后必须 `repair`，再 `tool-test`。

### 5. 删除

```text
agent-remove --role <Role>
provider-remove --provider <id>
```

Provider 仍被 Agent 引用时不能静默删除，除非用户明确选择 cascade。

## opencode Go DeepSeek Flash 推荐流程

如果已经证实：

```text
/responses 文本正常
/responses Tool Call 异常
/chat/completions Tool Call 正常
```

则：

```text
provider-update
  --provider opencode_go
  --transport chat_completions_bridge
```

然后：

```text
agent-update
  --role <DeepSeekRole>
  --request-profile auto-tool-choice
```

再：

```text
repair
tool-test --role <DeepSeekRole>
test --role <DeepSeekRole>
```

不要因为模型名是 DeepSeek 就自动启用 `deepseek-thinking`。

## Bridge 管理

```text
bridge-status
bridge-start
bridge-stop
```

通常无需手工 start；`repair` 在 bridge transport 正在使用时会尝试启动。

日志：

```text
$CODEX_HOME/codex-deepseek-subagent/transport-bridge.log
```

## Multi-agent v1/v2

第三方/普通中转：

```text
backend=external
multi-agent-version=auto
=> v1
```

只有真实验证 Provider 路径支持 Codex/OpenAI v2 Agent payload 时才能使用 v2。

多 Provider 同时存在时：

```text
任一正在使用的 Agent 需要 v1
=> 父模型统一 v1
```

只有全部正在使用的 Provider 均为 v2 才切 v2。

## external Provider 审批兼容

当前 Codex Auto-review 可能让第三方子 Agent 去其第三方 Provider 请求内部 `codex-auto-review`，导致 403/404。

本 Skill 的兼容策略是：

```text
external Provider active
=> approvals_reviewer = user
```

这只是把审批交给用户，不关闭 sandbox，不把 approval policy 改成 never，也不使用 danger-full-access。

## Sandbox

Tool Call 协议问题与 Windows sandbox 问题必须分开诊断。

如果出现：

```text
windows sandbox: helper_unknown_error
setup refresh had errors
```

不要把它归因于 Responses/Chat bridge，也不要通过扩大 Agent 权限隐藏问题。

## `resume_agent`

第三方 Provider 的 child thread 继续采用：

```text
old child -> compact handoff -> fresh spawn same role
```

不要声称 Skill 已修复 Codex 内核的 resume model/provider 恢复问题。

## 验收优先级

```text
真实 Provider Tool Call probe
  + native Codex runtime
  + SQLite child metadata
  > 静态 Provider 声明
  > 模型名称/品牌推测
```

详细说明：

```text
references/transport-and-tool-testing.md
references/compatibility.md
references/model-visibility.md
```
