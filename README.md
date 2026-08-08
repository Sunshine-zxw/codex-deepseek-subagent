<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="codex-deepseek-subagent：为 Codex 注册第三方原生子 Agent">
</p>

# Codex Custom Provider Subagent

这个 fork 在原 `codex-deepseek-subagent` 的基础上，把“DeepSeek 专用配置”扩展成了一个更通用的 **Codex 原生子 Agent Provider 管理层**。

它仍然完整支持 DeepSeek 官方 API，同时也可以把 OpenAI Responses 兼容的第三方 API / 中转站模型注册为 Codex 原生自定义子 Agent，例如：

- DeepSeek V4 Flash；
- 中转站提供的 `gpt-5.6-luna`；
- 中转站提供的 `gpt-5.6-terra`；
- 其他能够被 Codex Responses Provider 正常调用的模型。

主 Codex 会话不会被替换。典型架构是：

```text
GPT-5.6 Sol 主 Agent
ChatGPT / Codex 登录
        │
        │ spawn_agent
        ▼
Luna / DeepSeek / 自定义角色
        │
        ├── 自定义 Provider
        ├── 中转站 API Key
        └── 指定模型
```

## 主要能力

- DeepSeek 官方 API 与自定义 Responses Provider 双模式；
- 自定义 `base_url`、Provider 名、模型 ID；
- API Key 不要求 `sk-` 前缀；
- API Key 保存在 macOS Keychain / Windows Credential Manager；
- 子 Agent 角色名支持自动推导或手工指定；
- reasoning effort 不再限定 `low/high/max`；
- 支持给模型目录显式声明自定义 reasoning 档位；
- `multi-agent` 支持 `auto / v1 / v2`；
- 第三方 Provider 默认通过 `auto -> v1` 避开当前跨 Provider v2 兼容问题；
- 保留直连测试、原生 `spawn_agent` 测试、SQLite 路由元数据验收；
- 针对当前 `resume_agent` 可能丢失第三方 model/provider 的问题使用 fresh-spawn + handoff 策略；
- 保留原项目的备份、原子写入、冲突检测与回滚机制。

## 使用前提

- macOS 或 Windows；
- Python 3.11+；
- ChatGPT/Codex 桌面应用至少运行过一次；
- 主 Codex 配置中存在明确的父模型；
- 第三方接口需要真正兼容 Codex 所使用的 Responses API 路径、流式事件和工具调用。

只支持 `/chat/completions` 并不等于可以作为 Codex 原生子 Agent。

## 管理入口

这个 fork 推荐使用：

```text
codex-deepseek-subagent/scripts/codex_provider_manager.py
```

原来的：

```text
codex-deepseek-subagent/scripts/codex_deepseek.py
```

仍作为底层稳定管理器，由新的 Provider 管理层复用。

Windows 示例：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py status --json
```

macOS 示例：

```bash
python3 codex-deepseek-subagent/scripts/codex_provider_manager.py status --json
```

## 配置 DeepSeek 官方 API

仍然可以保持原来的官方模式：

```powershell
$deepseekKey = Read-Host "DeepSeek API Key"
$deepseekKey | py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py setup `
  --official `
  --api-key-stdin `
  --json
Remove-Variable deepseekKey
```

默认：

```text
Provider: deepseek
Model: deepseek-v4-flash
Role: DeepSeek
Reasoning effort: high
Backend: external
Multi-agent: auto -> v1
```

## 配置中转站的 GPT-5.6 Luna

例如你的中转站是：

```text
https://relay.example.com/v1
```

模型 ID：

```text
gpt-5.6-luna
```

可以这样配置：

```powershell
$relayKey = Read-Host "Relay API Key"
$relayKey | py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py setup `
  --base-url "https://relay.example.com/v1" `
  --model "gpt-5.6-luna" `
  --provider "relay" `
  --provider-name "My Relay" `
  --backend external `
  --api-key-stdin `
  --json
Remove-Variable relayKey
```

这里没有指定 `--role`。

管理器会根据模型名自动推导：

```text
gpt-5.6-luna -> Luna
```

最终生成的 Agent 类似：

```toml
name = "Luna"
model = "gpt-5.6-luna"
model_provider = "relay"
model_reasoning_effort = "high"
```

角色文件会真正写到：

```text
$CODEX_HOME/agents/Luna.toml
```

而不是继续写入 `DeepSeek.toml`。

## 子 Agent 名称需要自己设置吗？

不需要。

默认采用自动推导：

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

如果无法识别，会从模型 ID 的最后一段生成一个安全的角色名。

如果你想自己命名，再显式传：

```powershell
--role FastLuna
```

也可以使用等价别名：

```powershell
--agent-name FastLuna
```

以后在 Codex 里就说：

```text
让 FastLuna 子代理检查这个项目。
```

自动生成的角色会在模型变化时自动更新。例如：

```text
Luna + gpt-5.6-luna
        ↓ repair --model gpt-5.6-terra
Terra + gpt-5.6-terra
```

如果角色是你手工指定的，则模型变化时保留你的名字。

## Reasoning effort

### 不再限制为三档

`--reasoning-effort` 现在接受任意非空、无控制字符的字符串。

例如：

```powershell
--reasoning-effort minimal
--reasoning-effort medium
--reasoning-effort xhigh
--reasoning-effort ultra
--reasoning-effort max
--reasoning-effort turbo-provider-tier
```

这与当前 Codex 的模型协议一致：Codex 对未知但非空的 reasoning effort 可以按自定义值处理。

### 指定当前使用档位

例如 Luna 使用 `ultra`：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py repair `
  --reasoning-effort ultra `
  --json
```

Agent TOML、直连测试和 SQLite 子线程验收会统一使用同一个 `ultra`。

### 显式声明模型支持的所有档位

如果中转站告诉你它支持：

```text
minimal
medium
high
xhigh
ultra
```

可以配置：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py repair `
  --reasoning-effort xhigh `
  --reasoning-efforts "minimal,medium,high,xhigh,ultra" `
  --json
```

`--reasoning-efforts` 是可选的。

规则：

- 如果模型 ID 已经存在于 Codex 当前模型目录，例如 `gpt-5.6-luna`，管理器优先保留该模型原有能力和 reasoning 档位；
- 如果你指定 `--reasoning-efforts`，则用你提供的列表覆盖模型目录中的可选档位；
- 当前选中的 `--reasoning-effort` 会自动确保包含在可选列表中；
- 对 Codex 模型目录中不存在的新模型，管理器才使用兼容模板建立模型条目。

这也避免了把 Luna 的模型元数据错误替换成 DeepSeek 的三档模板。

## Multi-agent v1 / v2 / auto

默认建议：

```text
--backend external
--multi-agent-version auto
```

结果：

```text
external + auto -> v1
```

适用于：

```text
Codex 主 Agent
    ↓
第三方 Provider / 中转站
    ↓
DeepSeek / Luna / Kimi / GLM / 其他模型
```

如果你的 Provider 是能够完整透明处理 OpenAI Agent 协议的 OpenAI 后端路径，可以显式设置：

```text
--backend openai
```

此时：

```text
openai + auto -> v2
```

也可以手工强制：

```powershell
--multi-agent-version v1
--multi-agent-version v2
```

对于普通中转站，即使模型名字是 `gpt-5.6-luna`，也不要仅凭模型名称把 `backend` 设置成 `openai`。这里描述的是 Provider 路径能否安全处理 v2 Agent 协议，而不是模型品牌。

## 配置完成后怎么使用

看到：

```text
status: ready
restart_required: true
new_task_required: true
```

之后：

1. 完全关闭 Codex；
2. 重新打开；
3. 新建一个会话。

如果角色是自动生成的 `Luna`，直接说：

```text
让 Luna 子代理扫描一下这个项目，找出明显 bug，你最后审核它的结论。
```

或者：

```text
把后端代码交给 Luna，主 Agent 自己检查前端，最后合并结果。
```

底层是原生：

```text
spawn_agent(agent_type="Luna", fork_turns="none", ...)
```

日常编码任务不需要再次运行 `setup`。

## 查看当前 Profile

```powershell
py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py profile --json
```

输出包含：

```text
base_url
model
provider
role
role_auto
reasoning_effort
reasoning_efforts
backend
multi_agent_version
effective_multi_agent_version
credential_target
```

## 状态检查与真实测试

静态状态：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py status --json
```

真实测试：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py test --json
```

`test` 不只检查配置文件，还会进行：

```text
自定义 Provider 直连
        ↓
主 Codex 原生 spawn_agent
        ↓
子 Agent 返回测试口令
        ↓
读取 state_*.sqlite
        ↓
校验 provider / model / reasoning / role
```

只有这些证据一致才算真实路由成功。

## 修改已有配置

模型变化、父模型变化、reasoning 变化或 Provider 变化时使用 `repair`。

例如 Luna 改成 Terra：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py repair `
  --model "gpt-5.6-terra" `
  --json
```

如果原角色是自动生成的：

```text
Luna -> Terra
```

旧角色文件如果仍由本 Skill 完整管理，会安全删除；如果检测到用户手工改过，则保留并给出警告。

更换 API Key：

```powershell
$newKey = Read-Host "New API Key"
$newKey | py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py repair `
  --replace-api-key-stdin `
  --json
Remove-Variable newKey
```

## resume_agent 注意事项

当前 Codex 的第三方自定义子 Agent 在 `resume_agent` 场景可能恢复历史但丢失原 model/provider/reasoning 配置。

因此这个 Skill 的日常策略是：

```text
旧子 Agent
   ↓
返回 compact handoff
   ↓
停止
   ↓
spawn 一个同角色的新 Agent
   ↓
把 handoff 作为上下文继续
```

不要依赖：

```text
resume_agent(第三方子 Agent)
```

直到 Codex 上游把恢复配置问题修复并经过实际验收。

## 上游 Issue 对应情况

### Windows `fcntl`

上游当前管理器已经采用 `fcntl` / `msvcrt` 条件导入和跨平台锁逻辑。本 fork 保留该实现并增加回归测试。

### `resume_agent` 后出现“ChatGPT 账户不支持第三方模型”

本 fork 不伪造一个无法在 Skill 层真正实现的内核修复，而是使用 fresh-spawn + handoff workaround，并在 Skill 日常调用规则中禁止把第三方 Agent 的 `resume_agent` 当成可靠路径。

### Reasoning effort 固定为 high

本 fork 已处理：

- 当前 effort 可配置；
- 不再限制 `low/high/max`；
- 支持 Codex 已知档位与 Provider 自定义字符串；
- 可显式声明模型目录中的 reasoning 档位集合；
- Agent TOML、直连测试和 SQLite 验收统一使用 Profile 值。

## 安全与回滚

- API Key 只从标准输入读取；
- macOS 使用 Keychain；
- Windows 使用 Credential Manager；
- 自定义 Provider 使用按 Provider + Base URL 派生的独立 credential target；
- Key 不写入 TOML、模型目录或测试输出；
- 配置写入前创建备份；
- 写入与测试失败时恢复事务；
- 不修改主任务顶层模型或 ChatGPT 登录方式；
- 角色改名时只有确认旧 Agent 文件仍属于本 Skill 管理才自动删除。

详细实现边界见：

```text
codex-deepseek-subagent/references/compatibility.md
```

Skill 执行契约见：

```text
codex-deepseek-subagent/SKILL.md
```

## 开发验证

```bash
python3 scripts/test_manager.py
python3 scripts/test_provider_manager.py
```

Windows：

```powershell
py -3 scripts\test_manager.py
py -3 scripts\test_provider_manager.py
```

## License

[MIT](./LICENSE)
