# 子 Agent 模型可见性

本 fork 默认把 **仅由 Skill 为子 Agent 注入的模型条目** 从 Codex 主会话模型选择器中隐藏。

## 规则

- 如果目标模型原本不在 Codex 的基础模型目录中，Skill 会添加必要的模型元数据，并写入：

  ```json
  "visibility": "hide"
  ```

  该模型仍可由自定义 Agent TOML 通过 `model` + `model_provider` 精确引用，但不会作为新增项出现在主会话模型 picker 中。

- 如果目标模型原本就在 Codex 模型目录中，例如某个原生 GPT 模型，Skill 保留该模型原来的 `visibility`，不会因为配置子 Agent 而把官方模型隐藏或改变其 picker 行为。

## 示例

自定义中转站模型：

```text
relay/deepseek-v4-flash
```

若 Codex 原目录没有该 slug，则 Skill 注入隐藏元数据；日常通过：

```text
spawn_agent(agent_type="DeepSeek", fork_turns="none")
```

调用，而不是从主会话模型菜单选择它。

对于原本已经存在的：

```text
gpt-5.6-luna
```

如果 Codex 自己已经将它列在模型菜单中，Skill 不改变这件事。这里的可见模型是 Codex 原生条目，不是 Skill 额外增加的菜单项。

## 为什么使用 `hide`

Codex 当前模型元数据将模型可见性与 picker/API 暴露分开处理。这个 fork 使用 `hide` 的目的，是让模型仍保留运行时元数据供子 Agent 精确引用，同时避免新增到普通主会话的模型选择界面。
