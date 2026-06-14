# Codex Switch

> One tiny macOS app for switching Codex between Official OpenAI login and a custom OpenAI-compatible API endpoint, while keeping your current conversation context.

Codex Switch is a lightweight helper for people who bounce between the official Codex login and a custom API route. It can switch providers and keep the same Codex thread context, so you can continue the current conversation after moving between Official OpenAI and a custom API. It gives you both a terminal command and a double-click macOS app, keeps backups before every switch, and preserves your official ChatGPT login while routing custom model calls through a relay API key.

中文：Codex Switch 是一个很小的 macOS 工具，用来在 Codex 的官方 OpenAI 登录模式和自定义 API 模式之间快速切换。切换后会尽量保留当前对话上下文，让你继续原来的 Codex 会话。它同时提供命令行和双击可用的 macOS App，每次切换都会备份配置，并保留官方 ChatGPT 登录态。

## Download App

Download the latest macOS app from [GitHub Releases](https://github.com/kuangre123/codex-switch/releases/latest):

```text
Codex-Switch-macOS.zip
```

Unzip it, move `Codex Switch.app` to `~/Applications` or `/Applications`, then open it. On first launch, macOS may ask you to right-click and choose **Open** because the app is unsigned.

中文：你可以直接在 [GitHub Releases](https://github.com/kuangre123/codex-switch/releases/latest) 下载 `Codex-Switch-macOS.zip`。解压后把 `Codex Switch.app` 放进 `~/Applications` 或 `/Applications`，首次打开如果 macOS 提示不明开发者，右键选择 **打开**。

## Why

Codex users often need two modes:

- **Official OpenAI**: use ChatGPT/OpenAI login, provider `openai`.
- **Custom API**: use an API key and route model calls to an OpenAI-compatible endpoint, provider `custom`.

Doing that by hand means editing `~/.codex/auth.json` and `~/.codex/config.toml` over and over. Codex Switch turns that into one click.

中文：手动切换需要反复改 `~/.codex/auth.json` 和 `~/.codex/config.toml`。这个工具把它变成一次点击。

## Features

- One-click macOS app: switch, status, settings.
- Continue the same conversation after switching between Official OpenAI and a custom API.
- CLI for scripting and quick terminal use.
- Configurable custom API endpoint.
- Default custom API endpoint: `https://jp.icodeeasy.cc`.
- Automatic backups under `~/.codex/backups`.
- Preserves existing ChatGPT login tokens while custom mode uses a provider-level bearer token.
- Provider Sync updates existing Codex thread metadata in place, so the current conversation can continue on the selected provider without forking into a new thread.
- The macOS app automatically restarts Codex after switching so the running desktop app reloads the selected provider.
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

- **Switch**: choose `Local custom` or `Official OpenAI`.
- **Status**: show current Codex auth/provider/model.
- **Settings**: edit custom API base URL, custom model, and official model.

When switching from the macOS app, existing Codex thread metadata is provider-synced in place, then Codex.app is gracefully quit and reopened so the running session reloads the selected provider. The thread id and conversation history stay in place, so you can continue the original conversation after the switch.

### CLI

```bash
codex-switch status
codex-switch local
codex-switch official
```

Set custom defaults:

```bash
codex-switch config set \
  --local-base-url https://jp.icodeeasy.cc \
  --local-model gpt-5.5 \
  --official-model gpt-5.2-codex
```

Temporarily override the custom API endpoint:

```bash
codex-switch local --base-url https://your-endpoint.example.com --model your-model
```

Prompt for and save an API key:

```bash
codex-switch local-login
```

Sync existing thread metadata without using the app:

```bash
codex-switch local --migrate-latest
codex-switch official --migrate-latest
```

The macOS app also passes `--restart-codex` so Codex Desktop reloads the selected provider immediately after Provider Sync.

## What It Changes

Codex Switch edits only the user Codex files:

```text
~/.codex/auth.json
~/.codex/config.toml
~/.codex/codex-switch-state.json
```

When Provider Sync is enabled by the app or `--migrate-latest`, it also updates existing Codex Desktop thread metadata:

```text
~/.codex/sessions/**/rollout-*.jsonl
~/.codex/archived_sessions/**/rollout-*.jsonl
~/.codex/sqlite/state_*.sqlite
~/.codex/state_*.sqlite
```

This is what keeps the current conversation context attached to the newly selected provider.

Backups are written before every switch:

```text
~/.codex/backups/
~/.codex/backups_state/provider-sync/
```

Custom mode writes the custom API key into the custom provider, while keeping ChatGPT as the preferred auth method:

```toml
model_provider = "custom"
preferred_auth_method = "chatgpt"

[model_providers.custom]
base_url = "https://jp.icodeeasy.cc"
requires_openai_auth = true
wire_api = "responses"
experimental_bearer_token = "sk-..."
```

Official mode switches back to the official provider and removes the custom provider bearer token:

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
