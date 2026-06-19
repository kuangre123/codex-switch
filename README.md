# Codex Switch

> One tiny macOS app for configuring Official OpenAI and custom API routes side by side in Codex, with Claude Code support too.

Codex Switch is a lightweight helper for configuring multiple coding-agent API routes. For Codex, it keeps Official OpenAI and a custom API provider configured in parallel, registers the custom model in Codex's model catalog, and controls which models appear in the picker based on the active provider. It can also run a local adapter that translates Codex Responses API requests to Chat Completions for OpenAI-compatible APIs that do not support Responses. Claude Code is supported by updating its official Claude login/custom API route through `~/.claude/settings.json`.

Codex Switch 是一个很小的 macOS 工具，用来把 Codex 的官方 OpenAI 和自定义 API 并行配置到一起。它会注册自定义模型到 Codex 的模型目录，并根据当前激活的 provider 自动控制模型选择器里只显示对应的模型，不会官方和自定义混在一起。它同时提供命令行和双击可用的 macOS App，每次保存都会备份配置，并保留官方 ChatGPT 登录态。

它的核心特性是：Codex 侧把 Official OpenAI 和 custom provider 并行保留在配置里，但模型选择器只显示当前激活 provider 的模型；Claude Code 侧继续支持官方 / 自定义路由配置。

官方 OpenAI provider 使用 ChatGPT/OpenAI 登录。自定义 API provider 使用 API key，把请求转发到兼容 OpenAI 的接口，并通过模型目录显示为你自定义的名称，例如”我的模型”。如果自定义接口只支持 Chat Completions，App 可以启用本地 adapter，把 Codex 的 Responses 请求转换过去。adapter 会在收到流式请求时立即返回 SSE 头，避免上游响应慢时 Codex 超时重连。

## Download App 下载 app：

Download the latest macOS app from [GitHub Releases](https://github.com/kuangre123/codex-switch/releases/latest):

```text
Codex-Switch-macOS.zip
```

Unzip it, move `Codex Switch.app` to `~/Applications` or `/Applications`, then open it. On first launch, macOS may ask you to right-click and choose **Open** because the app is unsigned.

中文：你可以直接在 [GitHub Releases](https://github.com/kuangre123/codex-switch/releases/latest) 下载 `Codex-Switch-macOS.zip`。解压后把 `Codex Switch.app` 放进 `~/Applications` 或 `/Applications`，首次打开如果 macOS 提示不明开发者，右键选择 **打开**。

## Why

Codex can use multiple model providers, but configuring a custom provider by hand means editing `~/.codex/auth.json`, `~/.codex/config.toml`, and a model catalog JSON. Codex Switch turns that into one save action: official OpenAI stays available, the custom provider stays available, and Codex's own model picker decides which one to use.

中文：手动配置自定义 provider 需要改 `~/.codex/auth.json`、`~/.codex/config.toml` 和模型目录 JSON。这个工具把它变成一次保存：官方 OpenAI 保留，自定义 provider 也保留，最终由 Codex 自己的模型选择器来选。

## Features

- One-click macOS app: configure Codex or Claude Code, status, settings.
- Codex keeps Official OpenAI and custom API providers configured side by side.
- Provider-aware model picker: only shows models for the active provider, so official and custom models are never mixed.
- Claude Code support through the official `settings.json` `env` block.
- CLI for scripting and quick terminal use.
- Configurable custom API endpoint.
- Default custom API endpoint: `https://jp.icodeeasy.cc`.
- Automatic backups under `~/.codex/backups` and `~/.claude/backups`.
- Preserves existing ChatGPT login tokens while custom mode uses a provider-level bearer token.
- The macOS app restarts Codex after saving so the running app reloads provider and model catalog changes.
- The toolbar automatically checks GitHub Releases and shows whether an update is available.
- Official OpenAI mode hides custom API fields while keeping saved custom settings for later.
- Official model can be selected from a preset menu or typed manually.
- Codex custom providers are configured in parallel with Official OpenAI; users choose the actual model inside Codex.
- Codex custom models are registered with the official `model_catalog_json` config path, including a custom display name.
- Optional local adapter bridges Codex Responses API traffic to Chat Completions for compatible third-party APIs; streams SSE headers immediately to prevent Codex timeout during slow upstream responses.
- Custom API keys can be replaced from the app using a secure field; leave it blank to keep the saved key.
- The app bundles its matching CLI, so app and command behavior stay in sync after updates.
- No Python dependencies beyond the standard library.

## Install

```bash
git clone https://github.com/kuangre123/codex-switch.git
cd codex-switch
./scripts/install.sh
```

The installer creates:

```text
~/.local/bin/codex-switch
~/.local/share/codex-switch
~/Applications/Codex Switch.app
```

If your shell cannot find `codex-switch`, add this to `~/.zshrc`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

中文安装：

```bash
git clone https://github.com/kuangre123/codex-switch.git
cd codex-switch
./scripts/install.sh
```

安装后可以直接打开 `~/Applications/Codex Switch.app`。

## Usage

### macOS app

Open:

```bash
open "$HOME/Applications/Codex Switch.app"
```

The app has three actions:

- **Codex**: save Official OpenAI and custom API side by side, then choose the model inside Codex.
- **Claude Code**: choose `Custom API` or official mode.
- **Status**: show current auth/provider/model.
- **Settings**: edit custom API base URL, custom model, and official model.

When saving Codex settings from the macOS app, Official OpenAI and the custom provider are both kept. The **Target Mode** selector decides which provider Codex should open with after saving. Codex.app is gracefully quit and reopened so the running app reloads the provider list and model catalog. Existing Codex threads are not rewritten.

Claude Code switching updates `~/.claude/settings.json` under `env`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://your-claude-compatible-endpoint",
    "ANTHROPIC_AUTH_TOKEN": "your-token",
    "ANTHROPIC_MODEL": "claude-sonnet-4-6"
  }
}
```

Restart Claude Code terminal sessions after switching, because Claude Code reads these settings at startup.

### CLI

```bash
codex-switch status
codex-switch local
codex-switch official
codex-switch claude-status
codex-switch claude-local
codex-switch claude-official
```

Set custom defaults:

```bash
codex-switch config set \
  --local-base-url https://jp.icodeeasy.cc \
  --local-model gpt-5.5 \
  --local-model-display-name "My Model" \
  --official-model gpt-5.2-codex
```

Configure Codex official and custom providers in parallel:

```bash
codex-switch configure \
  --base-url https://jp.icodeeasy.cc \
  --custom-model my-gpt-5.5 \
  --custom-model-name "My Model" \
  --official-model gpt-5.5 \
  --default-provider custom \
  --chat-adapter
```

`--custom-model` must be a unique model ID that is different from the official Codex model IDs. Codex de-duplicates models by ID, so using `gpt-5.5` for both the official and custom route will hide the custom display name. `--chat-adapter` starts a local launchd service on `127.0.0.1:17638` and stores your real upstream URL separately, so Codex can speak Responses while your upstream API receives Chat Completions.

Temporarily override the custom API endpoint:

```bash
codex-switch local --base-url https://your-endpoint.example.com --model your-model
```

Register a custom Codex model catalog without switching immediately:

```bash
codex-switch register-model your-model --name "My Model"
```

Prompt for and save an API key:

```bash
codex-switch local-login
```

Legacy switch commands are still available for older workflows:

```bash
codex-switch local --migrate-latest
codex-switch official --migrate-latest
```

The macOS app uses `codex-switch configure --restart-codex`, not the legacy provider switch commands.

## What It Changes

Codex Switch edits only the user Codex files:

```text
~/.codex/auth.json
~/.codex/config.toml
~/.codex/codex-switch-state.json
~/.codex/codex-switch-model-catalog.json
```

The legacy `local --migrate-latest` and `official --migrate-latest` commands can still update existing Codex Desktop thread metadata:

```text
~/.codex/sessions/**/rollout-*.jsonl
~/.codex/archived_sessions/**/rollout-*.jsonl
~/.codex/sqlite/state_*.sqlite
~/.codex/state_*.sqlite
```

The current macOS app flow does not touch these thread metadata files.

Backups are written before every save or legacy switch:

```text
~/.codex/backups/
~/.codex/backups_state/provider-sync/
```

Custom mode writes the custom API key into the custom provider, while keeping ChatGPT as the preferred auth method:

```toml
model_provider = "custom"
model_catalog_json = "/Users/you/.codex/codex-switch-model-catalog.json"
preferred_auth_method = "chatgpt"

[model_providers.custom]
base_url = "http://127.0.0.1:17638/v1"
requires_openai_auth = true
wire_api = "responses"
models = ["your-model"]
experimental_bearer_token = "sk-..."
```

The legacy official command switches back to the official provider and removes the custom provider bearer token:

```toml
model_provider = "openai"
preferred_auth_method = "chatgpt"
```

`auth.json` is preserved, so existing ChatGPT login tokens are not discarded.

## Development

Run tests:

```bash
python3 tests/test_codex_switch.py
```

Package the macOS app:

```bash
./scripts/package-app.sh
```

Install from a local checkout:

```bash
./scripts/install.sh
```

## Uninstall

```bash
./scripts/uninstall.sh
```

This removes the installed CLI and app, but does not delete your `~/.codex` settings.

## FAQ

**Why does official mode ask me to log in again?**

It should not ask you to log in again if your current `auth.json` already contains a valid ChatGPT login. Codex Switch preserves that file instead of replacing it with an empty login stub.

**Does this expose my API key?**

The CLI status output redacts API keys. Custom mode also writes the key as `experimental_bearer_token` under the custom provider so Codex can keep using ChatGPT login for official account features.

**Can I use a remote OpenAI-compatible endpoint instead of localhost?**

Yes. Set it in the app settings or run:

```bash
codex-switch config set --local-base-url https://your-endpoint.example.com
```

## License

MIT

---

If this saved you from config-editing gymnastics, 帮我给个 star 把。
