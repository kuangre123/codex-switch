# Codex Switch

> 一键配置 Codex 的自定义 API，同时保留官方 OpenAI 登录。也支持 Claude Code。

Codex Switch 帮你把 Codex 的官方和自定义 API 都配好，保存后模型选择器里只出现当前 provider 的模型，不会混在一起。

- 选「自定义 API」：选择器里只出现你的自定义模型（比如”我的 GLM”），官方模型隐藏。
- 选「官方 OpenAI」：选择器里只出现官方模型（GPT-5.5 等），自定义模型隐藏。
- 两套配置始终保留在 `config.toml` 里，切换只需在 App 里改一下「选择 API 提供方」再保存。
- 国内接口真实模型名可以和官方一样，比如都叫 `gpt-5.5`；工具会自动映射成 `codex-switch/gpt-5.5` 这种 Codex 内部 ID，不会覆盖官方模型。
- 如果你的自定义 API 只支持 Chat Completions，勾选「Chat 适配器」即可，App 会在本地启动代理自动转换。
- Claude Code 侧通过 `~/.claude/settings.json` 切换。

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

App 保存后会自动重启 Codex，模型选择器里只出现当前 provider 的模型。要切换 provider，在 App 里改「选择 API 提供方」再保存即可。

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

Codex 自定义模型会按官方 `model_catalog_json` 配置写入 `~/.codex/codex-switch-model-catalog.json`，不是只改一个模型字符串。App 里的“上游模型 ID”用于真实 API 请求；Codex 里看到的是自动生成的不冲突内部 ID，显示名称可自定义，比如叫“我的模型”。

自定义 API 提供“新 API Key”安全输入框：留空会继续使用现有 Key，输入新 Key 后会在保存时更新，界面不会回显明文。

App 内置与自身版本匹配的 CLI，更新 App 后不会再因为外部 CLI 版本较旧而参数不兼容。

修改默认 API 地址：

```bash
codex-switch config set --local-base-url https://your-endpoint.example.com
codex-switch configure \
  --base-url https://your-endpoint.example.com \
  --custom-model gpt-5.5 \
  --custom-model-name "我的模型" \
  --official-model gpt-5.2-codex
codex-switch register-model codex-switch/gpt-5.5 --name "我的模型"
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
