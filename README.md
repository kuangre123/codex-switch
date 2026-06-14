# Codex Switch

> One tiny macOS app for switching Codex between Official OpenAI login and a custom OpenAI-compatible API endpoint.

Codex Switch is a lightweight helper for people who bounce between the official Codex login and a custom API route. It gives you both a terminal command and a double-click macOS app, keeps backups before every switch, and avoids storing stale OAuth refresh tokens.

中文：Codex Switch 是一个很小的 macOS 工具，用来在 Codex 的官方 OpenAI 登录模式和自定义 API 模式之间快速切换。它同时提供命令行和双击可用的 macOS App，每次切换都会备份配置，并避免恢复过期 OAuth refresh token。

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
- CLI for scripting and quick terminal use.
- Configurable custom API endpoint.
- Default custom API endpoint: `https://jp.icodeeasy.cc`.
- Automatic backups under `~/.codex/backups`.
- No stale OAuth token restore. Official mode resets auth to ChatGPT mode and lets Codex perform a fresh login when needed.
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

切换后建议重启 Codex App，让界面刷新到新的 provider/model。

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

## What It Changes

Codex Switch edits only the user Codex files:

```text
~/.codex/auth.json
~/.codex/config.toml
~/.codex/codex-switch-state.json
```

Backups are written before every switch:

```text
~/.codex/backups/
```

Custom mode writes:

```toml
model_provider = "custom"
preferred_auth_method = "apikey"

[model_providers.custom]
base_url = "https://jp.icodeeasy.cc"
requires_openai_auth = true
wire_api = "responses"
```

Official mode writes:

```toml
model_provider = "openai"
preferred_auth_method = "chatgpt"
```

and resets `auth.json` to:

```json
{
  "auth_mode": "chatgpt",
  "OPENAI_API_KEY": null
}
```

This intentionally does not restore old OAuth tokens, because refresh tokens can be single-use and restoring them can break login.

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

Because Codex Switch does not restore old OAuth refresh tokens. That is deliberate. A stale refresh token can cause errors like “refresh token was already used.”

**Does this expose my API key?**

The CLI status output redacts API keys. The key is stored in `~/.codex/auth.json`, same as Codex API-key login.

**Can I use a remote OpenAI-compatible endpoint instead of localhost?**

Yes. Set it in the app settings or run:

```bash
codex-switch config set --local-base-url https://your-endpoint.example.com
```

## License

MIT

---

If this saved you from config-editing gymnastics, 帮我给个 star 把。
