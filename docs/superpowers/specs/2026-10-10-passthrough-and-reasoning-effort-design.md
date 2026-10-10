# Pure passthrough + reasoning-effort setting — design

Date: 2026-10-10 · Status: approved (option A for effort)

## Problem

Codex Desktop (CLI 0.162) intermittently, then permanently, answered "no terminal or
file tools" on the custom provider.

- Codex 0.162 no longer sends a top-level `tools` (or `instructions`). Tools travel in
  `input[0]` = `{"type": "additional_tools", "tools": [namespace…]}`; the shell/file
  capability is the `custom` tool `exec` inside the `functions` namespace.
- The adapter streams via native `/responses` passthrough first. On any failure it falls
  back to the Chat Completions bridge, which only reads top-level `function` tools,
  skips unknown input items and drops `reasoning` → the model receives zero tools and
  the relay logs its default effort (`medium`).
- Once a request exhausts the sanitize tiers (or gets 404/405), the route is cached in
  `_RESPONSES_UNSUPPORTED_ROUTES` for the adapter's lifetime, so every later request is
  bridged until the adapter restarts.
- The `chat_adapter` state ("Chat 适配器" toggle) is saved but never read at runtime.

Evidence: rollout token stats (tool-less replies 18–30K input tokens, 0 cached, vs
37–141K cached); the same probe was bridged by the long-running adapter but passed
through by a restarted one; a captured Codex 0.162 request shows `additional_tools`.

## Change 1 — honor the Chat adapter toggle

`chat_adapter == "false"` (toggle off, the default) means pure Responses passthrough:

- Stream: always attempt `/responses` (never consult or record
  `_RESPONSES_UNSUPPORTED_ROUTES`). On failure send the existing `response.failed` SSE
  with the collected upstream errors. Never call the chat bridge.
- Non-stream: same; on failure answer HTTP 502 with the upstream errors. Never convert.
- Codex then retries or shows the real upstream error instead of a tool-less answer.

`chat_adapter == "true"`: behaviour unchanged (passthrough first, chat bridge fallback,
sticky route verdict).

Mechanism: a `remember_unsupported` keyword (default `True`) on
`relay_responses_stream` / `passthrough_responses`; `proxy_response` reads the toggle
once and passes it, and skips the bridge when the toggle is off.

## Change 2 — reasoning-effort setting (option A: a default, not a force)

- State key `local_reasoning_effort`; values `low|medium|high|xhigh`; default `xhigh`.
- `configure --reasoning-effort <value>` stores it; when omitted, the saved value (or
  the `xhigh` default) is used. `config show` (which the app reads) and the
  `configure` summary print it.
- When the custom provider is the default (`configure --default-provider custom` and the
  `local` command), write top-level `model_reasoning_effort`; always write it into
  `[profiles.ccswitch]`. Official-default saves leave the top-level key untouched.
- Codex sends this value as `reasoning.effort` (verified by request capture). Per-thread
  effort chosen in Codex still overrides it; existing threads keep their own setting.
- App: a "推理强度 / Reasoning Effort" picker next to "Chat 适配器" in the custom section,
  default xhigh, passed as `--reasoning-effort`.

Out of scope: chat-bridge support for `additional_tools`/namespaces/custom tools;
forcing effort in the adapter; background tasks' own effort (memory consolidation
sends `medium`); official-mode effort.

## Testing

- Toggle off: stream failure → `response.failed`, no `/chat/completions` call, route
  not recorded, next request still tries `/responses`; non-stream failure → 502, no
  chat call.
- Toggle on: existing fallback diagnostics test keeps passing (state sets the toggle).
- Effort: configure writes top-level (custom default) + profile + state, defaults to
  xhigh; official default leaves top-level alone; `local` applies the saved value.
- Full suite on the dev Python; `py_compile` on `/usr/bin/python3` (3.9, the adapter's
  runtime); Swift type-check of the app.
