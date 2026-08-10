# 兼容性与安全边界

## 支持范围

当前规范主入口：

```text
scripts/codex_subagent_cli.py
```

`codex_subagent_manager.py` 是底层实现。为兼容旧命令，直接执行它时会转发到 `codex_subagent_cli.py`，因此不会绕过第三方 Provider 的审批兼容处理。

支持：

- macOS；
- Windows；
- Python 3.11+；
- 多个 OpenAI Responses-compatible Provider；
- 多个 Codex 原生自定义子 Agent；
- 一个 Provider 被多个 Agent 共享；
- 自定义 Base URL / Provider ID / Model ID；
- 自动或自定义 Agent 角色名；
- 任意非空 reasoning effort；
- 可选 reasoning effort 档位集合；
- 多 Provider Agent 未显式指定时默认使用 `max`；
- multi-agent `auto / v1 / v2`；
- macOS Keychain / Windows Credential Manager 独立凭据；
- 第三方子 Agent 模型默认隐藏于主会话 picker。

不支持协议转换。只有 `/chat/completions`、Anthropic Messages 或其他兼容层不属于本 Skill 的原生 Provider 兼容范围。

## 配置位置

默认：

```text
CODEX_HOME = ~/.codex
```

主要文件：

```text
$CODEX_HOME/config.toml
$CODEX_HOME/models-with-subagents.json
$CODEX_HOME/agents/<Role>.toml
$CODEX_HOME/codex-deepseek-subagent/registry.json
```

旧单 Profile 文件仍可能存在：

```text
$CODEX_HOME/codex-deepseek-subagent/provider-profile.json
```

新管理器首次运行可以读取它并迁移到 registry。迁移完成后以 `registry.json` 为主。

## Registry 数据模型

Provider 和 Agent 必须分离。

示例：

```text
Providers
├── relay_a
├── relay_b
└── deepseek

Agents
├── Luna     -> relay_a / gpt-5.6-luna
├── Terra    -> relay_a / gpt-5.6-terra
├── Kimi     -> relay_b / kimi-k3
└── DeepSeek -> deepseek / deepseek-v4-flash
```

这样同一 Provider 下多个模型/Agent 只需要一份 Base URL 和一份 API Key。

Provider 记录：

```text
provider
provider_name
base_url
backend
multi_agent_version
credential_target
```

Agent 记录：

```text
role
provider
model
reasoning_effort
reasoning_efforts
role_auto
```

## 凭据

API Key 不要求 `sk-` 前缀。

仅要求：

- 非空；
- 无换行；
- 不异常超长。

凭据不写入：

```text
config.toml
registry.json
Agent TOML
模型目录
日志摘要
```

Windows：

```text
Credential Manager
```

macOS：

```text
Keychain
```

每个 Provider 使用独立 credential target。

新 Provider 默认：

```text
codex-subagent-provider-<provider-id>
```

从旧单 Profile 迁移时保留旧 credential target，从而避免用户无意义地重新输入 API Key。

## Provider 配置块

新管理器只维护自己的 marker：

```text
# BEGIN CODEX-CUSTOM-SUBAGENTS PROVIDERS
...
# END CODEX-CUSTOM-SUBAGENTS PROVIDERS
```

同时会移除旧 DeepSeek 单 Provider marker，避免迁移后重复注册。

如果移除 marker 后仍存在与 registry 同名的非托管 `model_providers.<id>`，视为冲突并停止，不静默覆盖。

## 第三方 Provider 审批兼容

当正在使用的 Agent 引用 `backend = "external"` Provider 时，规范入口会把当前生效配置中的 `approvals_reviewer` 路由到 `user`，避免第三方 Provider 请求不支持的 `codex-auto-review` 模型。它不修改 `approval_policy`、`sandbox_mode` 或 writable roots。

如果配置使用 Codex named profile，兼容层会优先处理当前 `profile` 对应的 `[profiles.<name>]` 配置；删除最后一个 external Agent 后，仍未被用户手动改动的兼容性修改会恢复原值。

配置变化后必须完全退出 Codex、重新打开并新建任务。真实审批链路仍需由用户决定是否允许。

## Agent 文件

每个 Agent 独立文件：

```text
$CODEX_HOME/agents/<Role>.toml
```

例如：

```toml
name = "Luna"
model = "gpt-5.6-luna"
model_provider = "relay_a"
model_reasoning_effort = "xhigh"
```

删除/改名时只清理由上一版 registry 管理的角色文件，不扫描删除其他用户 Agent。

## 角色名

角色是：

```text
spawn_agent(agent_type="<Role>")
```

使用的标识，不要求等于模型 ID。

自动推导示例：

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

手工角色设置后：

```text
role_auto = false
```

模型变化不得擅自改名。

自动角色：

```text
role_auto = true
```

模型变化时允许重新推导。

## ReasoningEffort

Codex 当前已知值包括：

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

同时 Codex 协议允许未知的非空自定义字符串。

因此本项目不能再次把 reasoning effort 限死成：

```text
low/high/max
```

示例自定义值：

```text
adaptive-high
turbo-provider-tier
```

是否真正被目标模型接受，以真实请求为准。

### reasoning_efforts

如果用户/Provider 明确提供模型支持档位：

```text
minimal,low,medium,high,xhigh,ultra
```

管理器将其作为模型级 metadata。

如果多个 Agent 使用相同 model slug，却声明不同档位集合，必须拒绝生成，因为 Codex catalog 的 reasoning metadata 是模型级，不是 Agent 级。

## 模型目录与 picker

新目录：

```text
$CODEX_HOME/models-with-subagents.json
```

### 已有模型

如果 Agent 使用的 model slug 已经存在于 Codex 当前目录：

- 保留原条目；
- 保留原 display name；
- 保留 context/tool/modalities；
- 保留原 picker visibility；
- 不因为它经第三方 Provider 调用就篡改官方模型条目。

例如官方已经存在：

```text
gpt-5.6-luna
```

则官方 Luna 仍按原来的 picker 规则显示。

### 新注入模型

如果 model slug 不存在，为了让 Codex 子 Agent 能解析模型 metadata，需要建立兼容条目。

所有此类条目必须：

```json
"visibility": "hide"
```

因此：

```text
relay-luna
relay-deepseek
kimi-custom
```

可以被子 Agent 使用，但不会出现在主会话模型选择菜单。

Registry 的：

```text
metadata.injected_models
```

记录哪些 slug 是本项目注入的。重建目录时先移除旧注入条目再重新生成，避免重复或残留。

## 多 Provider 的 multi-agent 规则

每个 Provider 有自己的：

```text
backend
multi_agent_version
```

解析：

```text
backend=external + auto -> v1
backend=openai   + auto -> v2
```

显式 `v1/v2` 优先。

### 为什么父模型需要全局决策

Codex 父线程的 multi-agent 版本是父模型级/会话级能力，不适合在同一个父线程里根据每个子 Agent 动态改写。

因此 registry 采用：

```text
只要当前任意 Agent 所使用 Provider effective=v1
→ parent=v1

只有所有当前 Agent 所使用 Provider effective=v2
→ parent=v2
```

这意味着一个普通外部中转站存在时，即使另一个 Provider 已验证 v2，父会话仍保持 v1。

这是刻意的保守兼容策略。

## 为什么 GPT 模型名不代表 backend=openai

例如：

```text
gpt-5.6-luna
```

经普通中转站调用时仍应默认：

```text
backend=external
```

判断依据是 Provider 路径是否完整支持 Codex v2 Agent payload，而不是模型品牌。

## 真实路由验收

`test --role <Role>` 包含两层。

### Provider 直连

显式指定：

```text
model
model_provider
model_reasoning_effort
```

返回固定 marker 才算成功。

### Native spawn

父 Codex 必须真实执行：

```text
spawn_agent(agent_type="<Role>", fork_turns="none")
```

并等待 child 完成。

随后从 SQLite `threads` 元数据核对：

```text
model_provider
model
reasoning_effort
agent_role
```

只有 metadata 与 registry 完全一致才算成功。

不能只相信模型回答“我是 Luna/DeepSeek”。

## `resume_agent`

当前第三方自定义 Agent 仍存在恢复风险：历史可以恢复，但 model/provider/reasoning 可能不按原 child 配置恢复。

因此：

```text
old child
→ compact handoff
→ fresh spawn same role
→ continue
```

这是 workaround，不是 Codex 内核修复。

## Windows sandbox

多 Provider 管理层只管理模型路由和 Agent 配置，不会自动解决 Codex Windows sandbox 本身的 ACL/helper 问题。

出现：

```text
windows sandbox helper_unknown_error
setup refresh had errors
```

应单独诊断 Codex Windows sandbox，而不是误判为 Provider/API 连接失败。

Provider 直连成功但 native spawn 在 shell 工具阶段失败，也可能属于 sandbox 层。

## Windows 文件锁

底层旧管理器继续使用：

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

不能重新引入 Windows 上无条件 import `fcntl` 的回归。

## 项目边界

本项目不承担：

- Chat Completions → Responses；
- Anthropic Messages → Responses；
- OAuth 聚合；
- LiteLLM gateway；
- 本地常驻代理服务；
- 流量统计和托盘 UI；
- 主会话第三方模型 picker 暴露。

如果需求发展到大量异构协议/Provider，应考虑 Codex Router 一类完整路由器。

本项目应保持：

```text
Responses-compatible Provider
+
Native Codex Subagent
+
Multi Provider Registry
+
Hidden injected models
```

## 验证优先级

兼容性判断：

```text
真实 Codex runtime 验收
    > SQLite child metadata
    > config/catalog 静态检查
    > Provider 文档
    > 模型名称推测
```

未经真实测试，不应声称某 Provider/模型已完整兼容 native subagent。
