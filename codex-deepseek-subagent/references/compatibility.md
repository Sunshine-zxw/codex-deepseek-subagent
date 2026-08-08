# 兼容性与安全边界

## 支持范围

- macOS；
- Windows；
- Python 3.11+；
- ChatGPT/Codex 桌面应用至少启动过一次；
- DeepSeek 官方 API；
- OpenAI Responses wire API 兼容的第三方 API / 中转站；
- 默认模型 `deepseek-v4-flash`；
- 自定义模型 ID，例如 `gpt-5.6-luna`；
- 自动或自定义子 Agent 角色名；
- 任意非空 reasoning effort；
- 可选的自定义 reasoning 档位集合；
- multi-agent `auto` / `v1` / `v2`。

Provider 仅支持 `/chat/completions` 不属于可验证的 Codex 原生子 Agent 兼容范围。

## Codex 对 ReasoningEffort 的当前行为

当前 Codex 源码中的 `ReasoningEffort` 已知值包括：

```text
none
minimal
low
medium
high
xhigh
max
ultra
```

同时还存在：

```text
Custom(String)
```

未知但非空的字符串会作为模型自定义 effort 保留下来，而不是被客户端强制限制为固定枚举。

因此本 fork 不再人为限制 `low/high/max`。

管理器只做基础安全校验：

- 非空；
- 去除首尾空白；
- 不允许控制字符；
- 限制异常超长输入。

是否真的被目标 Provider / 模型接受，最终由真实 `codex exec` 和原生子 Agent 验收决定。

相关上游实现位于：

```text
openai/codex/codex-rs/protocol/src/openai_models.rs
```

## 配置位置

默认 `CODEX_HOME`：

```text
~/.codex
```

主要文件：

```text
$CODEX_HOME/config.toml
$CODEX_HOME/models-with-deepseek.json
$CODEX_HOME/agents/<Role>.toml
$CODEX_HOME/codex-deepseek-subagent/manifest.json
$CODEX_HOME/codex-deepseek-subagent/provider-profile.json
$CODEX_HOME/codex-deepseek-subagent/backups/
```

`models-with-deepseek.json` 名称为了兼容原项目继续保留，但内容现在可以包含非 DeepSeek 的自定义子 Agent 模型。

程序不修改顶层 `model` 或顶层 `model_provider`，主任务继续使用用户原来的 Codex 模型与登录方式。

## 动态角色

角色是 Codex `spawn_agent(agent_type=...)` 使用的标识，不要求等于实际模型 ID。

默认自动推导：

```text
gpt-5.6-luna       -> Luna
gpt-5.6-terra      -> Terra
gpt-5.6-sol        -> Sol
deepseek-v4-flash  -> DeepSeek
```

也支持用户显式指定：

```text
--role FastLuna
```

或：

```text
--agent-name FastLuna
```

### 路径解析顺序

必须先加载 Profile 并设置动态 `ROLE`，再调用底层 `resolve_paths()`。

正确顺序：

```text
bootstrap paths
    ↓
读取 provider-profile.json
    ↓
解析 / 推导 Role
    ↓
apply_profile(Role)
    ↓
重新 resolve_paths
    ↓
$CODEX_HOME/agents/<Role>.toml
```

否则会出现 TOML 内 `name = "Luna"`，但文件仍错误写到 `DeepSeek.toml` 的假动态角色问题。

### 角色变化

Profile 保存 `role_auto`：

- `true`：角色由模型自动推导；模型变化时角色跟着变化；
- `false`：角色由用户手工指定；模型变化时保留角色。

角色变化后，旧 Agent 文件只在 manifest 能证明它仍是本 Skill 未修改的受管理文件时删除。

如果 hash 不一致，旧文件必须保留并返回警告。

## 模型目录策略

这是自定义模型接入最重要的兼容层之一。

### 模型已经存在于 Codex 基础目录

例如：

```text
gpt-5.6-luna
```

如果 `codex debug models` / 当前基础 catalog 已经包含相同 slug，本 fork：

1. 复制该模型原始条目；
2. 保留 display name、context window、tool 能力、input modalities、原 reasoning 档位等元数据；
3. 只覆盖当前 Profile 明确要求修改的字段；
4. 不再用 DeepSeek V4 Flash 的模型条目覆盖 Luna。

这避免了“API 模型是 Luna，但本地模型能力描述还是 DeepSeek”的错误。

### 模型不存在于基础目录

只有当自定义模型 slug 不存在时，管理器才获取 DeepSeek 官方模型条目作为 Codex-compatible text-agent 结构模板，然后：

- 替换 slug；
- 修改显示信息；
- 写入当前 reasoning 配置；
- 再做真实直连与原生派发验收。

这是 fallback，不代表未知模型与 DeepSeek 的所有能力完全相同。

对于差异很大的未知模型，真实验收结果优先于静态模板。

## Reasoning 模型目录

Codex 当前内部模型 catalog 使用：

```text
default_reasoning_level
supported_reasoning_levels
```

`--reasoning-effort` 控制当前默认值。

例如：

```text
--reasoning-effort ultra
```

### 未指定 `--reasoning-efforts`

如果模型已经存在：

- 保留原有 `supported_reasoning_levels`；
- 当前选择值如果不在列表中，则追加。

例如原模型：

```text
low
medium
high
xhigh
max
ultra
```

选择：

```text
--reasoning-effort ultra
```

目录保持原列表，只把默认档位设为 `ultra`。

### 指定 `--reasoning-efforts`

例如：

```text
--reasoning-efforts "minimal,medium,high,xhigh,ultra"
```

表示用户明确知道 Provider / 模型支持这些档位，因此用该列表覆盖当前模型目录暴露的 effort 选项。

列表会：

- 去重；
- 保持输入顺序；
- 自动包含当前 `--reasoning-effort`。

### 未知模型

如果模型本身不存在于基础目录，又没有提供 `--reasoning-efforts`，至少暴露当前选择的 effort，而不是继承 DeepSeek 固定三档。

## API Key 与凭据

API Key 不要求：

```text
sk-...
```

允许中转站自己的 token 格式。

只要求：

- 非空；
- 无换行；
- 长度处于合理范围。

### 系统凭据

macOS：

```text
Keychain
```

Windows：

```text
Credential Manager
```

DeepSeek 官方模式兼容原凭据 target：

```text
codex-deepseek-api-key
```

自定义 Provider 根据：

```text
provider + base_url
```

生成独立 target：

```text
codex-deepseek-api-key-<hash>
```

切换 Provider 不会把 API Key 写入 `config.toml`。

旧 Provider 的系统凭据不会因为切换 Provider 被静默删除；只有用户明确要求删除当前凭据时才执行删除。

## v1 / v2 原生派发

### 默认外部 Provider

```text
backend = external
multi_agent_version = auto
```

解析为：

```text
v1
```

原因不是 DeepSeek V4 Flash 模型本身，而是当前 Codex v2 跨 Provider Agent 消息路径可能包含外部 Provider 无法消费的 OpenAI 专有 Agent payload。

因此下面这些场景默认都按跨 Provider 处理：

```text
Sol -> DeepSeek 官方 API
Sol -> 中转站 -> DeepSeek
Sol -> 中转站 -> GPT-5.6 Luna
Sol -> Kimi / GLM / 其他第三方 Provider
```

模型名字属于 OpenAI 并不足以证明中转站路径可以安全使用 v2。

### OpenAI backend

只有确认 Provider 路径完整透明地支持 OpenAI v2 Agent 协议时才使用：

```text
backend = openai
multi_agent_version = auto
```

此时解析为：

```text
v2
```

### 手动覆盖

允许：

```text
--multi-agent-version v1
--multi-agent-version v2
```

`external + v2` 会产生明确警告。

## 原生派发验收

`setup` / `test` 使用桌面 Codex runtime 建立隔离任务。

验收必须同时满足：

1. 直连请求成功；
2. 父 Agent 真正调用 `spawn_agent`；
3. 子 Agent 返回测试口令；
4. SQLite `threads` 表对应 child thread 元数据正确。

实际值来自当前 Profile：

```text
model_provider = <profile.provider>
model = <profile.model>
reasoning_effort = <profile.reasoning_effort>
agent_role = <profile.role>
```

不能只以返回文本中的模型自述判断路由成功。

## `resume_agent` 边界

上游 Codex 当前存在一种恢复行为风险：

```text
历史线程恢复成功
但原子 Agent 的 model/provider/reasoning 没有可靠恢复
```

这会表现为：

```text
原来：provider=relay, model=gpt-5.6-luna
resume 后：使用父线程/OpenAI 配置尝试启动 gpt-5.6-luna
```

从而可能出现类似：

```text
当前 ChatGPT 账户不支持该第三方模型
```

本 Skill 无法在配置层修复 Codex 内核的线程恢复实现，因此采用 workaround：

```text
old child
   ↓
compact handoff
   ↓
fresh spawn same role
   ↓
continue with handoff
```

这是一项兼容策略，不应描述成 `resume_agent` 已被修复。

## Windows

底层管理器使用：

```python
try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None
```

Windows 使用 `msvcrt` 文件锁。

不要重新改成无条件 `import fcntl`。

Codex Desktop runtime 自动发现失败时允许通过：

```text
CODEX_DESKTOP_BIN
```

指定 `codex.exe`。

## 配置事务

写入前创建备份。

管理器：

- 使用进程锁避免并发修改；
- 先生成候选 TOML / JSON；
- 解析验证后原子替换；
- setup / test 失败时恢复事务；
- 不静默覆盖不属于本 Skill 的冲突配置；
- 角色文件删除前检查 manifest 与 hash。

## 视觉输入

默认自定义子 Agent 按 text-only 角色处理。

图片、视频、截图等视觉输入应由父 Agent 先检查，再把必要事实转成文字任务包。

即使底层模型理论上支持视觉，也不要在这个 Skill 尚未明确声明并验证视觉工具链前让子 Agent 声称自己看过视觉材料。

## 安全结论

本 Skill 的兼容性判定优先级：

```text
真实 Codex runtime 验收
    >
SQLite 路由元数据
    >
Provider / 模型静态声明
    >
模型名称或品牌推测
```

“模型叫 GPT”“接口号称 OpenAI 兼容”“子 Agent 自称某模型”都不足以单独证明原生路由正确。
