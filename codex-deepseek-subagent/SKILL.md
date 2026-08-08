---
name: codex-deepseek-subagent
description: 仅在用户要求配置、检查、测试、修复、切换 Provider、调整 reasoning effort、停用或卸载 Codex 的 DeepSeek 原生子 Agent 时使用；支持 DeepSeek 官方 API 与 OpenAI Responses 兼容的第三方 API/中转站。普通 DeepSeek API 问题和已配置后的日常编码任务不要重复触发配置流程。
---

# Codex DeepSeek 子 Agent

本 Skill 只维护配置与验收，不承接日常编码任务。推荐管理入口是：

```text
scripts/codex_provider_manager.py
```

它在上游 `scripts/codex_deepseek.py` 的安全事务、备份、凭据和 SQLite 验收逻辑之上增加 Provider profile。不要手动改 TOML、JSON、Agent 文件或系统凭据库。

## 关键契约

- 只使用桌面应用内置的 Codex 运行时；版本仅用于诊断，兼容性以真实派发验收为准。
- 不修改主任务顶层 `model` 或顶层 `model_provider`；主任务继续使用用户原来的 ChatGPT/Codex 登录和模型。
- 支持 DeepSeek 官方 Provider，也支持 OpenAI **Responses wire API** 兼容的自定义 Provider / 中转站。
- 只有 `/chat/completions` 兼容不等于能作为 Codex 原生子 Agent；配置前明确这一限制。
- 父模型从桌面配置动态读取；父模型变化后运行 `repair`。
- DeepSeek 是纯文本 Agent。图片、视频、截图等视觉输入必须由父 Agent 先识别并整理成文字。
- API Key 不要求 `sk-` 前缀，但必须非空；只通过标准输入进入管理器，不在回复、日志或配置中回显。

## Provider profile

管理器将有效配置持久化到：

```text
$CODEX_HOME/codex-deepseek-subagent/provider-profile.json
```

默认 `CODEX_HOME=~/.codex`。

profile 包含：

- `mode`: `official` / `custom`
- `base_url`
- `model`
- `provider`
- `provider_name`
- `reasoning_effort`: `low` / `high` / `max`
- `multi_agent_version`: `auto` / `v1` / `v2`
- `backend`: `external` / `openai`

`status`、`test`、`repair` 必须读取持久化 profile，不得重新把配置硬编码回官方 URL、官方模型 ID 或 `high`。

## Multi-Agent 路由策略

默认：

```text
multi_agent_version = auto
```

当前 `auto` 规则：

```text
backend=external -> v1
backend=openai   -> v2
```

原因：当前 Codex v2 的跨 Provider 派发可能包含 OpenAI 专有的加密 `agent_message`。第三方后端即使兼容 Responses JSON，也不代表能消费 OpenAI 内部加密内容。

对 DeepSeek 官方 API、DeepSeek 中转站、Kimi/GLM/Ollama 等非 OpenAI 后端，默认使用 `external`，因此 `auto -> v1`。

只有用户明确知道中转站最终透明转发至 OpenAI 后端并保留相关协议内容时，才使用：

```text
--backend openai
```

用户可以明确强制 `v1` 或 `v2`；对 `external + v2` 必须报告兼容性警告。

## Reasoning effort

正式支持：

```text
low
high
max
```

默认 `high`。

所选值必须统一用于：

1. `$CODEX_HOME/agents/DeepSeek.toml`；
2. 直连测试的 `model_reasoning_effort`；
3. 原生子线程 SQLite 元数据验收。

这用于解决上游仓库 issue #3 中“配置、测试和验收分别硬编码 high”的问题。

## 日常派发

配置完成后的普通任务不要运行本 Skill、管理脚本或 `codex exec`。主 Agent 直接调用：

```text
spawn_agent(agent_type="DeepSeek", fork_turns="none", ...)
```

当前工具 schema 若不认识 `DeepSeek`，只提示用户重启 Codex / 打开新任务；不得改用默认角色或管理脚本代做日常任务。

## 禁止直接 resume 第三方 DeepSeek 线程

截至当前 Codex 版本，OpenAI Codex 上游存在恢复配置 bug：

```text
openai/codex#26718
resume_agent restores subagent history but not original model/reasoning settings
```

恢复历史时可能不恢复原子 Agent 的 model/provider/reasoning 配置，从而让线程错误继承父会话/OpenAI 配置。这与本项目上游 issue #2 的现象一致。

因此，在该上游问题仍存在时：

- 不对 `DeepSeek` / 自定义 Provider 子线程直接调用 `resume_agent`；
- 如果用户要求“继续上一个 DeepSeek 子 Agent”，优先读取上一子 Agent 已返回的结果或 handoff；
- 新建 `spawn_agent(agent_type="DeepSeek", fork_turns="none")`；
- 把上一轮 handoff、已完成工作、剩余工作和必要文件/符号信息作为新任务上下文传入；
- 如果旧线程没有任何可恢复结果，明确说明上游恢复限制，然后 fresh spawn 重新执行尚未完成的 bounded task。

生成的 DeepSeek Agent 指令要求在可能需要续接时主动返回 compact handoff，降低 fresh spawn 的上下文损失。

不要声称 Skill 已修复 Codex 内核的 `resume_agent`；这里只提供安全 workaround。

## 触发后的流程

1. 运行推荐入口的 `status --json`，根据结构化状态继续。
2. 如果用户只想查看 Provider 设置，运行 `profile --json`。
3. 首次配置运行 `setup`；父模型变化、Provider 变化、模型变化、reasoning effort 变化或配置损坏时运行 `repair`。
4. 官方 DeepSeek 可传 `--official`；自定义 Provider 按用户实际信息传 `--base-url`、`--model` 等参数。
5. 缺少当前 Provider 凭据时索要 API Key；收到后不复述，只通过 `--api-key-stdin` 传入。
6. 用户明确替换已保存 Key 时用 `--replace-api-key-stdin`。
7. `setup` / `test` 使用桌面内置运行时创建隔离验收会话。
8. 验收必须同时检查：直连成功、原生 `spawn_agent`、子 Agent 返回 `NATIVE_DEEPSEEK_OK`、SQLite 子线程元数据匹配当前 profile。
9. 若返回 `new_task_required` / `restart_required`，提示用户重启桌面应用并打开新任务。
10. 最终只汇报状态、Provider、base URL（可显示）、模型、reasoning effort、实际 multi-agent 版本、角色和备份位置；不要输出密钥或原始事件日志。

## 管理命令

macOS：

```text
python3 <skill-dir>/scripts/codex_provider_manager.py <command> --json
```

Windows：

```text
py -3 <skill-dir>\scripts\codex_provider_manager.py <command> --json
```

命令：

- `status`：只读检查运行时、配置、模型目录、凭据、profile 和客户端能力。
- `profile`：只读显示当前 Provider profile。
- `setup`：首次写入配置并验收。
- `test`：执行直连测试和原生 `spawn_agent(agent_type="DeepSeek")` 验收。
- `repair`：按当前或新的 Provider profile 重新应用配置并验收。
- `disable`：停用本 Skill 创建的角色，保留 Provider/profile/凭据。
- `uninstall`：移除本 Skill 管理的配置；只有用户明确要求删除凭据时才传 `--remove-credential`。

### 官方 API 示例

```text
python3 <skill-dir>/scripts/codex_provider_manager.py setup --official --api-key-stdin --json
```

### 自定义中转站示例

```text
python3 <skill-dir>/scripts/codex_provider_manager.py setup \
  --base-url "https://example.com/v1" \
  --model "deepseek-v4-flash" \
  --provider "deepseek_proxy" \
  --provider-name "DeepSeek Proxy" \
  --reasoning-effort high \
  --multi-agent-version auto \
  --backend external \
  --api-key-stdin \
  --json
```

### 切换到 max

```text
python3 <skill-dir>/scripts/codex_provider_manager.py repair --reasoning-effort max --json
```

默认使用当前 `CODEX_HOME`；只有用户明确指定其他 Codex Home 时才传 `--codex-home`。

## 状态处理

- `ready`：直连、原生路由、数据库元数据和返回口令均通过。
- `configured`：静态配置完整，但尚未完成实时验收。
- `credential_missing`：索要当前 Provider API Key 后继续原流程。
- `operation_in_progress`：已有配置操作正在运行，不并发修改。
- `conflict`：报告冲突文件和字段，等待用户决定是否替换。
- `invalid_*`：报告具体 profile 参数错误，不手工绕过验证。
- `unsupported`：报告缺少的系统能力，不按固定版本号猜测兼容性。
- `failed`：读取结构化错误；若程序已回滚，明确说明，不再手改配置。

## Windows issue #1

上游早期 issue #1 报告过 Windows 导入 Unix-only `fcntl` 失败。当前底座已使用条件导入：Unix/macOS 使用 `fcntl`，Windows 使用 `msvcrt`。本 fork 必须保留这个跨平台锁实现，不得重新引入无条件 `fcntl` 依赖。

更详细的路径、协议、resume workaround、版本和安全边界见 [references/compatibility.md](references/compatibility.md)。
