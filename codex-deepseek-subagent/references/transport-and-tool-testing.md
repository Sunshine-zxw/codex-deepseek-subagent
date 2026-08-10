# Transport 与 Tool Call 兼容性测试

这个扩展解决一种常见情况：Provider 的普通文本 Responses 请求正常，但工具调用链路不完整或不兼容。

## Transport 模式

每个 Provider 可以选择：

```text
responses
chat_completions_bridge
```

### `responses`

Codex 直接请求 Provider 的 Responses API：

```text
Codex -> Provider /responses
```

这是默认模式，也是链路最短的模式。Provider 必须真正兼容 Codex 所需的 Responses 流式事件、Tool Call 和 Tool Result continuation。

### `chat_completions_bridge`

Codex 仍然看到 Responses API，但本机轻量 Python bridge 把请求转换成 Chat Completions：

```text
Codex
  -> http://127.0.0.1:48671/providers/<provider>/responses
  -> Responses/Chat translation
  -> Provider /chat/completions
```

Bridge 只监听 loopback，不提供公网监听，不保存 API Key。Codex 仍通过原 Provider 的 Credential Manager / Keychain 凭据获取命令拿到 Key，然后 Authorization 只在本机 bridge 请求期间转发给对应上游。

Bridge 是 Python stdlib 实现，不引入 Node.js、LiteLLM 或独立数据库。只有至少一个正在使用的 Provider 选择 `chat_completions_bridge` 时，`repair`/配置写入才需要它运行。

## Request Profile

每个 Agent 可以配置：

```text
auto
default
auto-tool-choice
deepseek-thinking
deepseek-nonthinking
```

含义：

- `default`：尽量按普通 OpenAI-compatible Chat Completions 发送；
- `auto-tool-choice`：如果 Codex 请求 forced/required function tool choice，bridge 降级成 `auto`；适合“工具可用，但模型/转售商拒绝强制 tool_choice”的路径；
- `deepseek-thinking`：显式启用 DeepSeek thinking，映射 reasoning effort，并把 forced tool choice 降为 `auto`；
- `deepseek-nonthinking`：显式关闭 DeepSeek thinking；
- `auto`：保守自动选择。对于未知中转/转售路径，如果是否支持 DeepSeek 原生 thinking 字段不确定，优先显式使用 `auto-tool-choice` 或 `default`，不要仅凭模型名假设协议能力。

`request_profile` 描述的是请求协议兼容行为，不等于模型品牌。

## DeepSeek thinking 与工具历史

Bridge 借鉴 Codex Router 的兼容策略，但只实现当前 Skill 所需的窄路径：

1. DeepSeek thinking 模式下 forced `tool_choice` 会被规范化成 `auto`；
2. Chat Completions `reasoning_content` 会转换成 Responses `reasoning` item；
3. Codex 返回 Tool Result 后，下一轮 bridge 会把该 reasoning item 还原到 assistant message 的 `reasoning_content`；
4. `assistant.tool_calls` 与对应的 `tool` result 保持相邻顺序；
5. 连续 assistant tool-call 片段会合并，避免严格的 Chat Completions Provider 拒绝历史；
6. Responses custom tool 会被临时映射成一个带 `{input: string}` 的 function tool，再转换回 `custom_tool_call`。

这保证测试覆盖的不只是第一轮 tool call，还覆盖 tool-result continuation。

## Tool Call 实测

独立命令：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json tool-test --role DeepSeekGo
```

测试所有 Agent：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json tool-test --all
```

测试固定定义：

```text
Round 1
  -> Responses request + function tool codex_subagent_probe
  -> 模型必须调用工具并传入 {"value": 7}

Round 2
  -> 返回 function_call_output = "probe-result-7"
  -> 模型必须继续并只返回 TOOL_PROBE_OK
```

输出阶段：

```text
first_response
streaming
tool_call
arguments_json
tool_result_continuation
```

最终状态：

```text
pass
fail
```

最近一次结果会保存在：

```text
$CODEX_HOME/codex-deepseek-subagent/transport-state.json
```

`list` / `status` 会显示每个 Agent 最近一次 Tool Call 兼容性结果。

## 完整 `test`

原来的：

```text
test --role <Role>
```

现在执行：

```text
文本直连
  -> Tool Call 两轮实测
  -> native spawn_agent
  -> SQLite model/provider/reasoning/role 验收
```

因此“能回答文本但工具调用失败”不会再被误判为完整兼容。

## opencode Go + DeepSeek V4 Flash

如果 opencode Go 的 DeepSeek V4 Flash 在 `/responses` 下文本正常、Tool Call 异常，而 `/chat/completions` 工具调用正常，可以把该 Provider 切换到 bridge：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json provider-update `
  --provider opencode_go `
  --transport chat_completions_bridge
```

对 Agent 使用保守的转售路径 profile：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json agent-update `
  --role DeepSeekGo `
  --request-profile auto-tool-choice
```

然后：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json repair
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json tool-test --role DeepSeekGo
```

如果上游明确要求并支持 DeepSeek 原生 `thinking` / `reasoning_content` 参数，再显式改为：

```text
--request-profile deepseek-thinking
```

不要仅因为模型 ID 包含 `deepseek` 就假设中转站接受 DeepSeek 原生参数。

## Bridge 生命周期

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json bridge-status
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json bridge-start
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json bridge-stop
```

默认端口：

```text
127.0.0.1:48671
```

可以通过环境变量修改：

```text
CODEX_SUBAGENT_BRIDGE_PORT
```

日志：

```text
$CODEX_HOME/codex-deepseek-subagent/transport-bridge.log
```

## 边界

- Bridge 当前只面向 OpenAI-compatible Chat Completions，不是通用 Anthropic/Messages 转换器；
- 不支持把图像/音频等复杂 Responses 内容完整翻译到所有上游；默认角色仍是 text-only；
- 上游 `/chat/completions` 本身如果 Tool Call 就有问题，bridge 无法修复模型能力；
- `tool-test` 是协议级真实请求，会产生少量 Provider 调用额度；
- Tool Call probe 通过不代表所有第三方工具 schema 都必然兼容，复杂 MCP/custom tools 仍以真实项目调用为最终证据；
- Bridge 不改变 Sandbox、审批策略或文件权限。
