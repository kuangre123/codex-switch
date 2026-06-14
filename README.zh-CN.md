# Codex Switch

> 一个很小的 macOS 工具，用来一键切换 Codex 官方登录和自定义 API。

Codex Switch 适合经常在两种模式之间切换的人：

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

官方模式不会恢复旧 OAuth token，因为 refresh token 可能是一次性轮换的，恢复旧 token 会导致登录刷新失败。

## 开发

```bash
python3 tests/test_codex_switch.py
./scripts/package-app.sh
```

## License

MIT

---

如果这个工具帮你少改几次配置，帮我给个 star 把。
