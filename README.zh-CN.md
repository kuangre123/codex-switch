# Codex Switch

> 一个很小的 macOS 工具，用来把 Codex 官方 OpenAI 和自定义 API 并行配置到一起，也支持 Claude Code。

Codex Switch 适合需要同时保留官方模型和自定义 API 的人。Codex 侧现在不再做“官方 / 自定义”二选一切换，而是把 Official OpenAI provider 和 custom provider 并行写入配置，并把自定义模型注册到 Codex 的模型目录里。用户之后直接在 Codex 里选择模型即可。它也支持 Claude Code，通过官方支持的 `~/.claude/settings.json` 的 `env` 配置切换官方 Claude / 自定义 Claude 兼容 API。

- **官方 OpenAI provider**：使用 ChatGPT/OpenAI 登录，provider 是 `openai`。
- **自定义 API provider**：使用 API key，把请求转发到兼容 OpenAI 的接口，provider 是 `custom`，模型可以显示成你自定义的名字，比如“我的模型”。

默认自定义 API 地址是：

```text
https://jp.icodeeasy.cc
```

## 下载 App

直接到 [GitHub Releases](https://github.com/kuangre123/codex-switch/releases/latest) 下载：

```text
Codex-Switch-macOS.zip
```

解压后把 `Codex Switch.app` 放到 `~/Applications` 或 `/Applications`。首次打开如果 macOS 提示不明开发者，右键选择 **打开**。

## 安装

```bash
git clone https://github.com/kuangre123/codex-switch.git
cd codex-switch
./scripts/install.sh
```

安装后会生成：

```text
~/.local/bin/codex-switch
~/Applications/Codex Switch.app
```

## 使用

打开 App：

```bash
open "$HOME/Applications/Codex Switch.app"
```

命令行：

```bash
codex-switch status
codex-switch local
codex-switch official
codex-switch claude-status
codex-switch claude-local
codex-switch claude-official
```

Codex 现在不是“切换官方/自定义”二选一，而是并行配置：官方 OpenAI 和自定义 API 会同时写入配置，App 保存后重启 Codex，之后你在 Codex 自己的模型选择器里选择要用的模型。

切换 Claude Code 时，App 会修改 `~/.claude/settings.json` 里的 `env`：

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://your-claude-compatible-endpoint",
    "ANTHROPIC_AUTH_TOKEN": "your-token",
    "ANTHROPIC_MODEL": "claude-sonnet-4-6"
  }
}
```

Claude Code 需要重新打开终端会话后读取新配置；它不像 Codex Desktop 那样由 App 自动重启。

App 启动后会自动检查 GitHub Releases，右上角会显示“已是最新版”或可用的新版本号。

选择官方 OpenAI 模式时，App 会隐藏自定义 API 地址和自定义模型，已保存的自定义配置仍会保留，方便之后切回自定义 API。

官方模型支持从预设菜单选择，也可以手动输入新的模型名。

Codex 自定义模型会按官方 `model_catalog_json` 配置写入 `~/.codex/codex-switch-model-catalog.json`，不是只改一个模型字符串。模型 ID 用于 API 请求，显示名称可自定义，比如叫“我的模型”。

自定义 API 提供“新 API Key”安全输入框：留空会继续使用现有 Key，输入新 Key 后会在保存时更新，界面不会回显明文。

App 内置与自身版本匹配的 CLI，更新 App 后不会再因为外部 CLI 版本较旧而参数不兼容。

修改默认 API 地址：

```bash
codex-switch config set --local-base-url https://your-endpoint.example.com
codex-switch configure \
  --base-url https://your-endpoint.example.com \
  --custom-model your-model-id \
  --custom-model-name "我的模型" \
  --official-model gpt-5.2-codex
codex-switch register-model your-model-id --name "我的模型"
```

或者在 App 里点 **Settings** 修改。

## 安全说明

每次保存配置或使用旧切换命令前都会备份：

```text
~/.codex/backups/
~/.claude/backups/
```

Codex 自定义模型目录文件：

```text
~/.codex/codex-switch-model-catalog.json
```

保存时会保留现有 `auth.json` 里的官方 ChatGPT 登录态。自定义 API Key 会写入 custom provider 的 `experimental_bearer_token`，这样自定义 API 可以用，同时官方登录不会被清掉。

通过 macOS App 配置 Codex 时，工具不再做 Provider Sync，也不会把现有 thread 强行改到某个 provider。它只保存官方和自定义 provider 的并行配置，然后重启 Codex 让模型目录重新加载。

旧版切换命令仍然保留，老工作流如果需要也可以手动同步已有会话元数据：

```bash
codex-switch local --migrate-latest
codex-switch official --migrate-latest
```

Provider Sync 会额外触碰这些 Codex Desktop 会话元数据：

```text
~/.codex/sessions/**/rollout-*.jsonl
~/.codex/archived_sessions/**/rollout-*.jsonl
~/.codex/sqlite/state_*.sqlite
~/.codex/state_*.sqlite
~/.codex/backups_state/provider-sync/
```

## 开发

```bash
python3 tests/test_codex_switch.py
./scripts/package-app.sh
```

## License

MIT

---

如果这个工具帮你少改几次配置，帮我给个 star 把。
