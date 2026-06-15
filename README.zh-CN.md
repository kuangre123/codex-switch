# Codex Switch

> 一个很小的 macOS 工具，用来一键切换 Codex 官方登录和自定义 API，并尽量保留当前对话上下文。

Codex Switch 适合经常在两种模式之间切换的人。它的核心特性是：切换官方 OpenAI / 自定义 API 后，当前 Codex thread 和上下文历史会尽量保留，你可以继续原来的对话，不用重新开一个会话。

- **官方 OpenAI 模式**：使用 ChatGPT/OpenAI 登录，provider 是 `openai`。
- **自定义 API 模式**：使用 API key，把请求转发到兼容 OpenAI 的接口，provider 是 `custom`。

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
```

通过 App 切换模式时，会自动同步当前会话上下文并重启 Codex，让你回到同一条对话里继续使用新的 provider。

App 启动后会自动检查 GitHub Releases，右上角会显示“已是最新版”或可用的新版本号。

选择官方 OpenAI 模式时，App 会隐藏自定义 API 地址和自定义模型，已保存的自定义配置仍会保留，方便之后切回自定义 API。

官方模型支持从预设菜单选择，也可以手动输入新的模型名。

修改默认 API 地址：

```bash
codex-switch config set --local-base-url https://your-endpoint.example.com
```

或者在 App 里点 **Settings** 修改。

## 安全说明

每次切换前都会备份：

```text
~/.codex/backups/
```

切换时会保留现有 `auth.json` 里的官方 ChatGPT 登录态。自定义 API Key 会写入 custom provider 的 `experimental_bearer_token`，这样自定义 API 可以用，同时官方登录不会被清掉。

通过 macOS App 切换时，工具会先做 Provider Sync：原地更新已有会话 rollout 和 Codex Desktop sqlite 里的 `model_provider`，所以当前 thread id 和上下文历史都会保留，不需要分叉到新会话。随后 App 会自动优雅重启 Codex，让正在运行的 Codex Desktop 重新加载新的 provider。

如果只用命令行，也可以手动同步已有会话元数据：

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
