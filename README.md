# Codex Switch

> 一个小巧的 macOS 工具：一键把 Codex 在「官方 OpenAI」和「你自己的自定义 / 第三方 API」之间切换，**切换后对话记录始终都在**。
>
> A tiny macOS app to switch Codex between Official OpenAI and your own custom / third‑party API — **without ever losing your conversations**.

Codex Switch 把原本要手改 `~/.codex/auth.json` 和 `~/.codex/config.toml` 的事变成一次点击：官方 OpenAI 和你的自定义 provider 同时保留在配置里，切换只改「默认走哪一路」，**不碰任何对话数据**。它内置了国内主流大模型的快速预设，并能在本地启动一个适配器，把只支持 Chat Completions 的接口自动桥接成 Codex 需要的 Responses 协议。

It turns hand‑editing `~/.codex/auth.json` and `config.toml` into one save: Official OpenAI and your custom provider both stay configured, switching only changes the default route, and your saved conversations are never touched. It ships quick presets for popular Chinese LLM providers and can run a local adapter that bridges Chat‑Completions‑only APIs to the Responses protocol Codex speaks.

> ⚠️ 不要在对话里让 Codex 自己改接入方式，容易改坏。切换请用本工具，稳定得多。
> Don't ask Codex itself to edit its provider config in chat — use this app to switch, it's far more reliable.

<p align="center">
  <img src="assets/screenshot.png" alt="Codex Switch" width="600">
</p>

## 下载 / Download

从 [GitHub Releases](https://github.com/kuangre123/codex-switch/releases/latest) 下载，**推荐 DMG**：

```text
Codex-Switch-vX.Y.Z.dmg        # 推荐：已签名 + 公证，双击即开
Codex-Switch-vX.Y.Z-macOS.zip
```

应用已用 **Developer ID 签名并通过 Apple 公证（notarized）**，也是 **通用二进制（Intel + Apple 芯片）**。打开 DMG 后把 `Codex Switch.app` 拖进「应用程序」即可，**不会再有"未受信任的开发者"提示**。

The app is **signed with a Developer ID and notarized by Apple**, and is a **universal binary (Intel + Apple Silicon)**. Open the DMG, drag `Codex Switch.app` into Applications — no Gatekeeper warning.

## 功能 / Features

- **官方 / 自定义并行**：两套配置都留在 `config.toml`，在 App 里选「API 提供方」再保存即可切换（桌面端和 CLI 通用）。
- **对话永不丢**：切换只改写 `config.toml` 和 `auth.json`，**绝不触碰会话数据库或 rollout**，所以官方 / 自定义来回切，历史对话都还在。
- **国内大模型快速预设**：DeepSeek、Kimi、智谱 GLM、通义千问、豆包（火山引擎）、百度文心、MiniMax、阶跃星辰 StepFun，以及「第三方 / 中转 API（手动填写）」——选完自动填好接入点和模型，只需粘贴 API Key。
- **自定义 / 第三方供应商卡片**：支持任意 OpenAI 兼容的第三方 / 中转 API，填接入点 + 模型 ID + Key 即可。
- **Chat 适配器**：接口只支持 `/chat/completions`（如 DeepSeek/Kimi/千问等）时，在本地启动一个代理，自动把 Codex 的 Responses 请求转成 Chat Completions；原生支持 `/responses` 的接口则直连。
- **保存时智能探测**：保存前先用你的 Key 试探接入点（先 `/responses` 再 `/chat/completions`），都不通就当场报错「请检查设置」，不会留下一个用不了的会话。
- **跳过登录**：可选绕过 ChatGPT OAuth，用 API‑Key 模式。
- **CLI 通用**：同时写入 `[profiles.ccswitch]`（自定义）和 `[profiles.official]`（官方），终端里 `codex` / `codex-official` 直接用。
- 自动备份到 `~/.codex/backups`；工具栏自动检查 GitHub 新版本；保存后自动重启 Codex 让配置生效。

## 切换原理 / How switching works

借鉴 [cc-switch](https://github.com/farion1231/cc-switch) 的思路：**provider 切换工具只应写实时配置文件，绝不动会话数据**。

- 选「自定义 API」→ 顶层 `model_provider = "custom"`，Codex 走你的自定义 provider。
- 选「官方 OpenAI」→ 顶层 `model_provider = "openai"`，Codex 走官方，用其内置模型目录（官方模型列表完整）。
- 两个 provider 段和 CLI profiles 始终保留；切换只改顶层默认 + auth。
- **不写自定义模型目录（model_catalog_json）** —— 早期版本写过，但它会替换 Codex 内置目录、还容易让对话列表加载失败，已彻底移除。

> 注：桌面端的模型选择器由 Codex 自己（后端 / 内置）驱动；自定义 provider 的模型在选择器里显示为"自定义"标签，这是 Codex 的限制。实际请求按所选 provider 正确路由，CLI 里可完全控制模型 ID。

## CLI

```bash
# 状态
codex-switch status

# 并行配置官方 + 自定义，默认走自定义，保存时探测验证
codex-switch configure \
  --base-url https://api.deepseek.com/v1 \
  --custom-model deepseek-chat \
  --custom-model-name "DeepSeek" \
  --official-model gpt-5.5 \
  --default-provider custom \
  --chat-adapter \
  --probe \
  --restart-codex

# 终端里：codex 走自定义（profile ccswitch），codex-official 走官方
```

`--custom-model` 是发给你接口的真实上游模型 ID。`--chat-adapter` 会在 `127.0.0.1:17638` 起一个本地服务，把 Responses 桥接成 Chat Completions。`--probe` 在保存前验证接入点是否可用。

## 会改动哪些文件 / What it changes

```text
~/.codex/auth.json                  # 凭证 / 登录模式
~/.codex/config.toml                # provider、默认模型、CLI profiles
~/.codex/codex-switch-state.json    # 工具自己的设置
~/.codex/codex-switch-adapter.py    # Chat 适配器脚本（稳定副本）
```

**不会**触碰 `~/.codex/sessions`、rollout 文件或会话数据库 —— 你的对话安全。

## 从源码构建 / Build from source

```bash
git clone https://github.com/kuangre123/codex-switch.git
cd codex-switch
bash scripts/build-release-dmg.sh   # 需要完整 Xcode；有 Developer ID + ASC API key 时自动签名公证
```

没有签名证书时会自动退回 ad‑hoc 签名（仅本机可用），不影响开发构建。

## 作者 / Author

狂热AI（X：[@CrazyAIAgent](https://x.com/CrazyAIAgent)）
