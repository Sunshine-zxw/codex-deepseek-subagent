<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="Codex Custom Provider Subagent">
</p>

# Codex Custom Provider Subagent

这个 fork 在原 `codex-deepseek-subagent` 基础上扩展为一个轻量的 **Codex 多 Provider / 多子 Agent 管理器**。

目标不是替代 Codex 的主模型，也不是做本地协议代理，而是保持：

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

第三方模型默认只作为子 Agent 使用。Skill 为未知模型注入的模型目录条目会设置：

```json
"visibility": "hide"
```

因此它们不会污染 Codex 主会话的模型选择菜单。

如果某个模型本来就是 Codex 自带模型，例如官方 `gpt-5.6-luna`，管理器会保留它原来的 picker 可见性，不会误隐藏官方模型。

## 适合什么场景

推荐用于：

- 主 Agent 继续使用 ChatGPT / Codex 订阅；
- 需要 1～5 个左右第三方 Provider；
- 一个 Provider 下可以挂多个子 Agent；
- 第三方接口已经真正兼容 Codex 所需的 OpenAI Responses API；
- 不希望第三方子 Agent 模型出现在主会话模型菜单；
- 不需要 LiteLLM、后台代理服务、Chat Completions/Anthropic Messages 协议转换。

如果第三方接口只有 `/chat/completions`、Anthropic Messages，或者需要 OAuth/大量厂商协议转换，应该考虑类似 Codex Router 的完整路由层，而不是继续扩大本项目范围。

## 当前架构

新的规范主入口：

```text
codex-deepseek-subagent/scripts/codex_subagent_cli.py
```

它维护：

```text
$CODEX_HOME/codex-deepseek-subagent/registry.json
```

注册表分成两个独立层：

```text
Providers
├── relay_a
├── relay_b
└── deepseek

Agents
├── Luna     -> relay_a / gpt-5.6-luna
├── Terra    -> relay_a / gpt-5.6-terra
├── DeepSeek -> deepseek / deepseek-v4-flash
└── Kimi     -> relay_b / kimi-k3
```

每个 Agent 生成独立文件：

```text
$CODEX_HOME/agents/Luna.toml
$CODEX_HOME/agents/Terra.toml
$CODEX_HOME/agents/DeepSeek.toml
$CODEX_HOME/agents/Kimi.toml
```

多个 Agent 可以共享同一个 Provider 和 API Key。

## 与旧管理器的关系

保留：

```text
codex-deepseek-subagent/scripts/codex_provider_manager.py
```

作为旧单 Profile 兼容管理器。

原始底层管理器仍是：

```text
codex-deepseek-subagent/scripts/codex_deepseek.py
```

已有单 Profile 配置无需重新输入全部参数，规范入口可以迁移：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_cli.py --json migrate
```

以后统一使用 `codex_subagent_cli.py`。历史路径 `codex_subagent_manager.py` 仍保留，但直接执行时会自动转发到规范入口，避免绕过第三方 Provider 审批兼容层。

## 快速开始：添加第一个 Provider

Windows：

```powershell
$relayKey = Read-Host "Relay API Key"
$relayKey | py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json provider-add `
  --provider relay_a `
  --provider-name "Relay A" `
  --base-url "https://relay.example.com/v1" `
  --backend external `
  --multi-agent-version auto `
  --api-key-stdin
Remove-Variable relayKey
```

`backend=external + auto` 会按 v1 处理第三方跨 Provider 子 Agent。

API Key：

- 不写入 TOML；
- 不写入 registry；
- Windows 存入 Credential Manager；
- macOS 存入 Keychain；
- 每个 Provider 使用独立 credential target。

## 添加 Luna 子 Agent

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json agent-add `
  --provider relay_a `
  --model "gpt-5.6-luna" `
  --reasoning-effort max
```

没有写 `--role` 时会自动推导：

```text
gpt-5.6-luna       -> Luna
gpt-5.6-terra      -> Terra
gpt-5.6-sol        -> Sol
deepseek-v4-flash  -> DeepSeek
kimi-*             -> Kimi
qwen-*             -> Qwen
glm-*              -> GLM
claude-*           -> Claude
gemini-*           -> Gemini
```

如果要自定义名称：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json agent-add `
  --provider relay_a `
  --model "gpt-5.6-luna" `
  --role FastLuna `
  --reasoning-effort high
```

之后直接在 Codex 里说：

```text
让 Luna 子代理检查这个项目，你最后审核它的结论。
```

或：

```text
让 FastLuna 子代理先分析这个 bug。
```

## 一个 Provider 挂多个 Agent

例如同一个中转站同时提供 Luna 和 Terra：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json agent-add `
  --provider relay_a `
  --model "gpt-5.6-luna" `
  --reasoning-effort xhigh

py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json agent-add `
  --provider relay_a `
  --model "gpt-5.6-terra" `
  --reasoning-effort high
```

最终：

```text
relay_a
├── Luna
└── Terra
```

不需要为每个 Agent 重复保存 API Key。

## 添加第 2～4 个 Provider

重复 `provider-add` 即可：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json provider-add `
  --provider relay_b `
  --provider-name "Relay B" `
  --base-url "https://relay-b.example.com/v1" `
  --backend external `
  --api-key-stdin
```

然后把 Agent 指向它：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json agent-add `
  --provider relay_b `
  --model "kimi-k3" `
  --role Kimi `
  --reasoning-effort high
```

## Reasoning effort

`--reasoning-effort` 不限制固定三档，可以使用 Codex 已知档位，也允许 Provider 自定义非空字符串：

```text
none
minimal
low
medium
high
xhigh
max
ultra
adaptive-high
turbo-provider-tier
```

如果 Provider 明确告诉你完整档位列表：

```powershell
--reasoning-effort xhigh `
--reasoning-efforts "minimal,low,medium,high,xhigh,ultra"
```

`--reasoning-efforts` 是可选的。

## Multi-agent v1 / v2

每个 Provider 独立保存：

```text
backend
multi_agent_version
```

默认第三方中转站：

```text
backend = external
multi_agent_version = auto
→ effective v1
```

完整透明支持 OpenAI v2 Agent 协议的路径才配置：

```text
backend = openai
multi_agent_version = auto
→ effective v2
```

### 多 Provider 时的全局规则

父 Codex 会话的 multi-agent 版本不是每个子 Agent 独立切换的，因此注册表采用保守规则：

```text
任意已使用 Provider 需要 v1
→ 父模型保持 v1

所有已使用 Provider 都明确为 v2
→ 父模型才使用 v2
```

这避免一个外部 Provider 被另一个 v2 Provider 意外拖进不兼容协议。

## 模型选择菜单

本项目的默认目标是：

```text
第三方子 Agent 可调用
≠
第三方模型必须出现在主会话 picker
```

对 Codex 模型目录中原本不存在、由本项目新注入的模型：

```json
"visibility": "hide"
```

所以例如：

```text
relay-luna
relay-deepseek
kimi-k3-custom
```

不会因为注册为子 Agent 就出现在你的主会话模型选择菜单。

对 Codex 原本已有的模型，本项目不改变其 picker visibility。

## 查看全部 Provider / Agent

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json list
```

输出会分别列出：

```text
providers
agents
parent_multi_agent_version
credential_present
```

## 状态检查

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json status
```

检查：

- registry；
- Provider 是否写入 `config.toml`；
- API Key 是否存在；
- Agent TOML 是否一致；
- 模型目录是否被选中；
- 新注入模型是否保持 hidden。

## 测试一个 Agent

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json test --role Luna
```

测试全部：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json test --all
```

真实测试包括：

```text
Provider 直连
    ↓
native spawn_agent
    ↓
等待子线程
    ↓
读取 state_*.sqlite
    ↓
校验 role / provider / model / reasoning
```

## 修改 Provider

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json provider-update `
  --provider relay_a `
  --base-url "https://new-relay.example.com/v1"
```

替换 API Key：

```powershell
$newKey = Read-Host "New API Key"
$newKey | py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json provider-update `
  --provider relay_a `
  --replace-api-key-stdin
Remove-Variable newKey
```

## 修改 Agent

换模型：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json agent-update `
  --role Luna `
  --model "gpt-5.6-terra"
```

自动命名的 Agent 会按模型重新推导角色；手工命名的 Agent 会保留自定义名称，除非使用：

```text
--new-role <name>
```

切换 Provider：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json agent-update `
  --role Luna `
  --provider relay_b
```

## 删除 Agent / Provider

删除 Agent：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json agent-remove --role Luna
```

删除 Provider：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json provider-remove --provider relay_a
```

如果 Provider 仍被 Agent 引用，默认拒绝删除。

确认同时删除这些 Agent 时：

```powershell
--cascade
```

只有明确需要同时删除系统凭据时再加：

```powershell
--remove-credential
```

## Repair

根据当前 registry 重新生成 Provider block、模型目录和所有 Agent TOML：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_subagent_manager.py --json repair
```

## `resume_agent` 注意事项

当前 Codex 的第三方自定义子 Agent 在 `resume_agent` 场景可能恢复历史但丢失原 model/provider/reasoning 配置。

因此仍建议：

```text
旧子 Agent
→ compact handoff
→ fresh spawn 同一 Role
→ 继续任务
```

不要把第三方 Agent 的 `resume_agent` 当成可靠主路径，直到 Codex 上游修复并经过真实验收。

## 项目边界

本项目刻意不实现：

- Chat Completions → Responses 协议翻译；
- Anthropic Messages → Responses；
- OAuth Provider 聚合；
- 本地常驻模型代理服务器；
- 流量统计/托盘程序；
- 把第三方模型主动暴露到主会话 picker。

这些属于 Codex Router 一类完整路由项目的职责。

本项目保持：

```text
Responses-compatible Provider
+
Native Codex Subagent
+
Multi Provider Registry
+
Hidden injected models
```

## 开发验证

新增回归测试：

```bash
python3 scripts/test_manager.py
python3 scripts/test_provider_manager.py
python3 scripts/test_multi_provider_manager.py
python3 scripts/test_external_provider_approval_fix.py
```

Windows：

```powershell
py -3 scripts\test_manager.py
py -3 scripts\test_provider_manager.py
py -3 scripts\test_multi_provider_manager.py
py -3 scripts\test_external_provider_approval_fix.py
```

测试代码已加入仓库；在未实际执行测试的环境中不要声称测试已通过。

更详细的运行约束见：

```text
codex-deepseek-subagent/SKILL.md
codex-deepseek-subagent/references/compatibility.md
```
