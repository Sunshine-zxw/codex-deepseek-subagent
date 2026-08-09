---
name: codex-deepseek-subagent
description: 配置、检查、测试、修复或管理 Codex 原生第三方子 Agent；支持多个 Responses-compatible Provider、多个 Agent、独立凭据、自定义角色和 reasoning effort。普通已配置后的编码任务不要重复运行管理流程。
---

# Codex 多 Provider 子 Agent

本 Skill 只维护第三方 Provider、子 Agent 配置和真实路由验收，不承接普通编码任务。

新的主入口：

```text
scripts/codex_subagent_manager.py
```

旧单 Profile 兼容入口：

```text
scripts/codex_provider_manager.py
```

底层原始管理器：

```text
scripts/codex_deepseek.py
```

已有用户如果只配置过一个旧 Profile，优先迁移，不要求重新输入模型、角色和 Provider 参数：

```text
python3 <skill-dir>/scripts/codex_subagent_manager.py --json migrate
```

Windows 使用 `py -3`。

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
- 第三方接口必须真正兼容 Codex 所需的 Responses API、流式事件和工具调用；
- 只有 `/chat/completions` 不足以证明兼容；
- API Key 不出现在聊天、命令参数、TOML、模型目录或 registry；
- Windows 使用 Credential Manager；macOS 使用 Keychain；
- 每个 Provider 使用独立 credential target；
- 新注入的第三方模型必须默认 `visibility = "hide"`，不进入主会话模型选择菜单；
- Codex 原本已有的模型必须保留原 picker visibility；
- reasoning effort 不限制为固定三档；
- 第三方 `resume_agent` 继续使用 fresh-spawn + handoff workaround。

## Registry 模型

主注册表：

```text
$CODEX_HOME/codex-deepseek-subagent/registry.json
```

逻辑结构：

```text
providers:
  relay_a
  relay_b
  deepseek

agents:
  Luna     -> relay_a / gpt-5.6-luna
  Terra    -> relay_a / gpt-5.6-terra
  DeepSeek -> deepseek / deepseek-v4-flash
  Kimi     -> relay_b / kimi-k3
```

不要为每个 Agent 重复保存同一个 Provider/API Key。

## Provider 管理

### 添加 Provider

至少需要：

- Provider ID；
- Base URL；
- API Key；
- backend 类型。

普通中转站默认：

```text
backend = external
multi_agent_version = auto
```

对应 effective v1。

命令：

```text
python3 <skill-dir>/scripts/codex_subagent_manager.py --json provider-add \
  --provider <id> \
  --provider-name <display-name> \
  --base-url <url> \
  --backend external \
  --multi-agent-version auto \
  --api-key-stdin
```

Provider ID 只能使用字母、数字、下划线和连字符。

### 修改 Provider

```text
provider-update --provider <id> ...
```

替换 API Key 只能通过：

```text
--replace-api-key-stdin
```

### 删除 Provider

```text
provider-remove --provider <id>
```

如果仍有 Agent 引用，默认拒绝。

只有用户明确要求同时移除这些 Agent 时才用：

```text
--cascade
```

只有用户明确要求删除系统凭据时才附加：

```text
--remove-credential
```

## Agent 管理

### 添加 Agent

```text
agent-add --provider <id> --model <model-id>
```

角色名默认自动从模型 ID 推导，不必额外追问。

常见映射：

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

用户想自定义名称时再加：

```text
--role FastLuna
```

Agent 文件必须真正写到：

```text
$CODEX_HOME/agents/<Role>.toml
```

### 修改 Agent

```text
agent-update --role <Role> ...
```

支持：

- 换 Provider；
- 换模型；
- 换 reasoning effort；
- 换 reasoning 档位列表；
- `--new-role` 显式改角色名。

自动生成的角色在模型变化时允许重新推导；手工指定角色必须保留，除非用户显式 `--new-role`。

### 删除 Agent

```text
agent-remove --role <Role>
```

只能删除 registry 管理的对应 Agent 文件，不能扫描并删除其他用户 Agent。

## Reasoning effort

当前档位：

```text
--reasoning-effort <value>
```

必须原样保留用户或 Provider 给出的字符串。有效例子包括：

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
```

不能重新硬编码为 `low/high/max`。

如果 Provider 明确给出完整档位列表：

```text
--reasoning-efforts "minimal,low,medium,high,xhigh,ultra"
```

列表去重、保持顺序，并自动确保当前 `reasoning_effort` 在列表中。

多个 Agent 如果共用同一个模型 ID，但声明了互相冲突的 reasoning 档位集合，管理器必须拒绝生成模糊的模型级元数据。

## 模型目录与 picker

模型目录是模型级元数据，而 Agent 是 `model + provider + role` 的运行配置。

规则：

1. 如果模型已存在于 Codex 当前模型目录，保持原始条目，不修改 picker visibility；
2. 如果模型不存在，需要为子 Agent 生成兼容元数据；
3. 新生成条目必须：

```json
"visibility": "hide"
```

4. registry 记录哪些模型是本 Skill 注入的，后续重建目录时先移除旧注入条目再生成，避免重复；
5. 不主动把第三方模型暴露到主会话 picker。

## Multi-agent v1 / v2

每个 Provider 独立保存：

```text
backend
multi_agent_version
```

`auto` 解释：

```text
external -> v1
openai   -> v2
```

不要因为模型名包含 `gpt-*` 就把中转站标成 `openai`。判断对象是 Provider 路径是否能安全处理 v2 Agent 协议。

### 多 Provider 的父会话规则

父 Codex 模型的 multi-agent 版本是全局能力，因此：

```text
任意当前 Agent 所使用的 Provider effective=v1
→ 父模型保持 v1

所有当前 Agent 所使用的 Provider effective=v2
→ 父模型才允许 v2
```

不能让不同 Agent 在同一父会话里隐式切换父模型的 multi-agent 版本。

## 状态与列表

查看全部：

```text
python3 <skill-dir>/scripts/codex_subagent_manager.py --json list
```

静态状态：

```text
python3 <skill-dir>/scripts/codex_subagent_manager.py --json status
```

状态至少检查：

- registry 是否存在；
- Provider 是否写入 `config.toml`；
- `wire_api = responses`；
- Provider 凭据是否存在；
- Agent TOML 是否与 registry 一致；
- 当前模型目录是否被选中；
- 本 Skill 注入的模型是否保持 hidden。

## Repair

```text
python3 <skill-dir>/scripts/codex_subagent_manager.py --json repair
```

`repair` 根据 registry 重新生成：

```text
Provider block
模型目录
Agent TOML
multi-agent 配置
```

只管理自身 marker block 和 registry 中的 Agent，不静默覆盖同名的非托管 Provider 配置。

## 真实测试

测试单个：

```text
python3 <skill-dir>/scripts/codex_subagent_manager.py --json test --role Luna
```

测试全部：

```text
python3 <skill-dir>/scripts/codex_subagent_manager.py --json test --all
```

真实验收包含：

```text
Provider 直连
    ↓
主 Codex native spawn_agent
    ↓
wait 子线程
    ↓
读取 state_*.sqlite
    ↓
校验 role/provider/model/reasoning
```

不能仅相信子 Agent 自述身份。

## `resume_agent` workaround

第三方 Provider 的子 Agent 当前仍不要依赖 `resume_agent`。

子 Agent 在可能继续的任务结束前返回 compact handoff：

- 已完成工作；
- 剩余工作；
- 关键文件/symbol；
- blocker；
- 继续所需事实。

需要继续时：

```text
fresh spawn 同一 Role
+
上一 Agent handoff
```

如果当前 Codex schema 尚未识别新注册角色，提示用户完全退出 Codex、重新打开并新建任务。不要偷偷用默认 Agent 代替。

## 日常使用

配置成功后，不要每次编码都运行管理器。

用户说：

```text
让 Luna 子代理检查这个项目。
```

主 Agent 应直接：

```text
spawn_agent(agent_type="Luna", fork_turns="none", ...)
```

用户说：

```text
让 DeepSeek 和 Kimi 分别独立检查。
```

可以并行 spawn 对应 registry 角色。

## 项目边界

本 Skill 不实现：

- Chat Completions → Responses 转换；
- Anthropic Messages → Responses 转换；
- OAuth Provider 聚合；
- 本地常驻模型路由服务；
- 流量统计/托盘 UI；
- 主会话第三方模型 picker 管理。

这些属于完整路由器项目职责。

本 Skill 的边界保持：

```text
Responses-compatible Provider
+
Native Codex Subagent
+
Multi Provider Registry
+
Hidden injected models
```

## 回归要求

测试文件：

```text
scripts/test_manager.py
scripts/test_provider_manager.py
scripts/test_multi_provider_manager.py
```

不能声称未实际执行的测试已经通过。

底层 Windows 锁仍必须保持 `fcntl` / `msvcrt` 条件导入。

不能把 reasoning effort、角色名、Provider 或 native test 再次写死成 DeepSeek/high。

更详细兼容性说明见：

```text
references/compatibility.md
```
