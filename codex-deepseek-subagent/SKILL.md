---
name: codex-deepseek-subagent
description: 配置、检查、测试、修复或管理 Codex 原生第三方子 Agent；支持多个 Responses-compatible Provider、多个 Agent、独立凭据、自定义角色和 reasoning effort。普通已配置后的编码任务不要重复运行管理流程。
---

# Codex 多 Provider 子 Agent

本 Skill 负责第三方 Provider、原生子 Agent 配置和真实路由验收，不承接普通编码任务。

## 主入口

新的规范入口：

```text
scripts/codex_subagent_cli.py
```

它在多 Provider 管理器之上增加第三方 Provider 的审批兼容处理。

底层实现：

```text
scripts/codex_subagent_manager.py
scripts/codex_provider_manager.py
scripts/codex_deepseek.py
```

除调试兼容问题外，不要绕过主入口直接使用底层脚本。

Windows 使用：

```text
py -3 <skill-dir>\scripts\codex_subagent_cli.py ...
```

macOS 使用：

```text
python3 <skill-dir>/scripts/codex_subagent_cli.py ...
```

## 核心目标

保持：

```text
主 Codex / ChatGPT 登录
        │
        │ spawn_agent
        ▼
多个自定义子 Agent
        │
        ├── Provider A
        ├── Provider B
        ├── Provider C
        └── Provider D
```

必须遵守：

- 不替换主 Codex 顶层模型或 ChatGPT 登录方式；
- 第三方模型通过独立 `model_provider` 和独立系统凭据运行；
- 一个 Provider 可以被多个 Agent 复用；
- Agent 与 Provider 分离存储；
- Provider 必须真正兼容 Codex 所需的 Responses API、流式事件和工具调用；
- 只有 `/chat/completions` 不足以证明兼容；
- API Key 不出现在聊天、命令参数、TOML、模型目录或 registry；
- Windows 使用 Credential Manager；macOS 使用 Keychain；
- 新注入的第三方模型必须默认 `visibility = "hide"`，不进入主会话模型选择菜单；
- Codex 原本已有的模型必须保留原 picker visibility；
- reasoning effort 不限制为固定三档；
- 第三方 `resume_agent` 继续使用 fresh-spawn + handoff workaround；
- 不通过 `danger-full-access`、关闭 sandbox 或绕过审批来修复第三方 Provider 兼容问题。

## Registry

主注册表：

```text
$CODEX_HOME/codex-deepseek-subagent/registry.json
```

逻辑结构：

```text
providers
├── relay_a
├── relay_b
└── deepseek

agents
├── Luna     -> relay_a / gpt-5.6-luna
├── Terra    -> relay_a / gpt-5.6-terra
├── Kimi     -> relay_b / kimi-k3
└── DeepSeek -> deepseek / deepseek-v4-flash
```

Agent 文件：

```text
$CODEX_HOME/agents/<Role>.toml
```

例如：

```text
$CODEX_HOME/agents/Luna.toml
```

## 第三方 Provider 的审批兼容

### 已知问题

当父会话使用：

```text
approvals_reviewer = "auto_review"
```

而子 Agent 使用第三方 / 中转 Provider 时，如果子 Agent 的操作需要真实审批，当前 Codex 可能让 Guardian 通过该第三方 Provider 请求内部模型：

```text
codex-auto-review
```

很多第三方 Provider 并不提供这个模型，常见结果是 400 / 403 / 404，并导致原操作 fail-closed。

这不是仓库 ACL 或工作区写权限问题。

### 本 Skill 的安全策略

只要 registry 中存在正在使用的：

```text
backend = external
```

主入口在 `reconcile` 后自动确保：

```text
approvals_reviewer = "user"
```

含义：

- workspace 内本来不需要审批的操作仍按原 sandbox 执行；
- 真正需要越过 sandbox、网络或其他受保护边界的操作交给用户审批；
- 不让第三方 Provider 去调用 `codex-auto-review`；
- 不改变 `approval_policy`；
- 不改变 `sandbox_mode`；
- 不扩大 writable roots；
- 不自动切换 `danger-full-access`。

兼容脚本：

```text
scripts/external_provider_approval_fix.py
```

通常不要单独调用；规范入口会自动处理。

如果 `status` 返回：

```text
external_provider_approval_compat.status = "would_patch"
```

运行：

```text
... codex_subagent_cli.py --json repair
```

然后完全重启 Codex 并新建任务。

### 不要采用的错误修法

不要因为 `codex-auto-review` 失败就在单个 `Luna.toml` 中强行依赖：

```toml
approval_policy = "never"
sandbox_mode = "workspace-write"
```

作为修复依据。

当前 Codex 的 spawn 流程会在应用角色配置后重新同步父任务的运行时 approval policy、approvals reviewer 和 permission profile，因此这些角色字段不是解决该 Provider 兼容问题的可靠边界。

## 多 Provider 的 multi-agent v1 / v2

Provider 级配置：

```text
backend = external | openai
multi_agent_version = auto | v1 | v2
```

默认：

```text
external + auto -> v1
openai   + auto -> v2
```

全局父模型规则：

```text
只要任一正在使用的 Agent 需要 v1
→ 父模型统一 v1

只有所有正在使用的 Agent 都明确解析为 v2
→ 父模型才使用 v2
```

普通中转站即使模型名是 `gpt-5.6-luna`，也默认按 `backend=external` 处理；模型品牌不能证明 Provider 路径支持 OpenAI v2 Agent payload。

## 角色名

常见模型默认自动推导：

```text
gpt-5.6-luna       -> Luna
gpt-5.6-terra      -> Terra
gpt-5.6-sol        -> Sol
deepseek-v4-flash  -> DeepSeek
kimi-*             -> Kimi
qwen-*             -> Qwen
glm-*              -> GLM
claude-*            -> Claude
gemini-*            -> Gemini
```

用户需要自定义时再传：

```text
--role FastLuna
```

角色名决定：

```text
spawn_agent(agent_type="<Role>")
```

角色不是 Provider，也不是模型 ID。

## Reasoning effort

当前档位：

```text
--reasoning-effort <value>
```

允许任意非空、无控制字符的 Provider/模型档位，例如：

```text
minimal
low
medium
high
xhigh
max
ultra
turbo-provider-tier
```

如果用户明确知道完整档位列表，可传：

```text
--reasoning-efforts "low,medium,high,xhigh"
```

不要自行把未知档位映射成 `low/high/max`。

## 管理命令

查看全部：

```text
... codex_subagent_cli.py --json list
```

状态：

```text
... codex_subagent_cli.py --json status
```

旧单 Profile 迁移：

```text
... codex_subagent_cli.py --json migrate
```

迁移后或 Skill 升级后建议执行：

```text
... codex_subagent_cli.py --json repair
```

添加 Provider：

```text
provider-add \
  --provider relay_a \
  --provider-name "Relay A" \
  --base-url "https://relay.example/v1" \
  --backend external \
  --multi-agent-version auto \
  --api-key-stdin
```

添加 Agent：

```text
agent-add \
  --provider relay_a \
  --model gpt-5.6-luna \
  --reasoning-effort high
```

更新：

```text
provider-update
agent-update
```

删除：

```text
provider-remove
agent-remove
```

测试单个：

```text
... codex_subagent_cli.py --json test --role Luna
```

测试全部：

```text
... codex_subagent_cli.py --json test --all
```

## 真实验收

不能只相信子 Agent 自述。

`test` 必须验证：

```text
Provider 直连
    ↓
父 Codex 原生 spawn_agent
    ↓
子 Agent 返回测试口令
    ↓
读取 state_*.sqlite
    ↓
核对：
model_provider
model
reasoning_effort
agent_role
```

所有证据一致才算路由成功。

## Windows sandbox

Windows sandbox 报错与 Provider 兼容是两类问题。

如果出现：

```text
windows sandbox: helper_unknown_error
setup refresh had errors
```

先排查 Windows sandbox/ACL/unelevated 模式，不要把它误诊成 API Key 或模型问题。

如果写 workspace 成功、但一到 approval-required 操作就出现 `codex-auto-review` 4xx，则优先按“第三方 Provider 审批兼容”处理。

## resume_agent

第三方 Agent 当前继续采用：

```text
旧 Agent 返回 compact handoff
        ↓
fresh spawn 同一 Role
        ↓
把 handoff 作为新任务上下文
```

不要把 `resume_agent` 当成可靠继续路径，直到上游确认恢复 model/provider/reasoning 的问题已修复并经过真实验收。

## 安全边界

- 不把 API Key 写入配置或聊天；
- 不静默覆盖用户未受管理的 Provider 配置；
- 不因为第三方 API 报错扩大 sandbox；
- 不通过关闭审批规避 Provider 不支持 `codex-auto-review` 的问题；
- Provider 兼容性优先采用“用户审批”降级，而不是“无审批”降级；
- 配置变化后提示完全重启 Codex，并新建任务。

详细说明见：

```text
references/compatibility.md
references/model-visibility.md
```
