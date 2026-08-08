---
name: codex-deepseek-subagent
description: 仅在用户要求配置、检查、测试、修复、切换 Provider、调整子 Agent 名称或 reasoning effort、停用或卸载 Codex 原生第三方子 Agent 时使用；支持 DeepSeek 官方 API 与 OpenAI Responses 兼容的第三方 API/中转站。已配置后的普通编码任务不要重复运行配置流程。
---

# Codex 自定义 Provider 子 Agent

本 Skill 只维护配置与验收，不承接日常编码任务。

推荐管理入口：

```text
scripts/codex_provider_manager.py
```

底层稳定管理器：

```text
scripts/codex_deepseek.py
```

不要手动修改本 Skill 管理的 TOML、模型目录、Agent 文件、manifest 或系统凭据。

## 核心契约

- 主 Codex 顶层模型与 ChatGPT/Codex 登录方式保持不变。
- 第三方模型通过独立 `model_provider` 和独立系统凭据运行。
- Provider 必须支持 Codex 所需的 Responses API 路径、流式事件和工具调用；只有 `/chat/completions` 不足以证明兼容。
- API Key 只通过标准输入传给管理器，不回显、不写入命令参数、临时文件、配置或日志摘要。
- macOS 使用 Keychain；Windows 使用 Credential Manager。
- 子 Agent 角色名不是模型名本身。角色名可以自动从模型 ID 推导，也可以由用户手工指定。
- reasoning effort 不限定固定枚举；只要是非空且无控制字符的字符串，管理器就允许交给当前 Codex 处理。
- 如果模型已经存在于 Codex 当前模型目录，优先保留其原始能力和 reasoning 档位，不要用 DeepSeek 模板覆盖。
- 第三方/跨 Provider 默认 `backend=external`，`multi-agent-version=auto`，实际使用 v1 明文派发。
- 只有确认 Provider 路径能安全处理 OpenAI v2 Agent 协议时才使用 `backend=openai` 或显式强制 v2。

## 子 Agent 角色名

用户不需要为了常见模型额外提供角色名。

首次自定义配置时，如果没有 `--role` / `--agent-name`，管理器自动推导，例如：

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

无法命中已知名称时，从模型 ID 最后一段生成安全角色名。

只有用户明确想自定义调用名称时才传：

```text
--role FastLuna
```

或：

```text
--agent-name FastLuna
```

角色自动生成时，后续模型从 Luna 改成 Terra，`repair --model ...` 会自动把角色从 `Luna` 更新为 `Terra`。

角色手工指定后，后续模型变化必须保留用户自定义名称，除非用户再次显式修改角色。

动态角色必须真正影响 Agent 文件路径：

```text
$CODEX_HOME/agents/<Role>.toml
```

例如：

```text
$CODEX_HOME/agents/Luna.toml
```

不要只修改 TOML 内 `name` 而仍写入 `DeepSeek.toml`。

## Reasoning effort

### 当前档位

使用：

```text
--reasoning-effort <value>
```

必须原样保留用户或模型提供方给出的档位名称。不要自行映射成 `low/high/max`。

有效例子包括但不限于：

```text
none
minimal
low
medium
high
xhigh
max
ultra
turbo-provider-tier
```

### 模型支持档位列表

如果用户或 Provider 明确给出完整档位列表，可以传：

```text
--reasoning-efforts "minimal,medium,high,xhigh,ultra"
```

该参数只用于声明/覆盖模型目录中暴露的 reasoning 档位集合。

规则：

1. 当前 `--reasoning-effort` 必须自动包含在最终档位集合中；
2. 列表去重但保持顺序；
3. 如果没有传 `--reasoning-efforts` 且模型已经存在于 Codex 模型目录，保留原模型档位；
4. 如果选择的当前档位不在原列表中，将它追加进去；
5. 对模型目录中不存在的新模型，才使用兼容模板建立条目，并至少暴露当前选中的档位。

Agent TOML、直连测试、原生子 Agent SQLite 元数据验收必须统一使用 Profile 中同一个 reasoning effort。

## 配置流程

### 1. 先读状态

运行：

```text
python3 <skill-dir>/scripts/codex_provider_manager.py status --json
```

Windows 使用：

```text
py -3 <skill-dir>\scripts\codex_provider_manager.py status --json
```

根据结构化状态继续，不靠文件名或旧聊天内容猜测。

### 2. DeepSeek 官方模式

用户明确要求 DeepSeek 官方 API 时使用：

```text
setup --official
```

缺少凭据时索要 API Key，然后只通过：

```text
--api-key-stdin
```

传入。

### 3. 自定义 / 中转站模式

至少需要明确：

- Base URL；
- 模型 ID；
- API Key。

Provider ID 如果用户没有命名，可以生成简短稳定的安全标识，例如 `relay`；Provider display name 可按中转站名称设置。

角色名通常不要额外追问，由模型 ID 自动推导即可。

reasoning effort 如果用户没有指定，保留当前 Profile 默认；如果用户或 Provider 给出了档位，使用其原始字符串。

普通中转站默认：

```text
--backend external
--multi-agent-version auto
```

### 4. 写入并实时验收

`setup` / `repair` 默认执行：

```text
Provider 配置
    ↓
模型目录合并
    ↓
Agent TOML
    ↓
直连 test
    ↓
原生 spawn_agent
    ↓
SQLite threads 元数据校验
```

验收必须确认实际子线程：

```text
model_provider = Profile provider
model = Profile model
reasoning_effort = Profile effort
agent_role = Profile role
```

并确认子 Agent 返回预期测试口令。

不能仅相信 Agent 自述“我是 Luna / DeepSeek”。

### 5. 配置完成

若结果包含：

```text
restart_required: true
new_task_required: true
```

提示用户完全重启 Codex，并新建任务。

## 日常调用规则

配置成功后，普通任务不要再次运行 Skill 或管理脚本。

主 Agent 根据 Profile 中的实际角色直接调用：

```text
spawn_agent(agent_type="<Role>", fork_turns="none", ...)
```

例如：

```text
spawn_agent(agent_type="Luna", fork_turns="none", ...)
```

用户说：

```text
让 Luna 子代理检查这个项目。
```

就应使用 `Luna` 角色。

不要把角色名 `DeepSeek` 写死在日常规则中。

## `resume_agent` workaround

当前 Codex 版本中，自定义第三方子 Agent 使用 `resume_agent` 时可能恢复历史但丢失原 model/provider/reasoning 配置。

因此对第三方 Provider：

```text
不要把 resume_agent 当成可靠继续路径。
```

子 Agent 在可能继续的任务结束前应返回 compact handoff：

- 已完成工作；
- 剩余工作；
- 关键文件 / symbol；
- blocker；
- 继续执行所需事实。

需要继续时：

```text
fresh spawn 同一 Role
    +
上一 Agent 的 handoff
```

如果当前工具 schema 不认识已配置角色，只提示用户重启 Codex 并新建任务，不要偷偷改用默认 Agent、管理脚本或 `codex exec` 代替用户的日常子任务。

## 管理命令

```text
python3 <skill-dir>/scripts/codex_provider_manager.py <command> --json
```

Windows 使用 `py -3`。

- `profile`：读取当前 Provider profile；
- `status`：只读检查配置、模型目录、凭据和 Codex runtime；
- `setup`：首次配置并验收；
- `test`：直连 + 原生 spawn + SQLite 验收；
- `repair`：按当前父模型和新的 Profile 参数重新应用配置；
- `disable`：停用当前角色，保留 Provider、目录和凭据；
- `uninstall`：移除本 Skill 管理配置；只有用户明确要求时才附带 `--remove-credential`。

Provider/Profile 参数只允许在 `setup` 或 `repair` 中修改。

## 修改角色时

角色变化后，管理器先按新角色生成新的：

```text
$CODEX_HOME/agents/<NewRole>.toml
```

旧角色文件只有在以下条件同时满足时自动删除：

- manifest 标记为本 Skill 管理；
- 当前文件 hash 与上一次受管理版本一致。

如果用户手工改过旧 Agent 文件，必须保留并返回警告，不能静默删除。

## 状态处理

- `ready`：直连、原生路由、数据库元数据和返回口令均通过；
- `configured`：静态配置完整但尚未实时验收；
- `credential_missing`：索要 API Key 后继续原流程；
- `operation_in_progress`：另一个配置操作正在执行，不并发修改；
- `conflict`：报告冲突字段/文件，不静默覆盖用户配置；
- `unsupported`：报告缺失能力；
- `failed`：读取结构化错误；如果已自动回滚，不再手工补改受管理文件。

## Issue 回归要求

### Windows

底层脚本应继续使用条件导入 `fcntl` / `msvcrt`，不能重新引入 Windows 上直接 import `fcntl` 的问题。

### Reasoning effort

不能再次把参数、Agent TOML、直连测试或 SQLite 验收任一环节硬编码为 `high`。

### Resume

不能声称 Skill 已经修复 Codex 内核的 `resume_agent` 配置恢复问题。当前实现是安全 workaround，而不是上游内核修复。

更详细的路径、模型目录策略、v1/v2 原因和安全边界见：

```text
references/compatibility.md
```
