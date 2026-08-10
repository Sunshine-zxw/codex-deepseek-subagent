<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="Codex Custom Provider Subagent">
</p>

# Codex Custom Provider Subagent

这个 fork 在原 `codex-deepseek-subagent` 基础上扩展为一个轻量的 **Codex 多 Provider / 多子 Agent 管理器**。

目标是保持 Codex 主模型与 ChatGPT/Codex 登录不变，把第三方模型作为原生 custom subagent 使用：

```text
GPT-5.6 Sol / 其他 Codex 主模型
ChatGPT / Codex 登录
        │
        │ native spawn_agent
        ▼
Luna / DeepSeek / Kimi / GLM / 自定义角色
        │
        ├── Provider A
        ├── Provider B
        ├── Provider C
        └── Provider D
```

第三方模型默认只作为子 Agent 使用。Skill 新注入的模型目录条目会设置：

```json
"visibility": "hide"
```

因此不会污染 Codex 主会话的模型选择菜单。Codex 原本已有的模型保持原来的 picker 可见性。

## 当前能力

- 多 Provider / 多 Agent 注册表；
- 一个 Provider 可复用给多个 Agent；
- Windows Credential Manager / macOS Keychain 独立凭据；
- 自定义角色名；
- 自定义 reasoning effort / reasoning 档位列表；
- `responses` 直连；
- 轻量 `chat_completions_bridge`；
- per-Agent Tool Call 真实两轮兼容性测试；
- native `spawn_agent` + SQLite 路由验收；
- external Provider Auto-review 兼容处理；
- 新注入模型默认从主会话 picker 隐藏；
- multi-agent v1/v2 保守路由。

本项目仍刻意不做 Anthropic Messages 转换、OAuth Provider 聚合、流量统计、托盘程序等完整 Router 功能。

## 唯一规范入口

以后统一使用：

```text
codex-deepseek-subagent/scripts/codex_subagent_cli.py
```

Windows：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py ...
```

macOS：

```bash
python3 codex-deepseek-subagent/scripts/codex_subagent_cli.py ...
```

除排障外，不要直接绕过主入口调用：

```text
codex_subagent_manager.py
codex_provider_manager.py
codex_deepseek.py
codex_transport_bridge.py
```

## 配置文件

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

合并模型目录：

```text
$CODEX_HOME/models-with-subagents.json
```

## Transport

每个 Provider 可以选择：

```text
responses
chat_completions_bridge
```

### `responses`

默认。Codex 直接请求 Provider 的 `/responses`。

文本回复正常并不代表完整兼容，至少还要验证：

```text
streaming
+ tool schema
+ tool call
+ tool result continuation
```

### `chat_completions_bridge`

当 Provider 的 `/responses` 文本正常但 Tool Call 有问题，而 `/chat/completions` 的工具调用正常时使用。

```text
Codex Responses
  -> 127.0.0.1:48671 Python bridge
  -> Chat Completions
  -> upstream Provider
```

Bridge 只监听 loopback，不保存 API Key，不引入 Node.js / LiteLLM。

不要把所有 Provider 无条件切换到 bridge；正常 Responses Provider 保持直连。

## Request Profile

每个 Agent 可以独立选择：

```text
auto
default
auto-tool-choice
deepseek-thinking
deepseek-nonthinking
```

推荐规则：

- `default`：普通 OpenAI-compatible Chat Completions；
- `auto-tool-choice`：将 forced/required function tool choice 降为 `auto`；
- `deepseek-thinking`：显式启用 DeepSeek thinking 参数并映射 reasoning effort；
- `deepseek-nonthinking`：显式关闭 DeepSeek thinking；
- `auto`：保守默认，不仅凭模型名推断转售 Provider 支持原生 DeepSeek thinking 参数。

对于 opencode Go / 其他转售路径中的 DeepSeek，优先从：

```text
--request-profile auto-tool-choice
```

开始测试。

## 添加 Provider

### Responses Provider

```powershell
$relayKey = Read-Host "Relay API Key"
$relayKey | py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json provider-add `
  --provider relay_a `
  --provider-name "Relay A" `
  --base-url "https://relay.example.com/v1" `
  --transport responses `
  --backend external `
  --multi-agent-version auto `
  --api-key-stdin
Remove-Variable relayKey
```

### Chat Completions bridge Provider

```powershell
$relayKey = Read-Host "Relay API Key"
$relayKey | py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json provider-add `
  --provider opencode_go `
  --provider-name "opencode Go" `
  --base-url "https://opencode.ai/zen/go/v1" `
  --transport chat_completions_bridge `
  --backend external `
  --multi-agent-version auto `
  --api-key-stdin
Remove-Variable relayKey
```

API Key 只从 stdin 输入，并保存到系统凭据库。

## 添加 Agent

例如 Luna：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json agent-add `
  --provider relay_a `
  --model "gpt-5.6-luna" `
  --reasoning-effort max
```

常见模型可自动推导角色名，例如：

```text
gpt-5.6-luna       -> Luna
gpt-5.6-terra      -> Terra
deepseek-v4-flash  -> DeepSeek
kimi-*             -> Kimi
qwen-*              -> Qwen
glm-*               -> GLM
```

只有想自定义调用名时才需要显式：

```text
--role <Role>
```

reasoning effort 不限制固定三档；Provider 可使用自定义非空字符串。

## opencode Go DeepSeek Flash 推荐配置

如果已经确认：

```text
/responses 文本正常
/responses Tool Call 异常
/chat/completions Tool Call 正常
```

先把 Provider 切换成 bridge：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json provider-update `
  --provider opencode_go `
  --transport chat_completions_bridge
```

再把 Agent 设为保守工具选择兼容模式：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json agent-update `
  --role DeepSeek `
  --request-profile auto-tool-choice
```

然后：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json repair
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json tool-test --role DeepSeek
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json test --role DeepSeek
```

不要仅因为模型名是 DeepSeek 就自动启用 `deepseek-thinking`。

## Tool Call 实测

单个 Agent：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json tool-test --role Luna
```

全部：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json tool-test --all
```

测试固定两轮：

```text
Round 1
  -> 定义 codex_subagent_probe
  -> 必须调用一次
  -> 参数必须为 {"value": 7}

Round 2
  -> 返回 function_call_output
  -> 模型继续
  -> 最终必须返回 TOOL_PROBE_OK
```

记录：

```text
first_response
streaming
tool_call
arguments_json
tool_result_continuation
```

只有全部成立才记为 `pass`。

## 完整 Agent 测试

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json test --role Luna
```

或：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json test --all
```

完整测试顺序：

```text
文本直连
  -> Tool Call 两轮实测
  -> native spawn_agent
  -> SQLite 路由元数据验收
```

如果文本和 native spawn 正常、Tool Call 失败，应视为：

```text
text-compatible, tool-incompatible
```

不能标成完整 ready。

## 查看状态

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json list
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json status
```

`list/status` 会显示 Provider transport、Agent request profile，以及最近一次 Tool Call 兼容结果。

## 修改 / Repair

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json provider-update ...
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json agent-update ...
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json repair
```

Transport 或 request profile 改变后必须 `repair`，然后重新 `tool-test`。

## Bridge 管理

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json bridge-status
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json bridge-start
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json bridge-stop
```

正常情况下无需手动启动；当正在使用 bridge transport 时，`repair` 会尝试启动它。

默认：

```text
127.0.0.1:48671
```

日志：

```text
$CODEX_HOME/codex-deepseek-subagent/transport-bridge.log
```

## Multi-agent v1 / v2

第三方普通中转建议：

```text
backend=external
multi-agent-version=auto
=> v1
```

只有真实验证支持 Codex/OpenAI v2 Agent payload 的路径才配置 v2。

多 Provider 同时存在时采用保守规则：

```text
任一正在使用的 Agent 需要 v1
=> 父模型统一 v1
```

## external Provider 审批兼容

当前 Codex Auto-review 在第三方子 Agent 场景可能尝试通过第三方 Provider 请求内部 `codex-auto-review`，导致 403/404。

本项目的兼容策略：

```text
external Provider active
=> approvals_reviewer = user
```

这不会关闭 sandbox，不会把 approval policy 改成 `never`，也不会自动启用 `danger-full-access`。

## 模型选择菜单

Skill 新注入的第三方模型：

```json
"visibility": "hide"
```

因此只用于子 Agent，不进入主会话 model picker。

Codex 原本已有的模型保持原 visibility。

## `resume_agent`

第三方 Provider 子线程暂时继续使用：

```text
old child -> compact handoff -> fresh spawn same role
```

不要把 `resume_agent` 当作第三方 custom-provider Agent 的可靠主路径，直到 Codex 上游修复并经过真实验收。

## Windows Sandbox

Tool Call / Provider 协议问题与 Windows sandbox 问题要分开诊断。

出现：

```text
windows sandbox: helper_unknown_error
setup refresh had errors
```

不要通过扩大 Agent 权限来隐藏问题。

## 开发验证

```powershell
py -3 scripts\test_manager.py
py -3 scripts\test_provider_manager.py
py -3 scripts\test_multi_provider_manager.py
py -3 scripts\test_external_provider_approval_fix.py
py -3 scripts\test_transport_bridge.py
```

测试代码已经加入仓库。没有实际运行测试的环境不得声称测试已通过。

## 使用当前开发分支

当前 transport bridge / Tool Call probe 等新功能位于：

```text
agent/custom-provider-support
```

在这些修改合并到 `main` 之前，必须确保 Codex 实际读取的是这个分支对应的 Skill 文件，而不是仓库默认 `main` 的旧版本。最稳妥的方式是使用本地 checkout 到该分支后的 `codex-deepseek-subagent/` Skill 目录。

详细说明：

```text
codex-deepseek-subagent/SKILL.md
codex-deepseek-subagent/references/transport-and-tool-testing.md
codex-deepseek-subagent/references/compatibility.md
codex-deepseek-subagent/references/model-visibility.md
```
