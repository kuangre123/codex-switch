# Pure Passthrough + Reasoning Effort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the adapter honor the "Chat 适配器" toggle (off = pure `/responses` passthrough, never a tool-less chat bridge) and add a reasoning-effort setting (default `xhigh`) written into Codex's config.

**Architecture:** `proxy_response` reads `chat_adapter` once; when off it never calls the chat bridge, never consults/records `_RESPONSES_UNSUPPORTED_ROUTES`, and returns the upstream error. The effort lives in state (`local_reasoning_effort`), is set by `configure --reasoning-effort`, and is written as `model_reasoning_effort` by the config rewriters; the SwiftUI app gets a segmented picker.

**Tech Stack:** Python 3 stdlib (adapter runs on `/usr/bin/python3` 3.9 — keep 3.9 syntax at runtime), unittest/pytest, SwiftUI (macOS 13).

**Spec:** `docs/superpowers/specs/2026-10-10-passthrough-and-reasoning-effort-design.md`

**Test runner (mandatory):** the suite shells out to `launchctl`/`open` and writes `~/Library/LaunchAgents`, so never run it bare on a real machine. Use the sandbox runner, which stubs those commands and points `HOME` at a temp dir:

```bash
"$SCRATCH/safe_pytest.sh" tests/ -k <pattern>
```

(`safe_pytest.sh` lives in the session scratchpad; it runs `HOME=$tmp PATH=$stubs:$PATH python3 -m pytest -q -p no:cacheprovider "$@"`.)

**Commits:** only when the user asks (repo policy for this session). Each task ends with a checkpoint instead.

## File map

- `src/codex_switch/cli.py` — adapter routing (`proxy_response`, `relay_responses_stream`, `passthrough_responses`), effort constants/helper, config rewriters, `configure_codex`, `switch_local`, `config_show`, argparse.
- `tests/test_codex_switch.py` — new adapter + effort tests; one existing test gains `chat_adapter: "true"`.
- `app/CodexSwitchApp.swift` — view-model property, load/save wiring, picker row.

---

### Task 1: Adapter honors the Chat adapter toggle

**Files:** Modify `src/codex_switch/cli.py` (`proxy_response` ~1588, `passthrough_responses` ~1655, `relay_responses_stream` ~1990) · Test `tests/test_codex_switch.py`

- [ ] **Step 1: Write the failing tests** (add to `CodexSwitchTests`, next to `test_stream_failure_reports_upstream_diagnostics`)

```python
    def _proxy_handler_with_state(self, temp: str, **extra_state: str) -> "cli_module.AdapterHandler":
        home = Path(temp) / ".codex"
        home.mkdir(parents=True)
        state = {
            "adapter_upstream_base_url": "https://relay.example",
            "local_api_key": "sk-x",
            "local_upstream_model": "gpt-6.1-sol",
        }
        state.update(extra_state)
        (home / "codex-switch-state.json").write_text(json.dumps(state), encoding="utf-8")
        (home / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-x"}), encoding="utf-8")
        handler = self._make_adapter_handler()
        handler.server = SimpleNamespace(home=home)
        return handler

    def test_passthrough_only_stream_failure_never_bridges_to_chat(self) -> None:
        cli_module._RESPONSES_UNSUPPORTED_ROUTES.clear()
        requested_urls: list[str] = []

        def fake_urlopen(req, timeout=None):
            requested_urls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(b"context too long"))

        request = {"stream": True, "input": [], "store": False, "reasoning": {"effort": "xhigh"}}
        with tempfile.TemporaryDirectory() as temp:
            handler = self._proxy_handler_with_state(temp, chat_adapter="false")
            with mock.patch.object(cli_module.urllib.request, "urlopen", side_effect=fake_urlopen):
                handler.proxy_response(request)

        stream = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("response.failed", stream)
        self.assertIn("context too long", stream)
        # Every sanitize tier was tried, all against /responses — never the chat bridge.
        self.assertEqual(len(requested_urls), 3)
        self.assertTrue(all(url.endswith("/v1/responses") for url in requested_urls), requested_urls)
        # A request-specific rejection must not poison later requests on this route.
        self.assertEqual(cli_module._RESPONSES_UNSUPPORTED_ROUTES, set())

    def test_passthrough_only_keeps_trying_responses_after_a_rejection(self) -> None:
        cli_module._RESPONSES_UNSUPPORTED_ROUTES.clear()
        sse_body = b"event: response.completed\ndata: {}\n\ndata: [DONE]\n\n"
        requested_urls: list[str] = []

        def fake_urlopen(req, timeout=None):
            requested_urls.append(req.full_url)
            if len(requested_urls) == 1:
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b"route flapped"))
            return self._FakeUpstreamResponse(sse_body, "text/event-stream")

        with tempfile.TemporaryDirectory() as temp:
            first = self._proxy_handler_with_state(temp, chat_adapter="false")
            second = self._make_adapter_handler()
            second.server = first.server
            with mock.patch.object(cli_module.urllib.request, "urlopen", side_effect=fake_urlopen):
                first.proxy_response({"stream": True, "input": []})
                second.proxy_response({"stream": True, "input": []})

        self.assertIn("response.failed", first.wfile.getvalue().decode("utf-8"))
        self.assertIn(sse_body.decode("utf-8"), second.wfile.getvalue().decode("utf-8"))
        self.assertEqual(len(requested_urls), 2)
        self.assertTrue(all(url.endswith("/v1/responses") for url in requested_urls), requested_urls)

    def test_passthrough_only_non_stream_failure_returns_502_without_chat(self) -> None:
        cli_module._RESPONSES_UNSUPPORTED_ROUTES.clear()
        requested_urls: list[str] = []

        def fake_urlopen(req, timeout=None):
            requested_urls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b"relay busy"))

        with tempfile.TemporaryDirectory() as temp:
            handler = self._proxy_handler_with_state(temp, chat_adapter="false")
            statuses: list[int] = []
            handler.send_response = statuses.append
            with mock.patch.object(cli_module.urllib.request, "urlopen", side_effect=fake_urlopen):
                handler.proxy_response({"stream": False, "input": []})

        self.assertEqual(statuses, [502])
        body = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertIn("relay busy", body["error"]["message"])
        self.assertTrue(all(url.endswith("/v1/responses") for url in requested_urls), requested_urls)
```

In `test_stream_failure_reports_upstream_diagnostics` (it asserts the chat bridge was tried), opt the state into the bridge:

```python
                json.dumps({
                    "adapter_upstream_base_url": "https://relay.example",
                    "local_api_key": "sk-x",
                    "local_upstream_model": "glm-5.2",
                    "chat_adapter": "true",
                }),
```

- [ ] **Step 2: Run to verify they fail**

Run: `"$SCRATCH/safe_pytest.sh" tests/ -k "passthrough_only or stream_failure_reports"`
Expected: the 3 new tests FAIL (chat bridge URLs requested / 2 entries instead of 502); `test_stream_failure_reports_upstream_diagnostics` PASSES.

- [ ] **Step 3: Implement**

`relay_responses_stream` — new keyword, gate both the skip and the two `add` sites:

```python
    def relay_responses_stream(
        self, request: dict[str, object],
        upstream_base_url: str, upstream_model: str, api_key: str,
        errors: list[str] | None = None,
        remember_unsupported: bool = True,
    ) -> bool:
```
Append to its docstring: ``remember_unsupported`` lets a rejected route go straight to the chat bridge on later requests; pure passthrough turns it off because there is no bridge to skip to.

```python
        route = (upstream_base_url, upstream_model)
        if remember_unsupported and route in _RESPONSES_UNSUPPORTED_ROUTES:
```
```python
                if exc.code in {404, 405}:
                    if remember_unsupported:
                        _RESPONSES_UNSUPPORTED_ROUTES.add(route)
                    return False
```
```python
        # Even the most sanitized payload was rejected: the endpoint/model
        # combination doesn't do Responses, so stop asking.
        if remember_unsupported:
            _RESPONSES_UNSUPPORTED_ROUTES.add(route)
        return False
```

`passthrough_responses` — identical keyword (`remember_unsupported: bool = True` after `errors`) and the same three guards.

`proxy_response` — read the toggle once, pass it, skip the bridge when off:

```python
        if not api_key:
            raise SwitchError("adapter API key is missing")
        # "Chat 适配器" toggle. Off (the default) is pure Responses passthrough:
        # Codex 0.162+ sends its tools inside an `additional_tools` input item
        # the chat bridge can't carry, so a bridged reply would arrive with no
        # tools at all. Returning the upstream error lets Codex retry or show it.
        chat_bridge = effective_setting(state, "chat_adapter", "false") == "true"
        stream = bool(request.get("stream"))
        resp_id = f"resp_{uuid.uuid4().hex}"

        if stream:
            errors: list[str] = []
            if self.relay_responses_stream(
                request, upstream_base_url, upstream_model, api_key, errors,
                remember_unsupported=chat_bridge,
            ):
                return
            if chat_bridge and self.relay_chat_stream(request, upstream_base_url, upstream_model, api_key, resp_id, errors):
                return
```
(rest of the stream branch unchanged)

```python
        errors = []
        passthrough = self.passthrough_responses(
            request, upstream_base_url, upstream_model, api_key, resp_id, errors,
            remember_unsupported=chat_bridge,
        )
        if passthrough is not None:
            self.send_json(200, passthrough)
            return
        if not chat_bridge:
            message = "upstream did not return a usable response"
            if errors:
                message += ": " + " ; ".join(errors)
            self.send_json(502, {"error": {"message": message}})
            return
        payload = responses_request_to_chat_payload(request, upstream_model, False)
```
(rest unchanged)

- [ ] **Step 4: Run to verify they pass**

Run: `"$SCRATCH/safe_pytest.sh" tests/`
Expected: all PASS (72 existing + 3 new).

- [ ] **Step 5: Checkpoint** — `git diff --stat`; commit only if the user asks.

---

### Task 2: Reasoning effort in state and config

**Files:** Modify `src/codex_switch/cli.py` (constants ~line 50, helper after `upstream_model_for_state` ~852, `rewrite_config_for_local` ~642, `rewrite_config_for_parallel` ~676, `switch_local`, `configure_codex` ~2701, `config_show`, argparse ~3211) · Test `tests/test_codex_switch.py`

- [ ] **Step 1: Write the failing tests**

```python
    def test_parallel_config_writes_reasoning_effort_for_custom_default(self) -> None:
        config = cli_module.rewrite_config_for_parallel(
            'model = "gpt-5.5"\n', cli_module.DEFAULT_ADAPTER_BASE_URL, "gpt-5.5",
            "codex-switch/gpt-6.1-sol", "sk-x", None, "custom", False, "xhigh",
        )
        sections = {name: "".join(lines) for name, lines in cli_module.split_toml_sections(config)}
        self.assertIn('model_reasoning_effort = "xhigh"', sections[""])
        self.assertIn('model_reasoning_effort = "xhigh"', sections["[profiles.ccswitch]"])

    def test_parallel_config_official_default_keeps_top_level_reasoning_effort(self) -> None:
        config = cli_module.rewrite_config_for_parallel(
            'model = "gpt-5.5"\nmodel_reasoning_effort = "high"\n', cli_module.DEFAULT_ADAPTER_BASE_URL,
            "gpt-5.5", "codex-switch/gpt-6.1-sol", "sk-x", None, "openai", False, "xhigh",
        )
        sections = {name: "".join(lines) for name, lines in cli_module.split_toml_sections(config)}
        self.assertIn('model_reasoning_effort = "high"', sections[""])
        self.assertNotIn("xhigh", sections[""])
        self.assertIn('model_reasoning_effort = "xhigh"', sections["[profiles.ccswitch]"])

    def test_local_config_writes_reasoning_effort(self) -> None:
        config = cli_module.rewrite_config_for_local(
            'model = "gpt-5.5"\n', cli_module.DEFAULT_ADAPTER_BASE_URL, "codex-switch/gpt-6.1-sol", "sk-x", None, "xhigh",
        )
        sections = {name: "".join(lines) for name, lines in cli_module.split_toml_sections(config)}
        self.assertIn('model_reasoning_effort = "xhigh"', sections[""])

    def test_configure_saves_reasoning_effort_defaulting_to_xhigh(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "auth.json").write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-test"}), encoding="utf-8")
            base_args = ["configure", "--base-url", "https://relay.example", "--custom-model", "gpt-6.1-sol", "--default-provider", "custom"]
            parser = cli_module.build_parser()
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(home)}), \
                    mock.patch.object(cli_module, "start_adapter_launch_agent", return_value=home / "adapter.plist"), \
                    mock.patch.object(cli_module, "apply_proxy_for_provider"), \
                    mock.patch.object(cli_module, "repair_legacy_message_ids_and_report"), \
                    redirect_stdout(io.StringIO()):
                args = parser.parse_args(base_args)
                self.assertEqual(args.func(args), 0)
                default_state = read_state(home)
                default_top = dict(cli_module.split_toml_sections((home / "config.toml").read_text(encoding="utf-8")))[""]
                args = parser.parse_args(base_args + ["--reasoning-effort", "high"])
                self.assertEqual(args.func(args), 0)

            self.assertEqual(default_state["local_reasoning_effort"], "xhigh")
            self.assertIn('model_reasoning_effort = "xhigh"\n', default_top)
            self.assertEqual(read_state(home)["local_reasoning_effort"], "high")
            top = dict(cli_module.split_toml_sections((home / "config.toml").read_text(encoding="utf-8")))[""]
            self.assertIn('model_reasoning_effort = "high"\n', top)
```

- [ ] **Step 2: Run to verify they fail**

Run: `"$SCRATCH/safe_pytest.sh" tests/ -k "reasoning_effort"`
Expected: FAIL (`TypeError: ... takes ... positional arguments` / `unrecognized arguments: --reasoning-effort`).

- [ ] **Step 3: Implement**

Constants (after `NO_PROXY_ENV_KEYS`):

```python
REASONING_EFFORTS = ("low", "medium", "high", "xhigh")
DEFAULT_REASONING_EFFORT = "xhigh"
```

Helper (after `upstream_model_for_state`):

```python
def reasoning_effort_for_state(state: dict[str, object]) -> str:
    effort = effective_setting(state, "local_reasoning_effort", DEFAULT_REASONING_EFFORT)
    return effort if effort in REASONING_EFFORTS else DEFAULT_REASONING_EFFORT
```

`rewrite_config_for_local` — signature gains `reasoning_effort: str | None = None`; inside the top-level branch after `preferred_auth_method`:

```python
            if reasoning_effort:
                lines = set_key(lines, "model_reasoning_effort", reasoning_effort)
```

`rewrite_config_for_parallel` — signature gains `reasoning_effort: str | None = None` (last); top-level branch after `preferred_auth_method`:

```python
            # The effort setting belongs to the custom route; an official
            # default keeps whatever effort the user chose for it.
            if reasoning_effort and default_provider == CUSTOM_PROVIDER_ID:
                lines = set_key(lines, "model_reasoning_effort", reasoning_effort)
```
and the ccswitch profile:

```python
    ccswitch_profile = {"model_provider": CUSTOM_PROVIDER_ID, "model": custom_model}
    if reasoning_effort:
        ccswitch_profile["model_reasoning_effort"] = reasoning_effort
    rewritten = ensure_profile(rewritten, "ccswitch", ccswitch_profile)
```

`switch_local` — pass the saved effort:

```python
    config = rewrite_config_for_local(
        read_config(config_path), base_url, model, api_key, None, reasoning_effort_for_state(state)
    )
```

`configure_codex` — after the `default_provider` validation:

```python
    reasoning_effort = getattr(args, "reasoning_effort", None) or reasoning_effort_for_state(state)
```
next to `state["skip_login"] = ...`:

```python
    state["local_reasoning_effort"] = reasoning_effort
```
the rewrite call gains the argument (`default_provider, skip_login, reasoning_effort,`) and the summary prints `print(f"reasoning_effort: {reasoning_effort}")` after `custom_model_name`.

`config_show` — after the `chat_adapter` line:

```python
    print(f"local_reasoning_effort: {reasoning_effort_for_state(state)}")
```

argparse (after `--chat-adapter`):

```python
    configure.add_argument("--reasoning-effort", choices=REASONING_EFFORTS, help=f"Default reasoning effort for the custom route. Default: saved setting or {DEFAULT_REASONING_EFFORT}")
```

- [ ] **Step 4: Run to verify they pass**

Run: `"$SCRATCH/safe_pytest.sh" tests/`
Expected: all PASS.

- [ ] **Step 5: Checkpoint** — `git diff --stat`; commit only if the user asks.

---

### Task 3: App picker

**Files:** Modify `app/CodexSwitchApp.swift` (~115, ~164, ~243/256, ~300/311, ~756)

- [ ] **Step 1: Wire the property** — after `@Published var skipLogin = false`:

```swift
    @Published var reasoningEffort = "xhigh"
```
after `self.skipLogin = (values["skip_login"] ?? "false") == "true"`:

```swift
                self.reasoningEffort = values["local_reasoning_effort"] ?? "xhigh"
```

- [ ] **Step 2: Pass it on save** — in `switchProvider`, after `let doSkipLogin = skipLogin`: `let effort = reasoningEffort`; in the `configureCodex(` call add `reasoningEffort: effort,` after `skipLogin: doSkipLogin,`. Signature becomes `... skipLogin: Bool, reasoningEffort: String, replacementKey: String)` and the argument array gains `"--reasoning-effort", reasoningEffort,` after `"--default-provider", defaultProvider,`.

- [ ] **Step 3: Add the row** — after the "Chat 适配器" `settingRow`:

```swift
                            settingRow(texts.text("推理强度", "Reasoning Effort")) {
                                Picker("", selection: $model.reasoningEffort) {
                                    ForEach(["low", "medium", "high", "xhigh"], id: \.self) { effort in
                                        Text(effort).tag(effort)
                                    }
                                }
                                .pickerStyle(.segmented)
                                .labelsHidden()
                            }
```

- [ ] **Step 4: Type-check**

Run: `xcrun swiftc -parse-as-library -typecheck -target arm64-apple-macos13.0 -sdk "$(xcrun --sdk macosx --show-sdk-path)" app/CodexSwitchApp.swift`
Expected: no output, exit 0.

- [ ] **Step 5: Checkpoint.**

---

### Task 4: Verification

- [ ] Full suite: `"$SCRATCH/safe_pytest.sh" tests/` → all pass; real `~/Library/LaunchAgents` and `launchctl getenv` unchanged (diff before/after).
- [ ] Adapter runtime: `/usr/bin/python3 -c "import sys; sys.path.insert(0, 'src'); import codex_switch.cli"` → exit 0 (3.9 import).
- [ ] Integration smoke: run the repo adapter on a spare port with a temp `CODEX_HOME` whose upstream is a local stub that 503s `/v1/responses` and records `/v1/chat/completions` hits; toggle off → `response.failed`, zero chat hits; toggle on → chat hit happens.

### Task 5: Deploy (ask the user first)

- [ ] Build with `scripts/package-app.sh`, install over `/Applications/Codex Switch.app`, then "保存配置并重启 Codex" in the app (that copies the new `cli.py` to `~/.codex/codex-switch-adapter.py` and restarts the adapter).
