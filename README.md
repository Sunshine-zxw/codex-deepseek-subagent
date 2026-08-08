<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="codex-deepseek-subagent：将 DeepSeek 注册为 Codex 原生子 Agent">
</p>

把 DeepSeek V4 Flash 注册为 Codex 原生自定义子 Agent，并支持 **DeepSeek 官方 API** 与 **OpenAI Responses 兼容的第三方 API / 中转站**。主 Codex 会话继续使用原来的 ChatGPT/Codex 登录和模型；DeepSeek 子 Agent 使用独立 Provider 与独立 API Key。

> 本 fork 在上游项目基础上增加了自定义 Provider profile、可配置 reasoning effort、multi-agent v1/v2/auto 策略，以及针对 `resume_agent` 跨 Provider 恢复问题的安全 workaround。

## 适用范围

这个 Skill 用于：

- 首次配置、状态检查和实时验收；
- DeepSeek 官方 API 或自定义 Responses 兼容中转站；
- 自定义模型 ID（例如中转站重命名后的 `deepseek-v4-flash`）；
- 配置 `low` / `high` / `max` reasoning effort；
- 父模型变化、Provider 变化或配置损坏后的 `repair`；
- 停用或卸载 DeepSeek 配置。

普通的编码、探索、实现、评审和验证任务不应重复运行配置流程。配置完成后由当前主 Agent 直接使用原生 `spawn_agent` 派发。

## 要求

- macOS 或 Windows；
- Python 3.11+；
- ChatGPT/Codex 桌面应用至少启动过一次；
- DeepSeek 官方 API Key，或你的中转站 API Key；
- Provider 必须支持 Codex 使用的 **OpenAI Responses wire API**。只有 `/chat/completions` 兼容并不足以保证可作为原生 Codex 子 Agent。

## 安装

安装本 fork：

```bash
npx skills add Sunshine-zxw/codex-deepseek-subagent -g -y
```

重启 Codex 桌面应用并新建任务。

## 快速开始

### 方式一：让 Skill 自动配置

在 Codex 新任务中说：

```text
帮我把 DeepSeek 配置成 Codex 的原生子 Agent。
```

如果你使用中转站，直接说明：

```text
帮我把 DeepSeek 配置成 Codex 原生子 Agent。
我使用自定义中转站，base URL 是 https://example.com/v1，
模型 ID 是 deepseek-v4-flash。
```

Skill 会先检查状态；缺少凭据时再索要 API Key。API Key 只通过标准输入进入管理器，并保存到系统凭据库。

### 方式二：直接运行管理器

本 fork 的推荐入口是：

```text
codex-deepseek-subagent/scripts/codex_provider_manager.py
```

原 `codex_deepseek.py` 保留为上游兼容底座，不建议直接用于自定义中转站配置。

#### DeepSeek 官方 API

macOS/Linux shell：

```bash
python3 codex-deepseek-subagent/scripts/codex_provider_manager.py setup \
  --official \
  --api-key-stdin \
  --json
```

Windows PowerShell：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py setup `
  --official `
  --api-key-stdin `
  --json
```

#### 自定义 API / 中转站

```powershell
py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py setup `
  --base-url "https://example.com/v1" `
  --model "deepseek-v4-flash" `
  --provider "deepseek_proxy" `
  --provider-name "DeepSeek Proxy" `
  --reasoning-effort "high" `
  --multi-agent-version "auto" `
  --backend "external" `
  --api-key-stdin `
  --json
```

如果中转站把模型重命名成：

```text
deepseek/deepseek-v4-flash
```

只需要把 `--model` 改成该实际模型 ID。

API Key **不要求以 `sk-` 开头**。

## Provider profile

成功配置后会保存：

```text
$CODEX_HOME/codex-deepseek-subagent/provider-profile.json
```

默认 `CODEX_HOME` 为 `~/.codex`。

profile 会记录：

- `base_url`
- `model`
- `provider`
- `provider_name`
- `reasoning_effort`
- `multi_agent_version`
- `backend`

因此后续 `status`、`test`、`repair` 会继续使用同一套配置，不会重新强制回到 `https://api.deepseek.com/`。

查看当前 profile：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py profile --json
```

## Reasoning effort

支持：

```text
low
high
max
```

默认：

```text
high
```

例如改为 `max`：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py repair `
  --reasoning-effort max `
  --json
```

该值会统一用于：

1. `$CODEX_HOME/agents/DeepSeek.toml`；
2. DeepSeek / 中转站直连测试；
3. 原生 `spawn_agent` 后的 SQLite 子线程元数据验收。

不会再出现 Agent 配置是 `max`、测试却硬编码 `high` 的情况。

## Multi-Agent v1 / v2 / auto

默认使用：

```text
--multi-agent-version auto
```

`auto` 的当前策略：

```text
backend = external  -> v1
backend = openai    -> v2
```

### 为什么第三方 DeepSeek 默认 v1

当前 Codex multi-agent v2 的跨 Provider 路由可能携带 OpenAI 专有的加密 `agent_message`。普通第三方 Responses 兼容后端能理解 OpenAI API 的 JSON 结构，并不代表它能消费这段 OpenAI 内部加密内容。

因此：

```text
GPT 主 Agent -> DeepSeek 官方 API       -> auto 使用 v1
GPT 主 Agent -> 中转站 -> DeepSeek      -> auto 使用 v1
GPT 主 Agent -> 透明代理 -> OpenAI 后端 -> 可设置 backend=openai，让 auto 使用 v2
```

也可以手工强制：

```text
--multi-agent-version v1
--multi-agent-version v2
```

如果对外部 DeepSeek 强制 v2，管理器会返回警告。

## 日常使用

配置成功并重启 Codex 后，可以直接说：

```text
用 DeepSeek 子 Agent 检查这个项目。
```

正常派发方式是：

```text
spawn_agent(agent_type="DeepSeek", fork_turns="none", ...)
```

### 不要直接 resume 第三方 DeepSeek 子 Agent

截至当前 Codex 版本，`resume_agent` 存在一个上游问题：恢复子线程历史时，可能不会恢复原子 Agent 的 `model` / `model_provider` / `reasoning_effort`，从而错误继承当前父会话配置。对应 OpenAI Codex 上游问题：

- `openai/codex#26718` — `resume_agent restores subagent history but not original model/reasoning settings`

这与上游仓库 issue #2 中“恢复 DeepSeek 后提示 ChatGPT 账户不支持 deepseek-v4-flash”的现象高度一致。

本 fork 当前采用安全 workaround：

```text
旧 DeepSeek 子 Agent
        ↓ 输出 handoff
主 Agent 保存 handoff
        ↓
spawn_agent 新 DeepSeek 子 Agent
        ↓
把 handoff + 新任务一起传入
```

也就是说，**继续同一项工作时优先 fresh spawn，不直接 `resume_agent` 第三方 Provider 线程**，直到 Codex 上游修复恢复时的 Provider/model 配置丢失问题。

## 管理命令

推荐入口：

```text
python3 <skill-dir>/scripts/codex_provider_manager.py <command> --json
```

Windows 用 `py -3`。

支持：

```text
status      只读检查
setup       首次配置
profile     查看当前 Provider profile
test        直连 + 原生 spawn_agent 验收
repair      按当前/新 profile 重建配置并验收
disable     停用 Agent，保留 Provider/profile/凭据
uninstall   移除本 Skill 管理的配置
```

替换当前 Provider 的 API Key：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py repair `
  --replace-api-key-stdin `
  --json
```

卸载并明确删除当前 Provider 凭据：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_provider_manager.py uninstall `
  --remove-credential `
  --json
```

## 验收

`setup` / `test` 会创建隔离验收会话。成功必须同时满足：

1. 直连请求成功；
2. 父 Agent 实际调用原生 `spawn_agent(agent_type="DeepSeek")`；
3. 子 Agent 返回精确口令 `NATIVE_DEEPSEEK_OK`；
4. `$CODEX_HOME/state_*.sqlite` 中子线程元数据与当前 profile 一致：

```text
model_provider = <profile.provider>
model = <profile.model>
reasoning_effort = <profile.reasoning_effort>
agent_role = DeepSeek
```

不能只相信子 Agent 自述。

## Windows

上游早期 issue #1 报告过 Windows 因 `fcntl` 导入失败。当前底座代码已经使用条件导入：

- Unix/macOS：`fcntl`
- Windows：`msvcrt`

因此本 fork 延续原有的跨平台文件锁实现。Windows 自动发现桌面 Codex 失败时，可以设置：

```text
CODEX_DESKTOP_BIN
```

指向实际 `codex.exe`。

## 安全与回滚

- API Key 只从标准输入读取；
- macOS 保存到 Keychain；
- Windows 保存到 Credential Manager；
- 自定义 Provider 使用 `provider + base_url` 派生独立凭据目标，减少不同中转站之间的凭据串用；
- 配置、临时文件、profile 和测试结果都不保存 API Key；
- 写入前创建备份；
- TOML/JSON 解析、配置写入或实时验收失败时恢复本次事务；
- 不修改主任务的顶层 `model` 或顶层 `model_provider`。

更详细的边界见 [兼容性说明](codex-deepseek-subagent/references/compatibility.md)。Skill 执行规则见 [SKILL.md](codex-deepseek-subagent/SKILL.md)。

## 上游 issue 状态

本 fork 针对上游当前三个 issue 的处理：

- **#1 Windows `fcntl`**：上游当前代码已改为 `fcntl` / `msvcrt` 条件导入，本 fork 保留并文档化该实现；
- **#2 resume 后错误回落到 ChatGPT/OpenAI 配置**：根因属于 Codex `resume_agent` 上游恢复配置问题，无法仅由 Skill 修复内核；本 fork 使用 `fresh spawn + handoff` workaround；
- **#3 reasoning_effort 可配置**：本 fork 已支持 `low` / `high` / `max`，持久化到 provider profile，并统一用于配置、直连和 SQLite 验收。

## 开发验证

```bash
python3 scripts/test_manager.py
python3 scripts/test_provider_manager.py
python3 scripts/build_readme_assets.py
```

## License

[MIT](./LICENSE)
