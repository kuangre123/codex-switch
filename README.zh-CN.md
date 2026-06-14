# Codex Switch

> 一个很小的 macOS 工具，用来一键切换 Codex 官方登录和自定义 API。

App 带有一个原创的 Codex Switch 图标：保留一点 Codex 风格的环形科技感，但做成了变形的切换标识。

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

安装完成后会自动打开 App。如果只想安装不打开：

```bash
./scripts/install.sh --no-open
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

首次打开时，如果还没有配置自定义 API Key，App 会自动引导你填写：

- 自定义 API 地址，默认 `https://jp.icodeeasy.cc`
- 模型名称，默认 `gpt-5.5`
- API Key

填写后会保存配置，并直接切换到自定义 API 模式。

命令行：

```bash
codex-switch status
codex-switch local
codex-switch official
```

会话列表保护/恢复：

```bash
codex-switch sessions snapshot
codex-switch sessions rebuild-index
codex-switch sessions list
codex-switch sessions recent --limit 10
codex-switch sessions promote --id <session-id>
```

修改默认 API 地址：

```bash
codex-switch config set --local-base-url https://your-endpoint.example.com
```

或者在 App 里点 **Settings** 修改。

App 主界面是中文的，包含 **切换模式**、**查看状态**、**会话工具**、**检查更新**、**设置**。其中 **会话工具** 可以备份会话列表状态、重建会话索引、查看最近会话。切换模式成功后，App 会询问是否选择要继续的对话；可以从最近 10 条里用复选框勾选多个对话，选中的对话会被排到 Codex 最近会话列表，方便刷新后继续打开。

注意：这个工具不会修改会话正文，也不会直接强制 Codex Desktop 打开某个线程；它调整的是 `session_index.jsonl` 的最近顺序。

## 安全说明

每次切换前都会备份：

```text
~/.codex/backups/
```

切换模式时还会自动备份轻量会话列表文件：

```text
~/.codex/session_index.jsonl
~/.codex/.codex-global-state.json
```

不会默认复制整个 `sessions/` 目录，因为历史会话可能很大。真实会话文件仍留在 `~/.codex/sessions` 和 `~/.codex/archived_sessions`，需要时可以从它们重建索引。

## 会不会丢 sessions？

换官方/中转站通常不会删除真实会话文件。更常见的问题是 Codex Desktop 在账号、provider 或模型切换后，会话列表索引 `session_index.jsonl` 或界面状态没对上，所以看起来像“丢了”。

如果列表不见了，可以先执行：

```bash
codex-switch sessions rebuild-index
```

它会扫描本地 session JSONL 文件，重新生成 `~/.codex/session_index.jsonl`，并尽量保留已有标题。执行后重启 Codex App 让列表刷新。

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
