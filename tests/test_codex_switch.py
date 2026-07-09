import json
import io
import os
import stat
import subprocess
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "codex_switch" / "cli.py"
sys.path.insert(0, str(ROOT / "src"))

from codex_switch import cli as cli_module


def run_tool(home: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        text=True,
        input=input_text,
        capture_output=True,
        check=False,
    )


def run_tool_with_env(env_updates: dict[str, str], *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(env_updates)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        text=True,
        input=input_text,
        capture_output=True,
        check=False,
    )


def run_claude_tool(home: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return run_tool_with_env({"CLAUDE_CONFIG_DIR": str(home)}, *args, input_text=input_text)


def write_sample_config(home: Path) -> None:
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        """model_provider = "openai"
model = "gpt-5"
approval_policy = "on-request"
preferred_auth_method = "chatgpt"

[model_providers.custom]
base_url = "http://127.0.0.1:9999/v1"
name = "custom"
requires_openai_auth = false
wire_api = "chat"

[projects."/Users/sirchen/Documents/aimashi"]
trust_level = "trusted"
""",
        encoding="utf-8",
    )
    os.chmod(home / "config.toml", stat.S_IRUSR | stat.S_IWUSR)


def write_local_config(home: Path) -> None:
    write_sample_config(home)
    text = (home / "config.toml").read_text(encoding="utf-8")
    text = text.replace('model_provider = "openai"', 'model_provider = "custom"', 1)
    text = text.replace('model = "gpt-5"', 'model = "gpt-5.5"', 1)
    text = text.replace('wire_api = "chat"', 'wire_api = "responses"\nexperimental_bearer_token = "sk-test-secret"', 1)
    (home / "config.toml").write_text(text, encoding="utf-8")


def read_state(home: Path) -> dict:
    return json.loads((home / "codex-switch-state.json").read_text(encoding="utf-8"))


def write_thread_database(home: Path) -> None:
    database = home / "sqlite" / "state_5.sqlite"
    database.parent.mkdir(parents=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                model_provider TEXT NOT NULL,
                model TEXT NOT NULL,
                cwd TEXT,
                updated_at INTEGER NOT NULL,
                updated_at_ms INTEGER,
                has_user_event INTEGER NOT NULL,
                archived INTEGER NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("older-official", "openai", "gpt-5.5", None, 10, 10000, 0, 0),
                ("current-custom", "custom", "codex-switch/gpt-5.5", None, 20, 20000, 0, 0),
                ("latest-official", "openai", "gpt-5.5", None, 30, 30000, 0, 0),
                ("archived-official", "openai", "gpt-5.5", None, 40, 40000, 0, 1),
            ],
        )
        connection.commit()


def write_rollout(home: Path, thread_id: str, provider: str, user_event: bool = False, model: str | None = None) -> Path:
    path = home / "sessions" / "2026" / "06" / "15" / f"rollout-test-{thread_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if model is None:
        model = "codex-switch/gpt-5.5" if provider == "custom" else "gpt-5.5"
    path.write_text(
        json.dumps(
            {
                "timestamp": "2026-06-15T00:00:00Z",
                "type": "session_meta",
                "payload": {"id": thread_id, "cwd": "/tmp/example", "model_provider": provider, "model": model},
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    if user_event:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": "2026-06-15T00:00:01Z",
                        "type": "user_message",
                        "payload": {"text": "hello"},
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
    return path


class CodexSwitchTests(unittest.TestCase):
    def test_local_switch_preserves_unrelated_config_and_stores_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None}),
                encoding="utf-8",
            )

            result = run_tool(
                home,
                "local",
                "--api-key",
                "sk-test-secret",
                "--base-url",
                "http://127.0.0.1:15721/v1",
                "--model",
                "gpt-5.5",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("sk-test-secret", result.stdout)
            self.assertEqual(
                json.loads((home / "auth.json").read_text(encoding="utf-8")),
                {"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-test-secret"},
            )
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('model_provider = "custom"', config)
            self.assertIn('model = "gpt-5.5"', config)
            self.assertIn('preferred_auth_method = "chatgpt"', config)
            self.assertIn('base_url = "http://127.0.0.1:15721/v1"', config)
            self.assertIn('requires_openai_auth = false', config)
            self.assertIn('wire_api = "responses"', config)
            self.assertIn('experimental_bearer_token = "sk-test-secret"', config)
            self.assertIn('models = ["gpt-5.5"]', config)
            self.assertIn('[projects."/Users/sirchen/Documents/aimashi"]', config)
            # No custom model catalog is written (cc-switch style): rely on
            # Codex's built-in catalog so the model list and conversations stay intact.
            self.assertNotIn('model_catalog_json', config)
            self.assertFalse((home / "codex-switch-model-catalog.json").exists())
            self.assertTrue((home / "backups").is_dir())
            self.assertTrue(any(p.name.startswith("config.toml.") for p in (home / "backups").iterdir()))
            self.assertEqual(stat.S_IMODE((home / "auth.json").stat().st_mode), 0o600)

    def test_official_switch_uses_chatgpt_auth_and_keeps_config_route(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_local_config(home)
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-test-secret"}),
                encoding="utf-8",
            )

            result = run_tool(home, "official")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads((home / "auth.json").read_text(encoding="utf-8")),
                {"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-test-secret"},
            )
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('model_provider = "openai"', config)
            self.assertIn('model = "gpt-5.5"', config)
            self.assertIn('preferred_auth_method = "chatgpt"', config)
            self.assertIn('base_url = "http://127.0.0.1:9999/v1"', config)
            self.assertNotIn("model_catalog_json", config)
            self.assertNotIn("experimental_bearer_token", config)
            self.assertNotIn("sk-test-secret", result.stdout)
            self.assertEqual(read_state(home)["local_api_key"], "sk-test-secret")

    def test_local_switch_reuses_cached_api_key_after_official_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "codex-switch-state.json").write_text(
                json.dumps({
                    "local_api_key": "sk-cached-secret",
                    "local_base_url": "http://127.0.0.1:18888/v1",
                    "local_model": "local-model",
                }),
                encoding="utf-8",
            )
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None}),
                encoding="utf-8",
            )

            result = run_tool(home, "local")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads((home / "auth.json").read_text(encoding="utf-8")),
                {"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-cached-secret"},
            )
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('base_url = "http://127.0.0.1:18888/v1"', config)
            self.assertIn('model = "local-model"', config)
            self.assertIn('models = ["local-model"]', config)
            self.assertIn('experimental_bearer_token = "sk-cached-secret"', config)
            self.assertNotIn("sk-cached-secret", result.stdout)

    def test_local_switch_can_replace_api_key_from_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-old-secret"}),
                encoding="utf-8",
            )

            result = run_tool(home, "local", "--api-key-stdin", input_text="sk-new-secret\n")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("sk-new-secret", result.stdout)
            self.assertEqual(
                json.loads((home / "auth.json").read_text(encoding="utf-8"))["OPENAI_API_KEY"],
                "sk-new-secret",
            )
            self.assertEqual(read_state(home)["local_api_key"], "sk-new-secret")
            self.assertIn(
                'experimental_bearer_token = "sk-new-secret"',
                (home / "config.toml").read_text(encoding="utf-8"),
            )

    def test_register_model_saves_setting_without_writing_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir()

            result = run_tool(home, "register-model", "my-custom-model", "--name", "我的模型")

            self.assertEqual(result.returncode, 0, result.stderr)
            # No custom catalog is written; only the saved setting changes.
            self.assertFalse((home / "codex-switch-model-catalog.json").exists())
            self.assertEqual(read_state(home)["local_model"], "my-custom-model")
            self.assertEqual(read_state(home)["local_model_display_name"], "我的模型")

    def test_configure_codex_writes_cli_profiles_and_superset_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None}),
                encoding="utf-8",
            )
            (home / "models_cache.json").write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "gpt-official",
                                "display_name": "GPT Official",
                                "shell_type": "shell_command",
                                "model_messages": {"instructions_template": "x"},
                                "upgrade": {"model": "gpt-next"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = run_tool(
                home,
                "configure",
                "--api-key",
                "sk-test-secret",
                "--base-url",
                "https://custom.example/v1",
                "--custom-model",
                "vendor/custom-model",
                "--custom-model-name",
                "我的模型",
                "--official-model",
                "gpt-official",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            config = (home / "config.toml").read_text(encoding="utf-8")
            # Top-level (desktop default) is official.
            self.assertIn('model_provider = "openai"', config)
            self.assertIn('model = "gpt-official"', config)
            # The custom provider always routes through the local adapter; the real
            # upstream URL is stored separately in state.
            self.assertIn('base_url = "http://127.0.0.1:17638/v1"', config)
            self.assertEqual(read_state(home)["adapter_upstream_base_url"], "https://custom.example/v1")
            self.assertIn('models = ["vendor/custom-model"]', config)
            # Official default mode must use Codex's built-in catalog so the
            # full official model list is preserved.
            self.assertNotIn('model_catalog_json = "', config)
            self.assertFalse((home / "codex-switch-model-catalog.json").exists())
            self.assertIn("model_catalog_json: (built-in)", result.stdout)
            # CLI parity: both routes are available as Codex profiles regardless
            # of which provider is the desktop default.
            self.assertIn('[profiles.ccswitch]', config)
            self.assertIn('[profiles.official]', config)
            ccswitch = config.split('[profiles.ccswitch]', 1)[1]
            self.assertIn('model_provider = "custom"', ccswitch)
            self.assertIn('model = "vendor/custom-model"', ccswitch)
            official = config.split('[profiles.official]', 1)[1]
            self.assertIn('model_provider = "openai"', official)
            self.assertIn('model = "gpt-official"', official)
            self.assertNotIn("Provider-synced", result.stdout)

    def test_configure_codex_can_select_custom_provider_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-test-secret"}),
                encoding="utf-8",
            )
            (home / "models_cache.json").write_text(
                json.dumps({"models": [{"slug": "gpt-official", "display_name": "GPT Official", "shell_type": "shell_command"}]}),
                encoding="utf-8",
            )

            result = run_tool(
                home,
                "configure",
                "--base-url",
                "https://custom.example/v1",
                "--custom-model",
                "vendor/custom-model",
                "--custom-model-name",
                "我的模型",
                "--official-model",
                "gpt-official",
                "--default-provider",
                "custom",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('model_provider = "custom"', config)
            self.assertIn('model = "vendor/custom-model"', config)
            self.assertIn('models = ["vendor/custom-model"]', config)
            self.assertEqual(read_state(home)["default_provider"], "custom")
            self.assertEqual(read_state(home)["local_upstream_model"], "vendor/custom-model")
            # No custom catalog is written in any mode (cc-switch style): the
            # active model is set in config.toml and Codex uses its built-in
            # catalog, so saved conversations always resolve and never disappear.
            self.assertNotIn('model_catalog_json', config)
            self.assertFalse((home / "codex-switch-model-catalog.json").exists())

    def test_custom_catalog_preserves_models_used_by_existing_threads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir()
            (home / "models_cache.json").write_text(
                json.dumps({"models": [{"slug": "gpt-5.5", "display_name": "GPT-5.5", "shell_type": "shell_command"}]}),
                encoding="utf-8",
            )
            # A saved conversation that used a model no longer in models_cache.json.
            db = home / "sqlite"
            db.mkdir()
            conn = sqlite3.connect(db / "state_1.sqlite")
            conn.execute("CREATE TABLE threads (id TEXT, model TEXT, model_provider TEXT)")
            conn.execute("INSERT INTO threads VALUES ('t1', 'gpt-5.2-codex', 'openai')")
            conn.commit()
            conn.close()

            catalog = cli_module.custom_model_catalog(home, "glm-5.2", "我的GPT")
            slugs = [m["slug"] for m in catalog["models"]]
            # Historical model is preserved so the conversation still resolves, the
            # official model keeps its name, and the custom model is named.
            self.assertIn("gpt-5.2-codex", slugs)
            self.assertIn("gpt-5.5", slugs)
            self.assertIn("glm-5.2", slugs)
            historical = next(m for m in catalog["models"] if m["slug"] == "gpt-5.2-codex")
            self.assertEqual(historical["shell_type"], "shell_command")
            # Historical models are hidden from the picker (only kept for resolution).
            self.assertEqual(historical["visibility"], "hide")
            custom = next(m for m in catalog["models"] if m["slug"] == "glm-5.2")
            self.assertEqual(custom["display_name"], "我的GPT")
            # The custom model stays visible in the picker.
            self.assertNotEqual(custom.get("visibility"), "hide")

    def test_responses_adapter_translates_basic_request_to_chat_payload(self) -> None:
        payload = cli_module.responses_request_to_chat_payload(
            {
                "instructions": "Be brief.",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "hello"}],
                    }
                ],
                "tools": [
                    {
                        "type": "function",
                        "name": "run_shell",
                        "description": "Run a shell command",
                        "parameters": {"type": "object"},
                    }
                ],
            },
            "glm-5.2",
            True,
        )

        self.assertEqual(payload["model"], "glm-5.2")
        self.assertEqual(payload["stream"], True)
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "Be brief."})
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "hello"})
        self.assertEqual(payload["tools"][0]["function"]["name"], "run_shell")

    def test_chat_adapter_translates_tool_calls_to_responses_function_calls(self) -> None:
        response = cli_module.chat_response_to_responses(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {
                                        "name": "run_shell",
                                        "arguments": "{\"cmd\":\"pwd\"}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            "glm-5.2",
        )

        self.assertEqual(response["output"][0]["type"], "function_call")
        self.assertEqual(response["output"][0]["call_id"], "call_123")
        self.assertEqual(response["output"][0]["name"], "run_shell")
        self.assertEqual(response["output"][0]["arguments"], "{\"cmd\":\"pwd\"}")

    def test_chat_adapter_streams_function_call_arguments_events(self) -> None:
        handler = cli_module.AdapterHandler.__new__(cli_module.AdapterHandler)
        handler.wfile = io.BytesIO()
        handler.write_response_stream(
            {
                "id": "resp_test",
                "object": "response",
                "status": "completed",
                "model": "glm-5.2",
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_test",
                        "call_id": "call_123",
                        "name": "run_shell",
                        "arguments": "{\"cmd\":\"pwd\"}",
                        "status": "completed",
                    }
                ],
            }
        )

        stream = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("event: response.function_call_arguments.delta", stream)
        self.assertIn("event: response.function_call_arguments.done", stream)
        self.assertIn("\"arguments\": \"{\\\"cmd\\\":\\\"pwd\\\"}\"", stream)

    @staticmethod
    def _make_adapter_handler() -> "cli_module.AdapterHandler":
        handler = cli_module.AdapterHandler.__new__(cli_module.AdapterHandler)
        handler.wfile = io.BytesIO()
        handler.send_response = lambda code: None
        handler.send_header = lambda key, value: None
        handler.end_headers = lambda: None
        return handler

    class _FakeUpstreamResponse:
        def __init__(self, body: bytes, content_type: str):
            self._body = body
            self.headers = {"Content-Type": content_type}

        def read(self, *args):
            return self._body

        def close(self):
            pass

        def __iter__(self):
            return iter([self._body])

    def test_responses_passthrough_payload_sanitize_tiers(self) -> None:
        request = {
            "input": [], "tools": [{"type": "function", "name": "run_shell"}],
            "store": False, "include": ["reasoning.encrypted_content"],
            "prompt_cache_key": "abc", "reasoning": {"effort": "medium"},
        }
        attempts = cli_module.responses_passthrough_payloads(request, "glm-5.2", True)

        self.assertEqual(len(attempts), 3)
        self.assertIn("store", attempts[0])
        # Tier 1 drops OpenAI-bookkeeping fields but keeps generation tuning.
        self.assertNotIn("store", attempts[1])
        self.assertNotIn("include", attempts[1])
        self.assertNotIn("prompt_cache_key", attempts[1])
        self.assertIn("reasoning", attempts[1])
        # Tier 2 drops generation tuning too; tools survive every tier.
        self.assertNotIn("reasoning", attempts[2])
        for attempt in attempts:
            self.assertEqual(attempt["model"], "glm-5.2")
            self.assertTrue(attempt["stream"])
            self.assertIn("tools", attempt)

    def test_responses_relay_remembers_unsupported_route(self) -> None:
        cli_module._RESPONSES_UNSUPPORTED_ROUTES.clear()
        handler = self._make_adapter_handler()
        call_count = 0

        def fake_urlopen(req, timeout=None):
            nonlocal call_count
            call_count += 1
            raise urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {},
                io.BytesIO(b'{"error":{"message":"Model glm-5.2 does not support /v1/responses"}}'),
            )

        request = {"input": [], "store": False, "reasoning": {"effort": "medium"}}
        with mock.patch.object(cli_module.urllib.request, "urlopen", side_effect=fake_urlopen):
            first = handler.relay_responses_stream(request, "https://relay.example", "glm-5.2", "sk-x", [])
            calls_after_first = call_count
            errors: list[str] = []
            second = handler.relay_responses_stream(request, "https://relay.example", "glm-5.2", "sk-x", errors)

        self.assertFalse(first)
        # First request exhausted every sanitize tier (full + 2 sanitized)...
        self.assertEqual(calls_after_first, 3)
        # ...then the route is remembered: no further /responses round-trips.
        self.assertFalse(second)
        self.assertEqual(call_count, calls_after_first)
        self.assertTrue(any("skipped" in e for e in errors))
        cli_module._RESPONSES_UNSUPPORTED_ROUTES.clear()

    def test_responses_relay_retries_sanitized_payload_after_400(self) -> None:
        cli_module._RESPONSES_UNSUPPORTED_ROUTES.clear()
        handler = self._make_adapter_handler()
        sse_body = b"event: response.completed\ndata: {}\n\ndata: [DONE]\n\n"
        sent_payloads = []

        def fake_urlopen(req, timeout=None):
            sent_payloads.append(json.loads(req.data.decode("utf-8")))
            if len(sent_payloads) == 1:
                raise urllib.error.HTTPError(
                    req.full_url, 400, "Bad Request", {},
                    io.BytesIO(b'{"error":{"message":"Unknown parameter: store"}}'),
                )
            return self._FakeUpstreamResponse(sse_body, "text/event-stream")

        errors: list[str] = []
        with mock.patch.object(cli_module.urllib.request, "urlopen", side_effect=fake_urlopen):
            handled = handler.relay_responses_stream(
                {"input": [], "store": False, "include": ["reasoning.encrypted_content"]},
                "https://relay.example", "glm-5.2", "sk-x", errors,
            )

        self.assertTrue(handled)
        self.assertIn(sse_body.decode("utf-8"), handler.wfile.getvalue().decode("utf-8"))
        self.assertIn("store", sent_payloads[0])
        self.assertNotIn("store", sent_payloads[1])
        self.assertTrue(any("HTTP 400" in e and "store" in e for e in errors))

    def test_responses_relay_synthesizes_stream_from_json_response(self) -> None:
        cli_module._RESPONSES_UNSUPPORTED_ROUTES.clear()
        handler = self._make_adapter_handler()
        complete = {
            "id": "resp_upstream", "object": "response", "status": "completed",
            "model": "glm-5.2",
            "output": [{
                "type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                "content": [{"type": "output_text", "text": "你好", "annotations": []}],
            }],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }

        def fake_urlopen(req, timeout=None):
            return self._FakeUpstreamResponse(
                json.dumps(complete).encode("utf-8"), "application/json",
            )

        with mock.patch.object(cli_module.urllib.request, "urlopen", side_effect=fake_urlopen):
            handled = handler.relay_responses_stream({"input": []}, "https://relay.example", "glm-5.2", "sk-x", [])

        self.assertTrue(handled)
        stream = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("event: response.created", stream)
        self.assertIn("event: response.output_text.delta", stream)
        self.assertIn("event: response.completed", stream)
        self.assertIn("data: [DONE]", stream)
        # Chat-style usage keys are normalized to Responses keys.
        self.assertIn('"input_tokens": 3', stream)

    def test_chat_relay_retries_after_connection_dropped_without_response(self) -> None:
        import http.client
        handler = self._make_adapter_handler()
        sse_body = (
            b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            b"data: [DONE]\n\n"
        )
        attempts = 0

        def fake_urlopen(req, timeout=None):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise http.client.RemoteDisconnected("Remote end closed connection without response")
            return self._FakeUpstreamResponse(sse_body, "text/event-stream")

        errors: list[str] = []
        with mock.patch.object(cli_module.urllib.request, "urlopen", side_effect=fake_urlopen), \
                mock.patch.object(cli_module.time, "sleep"):
            handled = handler.relay_chat_stream({"input": []}, "https://relay.example", "glm-5.2", "sk-x", "resp_1", errors)

        self.assertTrue(handled)
        self.assertEqual(attempts, 2)
        stream = handler.wfile.getvalue().decode("utf-8")
        self.assertIn("response.output_text.delta", stream)
        self.assertIn("response.completed", stream)

    def test_stream_failure_reports_upstream_diagnostics(self) -> None:
        cli_module._RESPONSES_UNSUPPORTED_ROUTES.clear()
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir(parents=True)
            (home / "codex-switch-state.json").write_text(
                json.dumps({
                    "adapter_upstream_base_url": "https://relay.example",
                    "local_api_key": "sk-x",
                    "local_upstream_model": "glm-5.2",
                }),
                encoding="utf-8",
            )
            (home / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-x"}), encoding="utf-8")
            handler = self._make_adapter_handler()
            handler.server = SimpleNamespace(home=home)

            def fake_urlopen(req, timeout=None):
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b"no such route"))

            with mock.patch.object(cli_module.urllib.request, "urlopen", side_effect=fake_urlopen):
                handler.proxy_response({"stream": True, "input": []})

            stream = handler.wfile.getvalue().decode("utf-8")
            self.assertIn("response.failed", stream)
            self.assertIn("HTTP 404", stream)
            self.assertIn("/responses", stream)
            self.assertIn("/chat/completions", stream)
            self.assertIn("no such route", stream)

    def test_configure_codex_maps_duplicate_upstream_model_to_safe_custom_slug(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-test-secret"}),
                encoding="utf-8",
            )
            (home / "models_cache.json").write_text(
                json.dumps({"models": [{"slug": "gpt-5.5", "display_name": "GPT-5.5"}]}),
                encoding="utf-8",
            )

            result = run_tool(
                home,
                "configure",
                "--base-url",
                "https://custom.example/v1",
                "--custom-model",
                "gpt-5.5",
                "--custom-model-name",
                "我的模型",
                "--official-model",
                "gpt-5.5",
                "--default-provider",
                "custom",
                "--chat-adapter",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('model_provider = "custom"', config)
            self.assertIn('model = "codex-switch/gpt-5.5"', config)
            self.assertIn('models = ["codex-switch/gpt-5.5"]', config)
            state = read_state(home)
            # When the upstream model id collides with an official one, the custom
            # slug is namespaced so config/CLI routing stays unambiguous, while the
            # adapter still sends the real upstream id ("gpt-5.5").
            self.assertEqual(state["local_model"], "codex-switch/gpt-5.5")
            self.assertEqual(state["local_upstream_model"], "gpt-5.5")
            self.assertEqual(state["adapter_upstream_model"], "gpt-5.5")
            # No custom catalog is written.
            self.assertNotIn('model_catalog_json', config)
            self.assertFalse((home / "codex-switch-model-catalog.json").exists())

    def test_local_switch_rejects_empty_api_key_from_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-old-secret"}),
                encoding="utf-8",
            )

            result = run_tool(home, "local", "--api-key-stdin", input_text="\n")

            self.assertEqual(result.returncode, 2)
            self.assertIn("stdin was empty", result.stderr)

    def test_local_switch_preserves_existing_chatgpt_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "auth.json").write_text(
                json.dumps({
                    "auth_mode": "apikey",
                    "OPENAI_API_KEY": None,
                    "tokens": {
                        "access_token": "current-access",
                        "refresh_token": "current-refresh",
                    },
                }),
                encoding="utf-8",
            )

            result = run_tool(home, "local", "--api-key", "sk-test-secret")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads((home / "auth.json").read_text(encoding="utf-8")),
                {
                    "auth_mode": "chatgpt",
                    "OPENAI_API_KEY": "sk-test-secret",
                    "tokens": {
                        "access_token": "current-access",
                        "refresh_token": "current-refresh",
                    },
                },
            )
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('experimental_bearer_token = "sk-test-secret"', config)

    def test_local_switch_preserves_existing_conversation_metadata(self) -> None:
        # Core requirement: switching providers must NOT rewrite saved
        # conversations. The desktop does not filter the list by model_provider,
        # so re-tagging is both unnecessary and destructive.
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            write_thread_database(home)
            rollout = write_rollout(home, "latest-official", "openai", user_event=True)
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-test-secret"}),
                encoding="utf-8",
            )

            result = run_tool(home, "local", "--migrate-latest", "--no-open")

            self.assertEqual(result.returncode, 0, result.stderr)
            # Saved conversation keeps its own provider/model untouched.
            meta = json.loads(rollout.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(meta["payload"]["model_provider"], "openai")
            self.assertEqual(meta["payload"]["model"], "gpt-5.5")
            with closing(sqlite3.connect(home / "sqlite" / "state_5.sqlite")) as connection:
                providers = {row[0] for row in connection.execute("SELECT model_provider FROM threads")}
                models = {row[0] for row in connection.execute("SELECT model FROM threads")}
            self.assertEqual(providers, {"openai", "custom"})
            self.assertEqual(models, {"gpt-5.5", "codex-switch/gpt-5.5"})

    def test_local_switch_does_not_retag_threads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            write_thread_database(home)
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-test-secret"}),
                encoding="utf-8",
            )

            result = run_tool(home, "local", "--migrate-latest", "--no-open")

            self.assertEqual(result.returncode, 0, result.stderr)
            with closing(sqlite3.connect(home / "sqlite" / "state_5.sqlite")) as connection:
                providers = {row[0] for row in connection.execute("SELECT model_provider FROM threads")}
            self.assertEqual(providers, {"openai", "custom"})

    def test_official_switch_preserves_existing_conversation_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_local_config(home)
            write_thread_database(home)
            rollout = write_rollout(home, "current-custom", "custom")
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-test-secret"}),
                encoding="utf-8",
            )

            result = run_tool(home, "official", "--migrate-latest", "--no-open")

            self.assertEqual(result.returncode, 0, result.stderr)
            # The custom conversation keeps its provider/model after switching to official.
            meta = json.loads(rollout.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(meta["payload"]["model_provider"], "custom")
            self.assertEqual(meta["payload"]["model"], "codex-switch/gpt-5.5")
            with closing(sqlite3.connect(home / "sqlite" / "state_5.sqlite")) as connection:
                providers = {row[0] for row in connection.execute("SELECT model_provider FROM threads")}
                models = {row[0] for row in connection.execute("SELECT model FROM threads")}
            self.assertEqual(providers, {"openai", "custom"})
            self.assertEqual(models, {"gpt-5.5", "codex-switch/gpt-5.5"})

    def test_local_switch_uses_public_default_api_address(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "codex-switch-state.json").write_text(
                json.dumps({"local_api_key": "sk-cached-secret"}),
                encoding="utf-8",
            )
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None}),
                encoding="utf-8",
            )

            result = run_tool(home, "local")

            self.assertEqual(result.returncode, 0, result.stderr)
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('base_url = "https://jp.icodeeasy.cc"', config)

    def test_official_switch_preserves_existing_oauth_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "codex-switch-state.json").write_text(
                json.dumps({
                    "official_auth": {
                        "auth_mode": "chatgpt",
                        "OPENAI_API_KEY": None,
                        "tokens": {
                            "access_token": "old-access",
                            "refresh_token": "old-refresh",
                        },
                    }
                }),
                encoding="utf-8",
            )
            (home / "auth.json").write_text(
                json.dumps({
                    "auth_mode": "apikey",
                    "OPENAI_API_KEY": "sk-test-secret",
                    "tokens": {
                        "access_token": "current-access",
                        "refresh_token": "current-refresh",
                    },
                }),
                encoding="utf-8",
            )

            result = run_tool(home, "official")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads((home / "auth.json").read_text(encoding="utf-8")),
                {
                    "auth_mode": "chatgpt",
                    "OPENAI_API_KEY": "sk-test-secret",
                    "tokens": {
                        "access_token": "current-access",
                        "refresh_token": "current-refresh",
                    },
                },
            )
            self.assertNotIn("official_auth", read_state(home))

    def test_configure_skip_login_stashes_tokens_and_configure_restores_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "auth.json").write_text(
                json.dumps({
                    "auth_mode": "chatgpt",
                    "OPENAI_API_KEY": "sk-test-secret",
                    "last_refresh": "2026-07-01T00:00:00Z",
                    "tokens": {"access_token": "acc", "refresh_token": "ref"},
                }),
                encoding="utf-8",
            )

            configure_args = [
                "configure",
                "--base-url", "https://custom.example/v1",
                "--custom-model", "vendor/custom-model",
                "--official-model", "gpt-official",
            ]
            result = run_tool(home, *configure_args, "--skip-login")

            self.assertEqual(result.returncode, 0, result.stderr)
            auth = json.loads((home / "auth.json").read_text(encoding="utf-8"))
            self.assertEqual(auth["auth_mode"], "api-key")
            self.assertNotIn("tokens", auth)
            stash = read_state(home)["stashed_chatgpt_login"]
            self.assertEqual(stash["tokens"], {"access_token": "acc", "refresh_token": "ref"})
            self.assertEqual(stash["last_refresh"], "2026-07-01T00:00:00Z")

            result = run_tool(home, *configure_args)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Restored the stashed ChatGPT login", result.stdout)
            auth = json.loads((home / "auth.json").read_text(encoding="utf-8"))
            self.assertEqual(auth["auth_mode"], "chatgpt")
            self.assertEqual(auth["tokens"], {"access_token": "acc", "refresh_token": "ref"})
            self.assertEqual(auth["last_refresh"], "2026-07-01T00:00:00Z")
            self.assertNotIn("stashed_chatgpt_login", read_state(home))

    def test_official_switch_restores_stashed_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "codex-switch-state.json").write_text(
                json.dumps({
                    "stashed_chatgpt_login": {
                        "tokens": {"access_token": "acc", "refresh_token": "ref"},
                        "last_refresh": "2026-07-01T00:00:00Z",
                    }
                }),
                encoding="utf-8",
            )
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "api-key", "OPENAI_API_KEY": "sk-test-secret"}),
                encoding="utf-8",
            )

            result = run_tool(home, "official")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Restored the stashed ChatGPT login", result.stdout)
            auth = json.loads((home / "auth.json").read_text(encoding="utf-8"))
            self.assertEqual(auth["auth_mode"], "chatgpt")
            self.assertEqual(auth["tokens"], {"access_token": "acc", "refresh_token": "ref"})
            self.assertEqual(auth["last_refresh"], "2026-07-01T00:00:00Z")
            self.assertNotIn("stashed_chatgpt_login", read_state(home))

    def test_configure_skip_login_twice_keeps_existing_stash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "codex-switch-state.json").write_text(
                json.dumps({
                    "stashed_chatgpt_login": {
                        "tokens": {"access_token": "acc", "refresh_token": "ref"},
                    }
                }),
                encoding="utf-8",
            )
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "api-key", "OPENAI_API_KEY": "sk-test-secret"}),
                encoding="utf-8",
            )

            result = run_tool(
                home,
                "configure",
                "--base-url", "https://custom.example/v1",
                "--custom-model", "vendor/custom-model",
                "--official-model", "gpt-official",
                "--skip-login",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            stash = read_state(home)["stashed_chatgpt_login"]
            self.assertEqual(stash["tokens"], {"access_token": "acc", "refresh_token": "ref"})

    def test_restore_prefers_live_tokens_and_drops_stale_stash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "codex-switch-state.json").write_text(
                json.dumps({
                    "stashed_chatgpt_login": {
                        "tokens": {"access_token": "stale", "refresh_token": "stale"},
                    }
                }),
                encoding="utf-8",
            )
            (home / "auth.json").write_text(
                json.dumps({
                    "auth_mode": "chatgpt",
                    "OPENAI_API_KEY": "sk-test-secret",
                    "tokens": {"access_token": "fresh", "refresh_token": "fresh"},
                }),
                encoding="utf-8",
            )

            result = run_tool(home, "official")

            self.assertEqual(result.returncode, 0, result.stderr)
            auth = json.loads((home / "auth.json").read_text(encoding="utf-8"))
            self.assertEqual(auth["tokens"], {"access_token": "fresh", "refresh_token": "fresh"})
            self.assertNotIn("stashed_chatgpt_login", read_state(home))

    def test_status_redacts_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-test-secret"}),
                encoding="utf-8",
            )

            result = run_tool(home, "status")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("auth_mode: apikey", result.stdout)
            self.assertIn("api_key: sk-...cret", result.stdout)
            self.assertNotIn("sk-test-secret", result.stdout)

    def test_local_switch_requires_key_when_none_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None}),
                encoding="utf-8",
            )

            result = run_tool(home, "local")

            self.assertEqual(result.returncode, 2)
            self.assertIn("--api-key", result.stderr)

    def test_config_set_and_show_persist_switch_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)

            result = run_tool(
                home,
                "config",
                "set",
                "--local-base-url",
                "http://127.0.0.1:17777/v1",
                "--local-model",
                "gpt-local",
                "--official-model",
                "gpt-official",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state = read_state(home)
            self.assertEqual(state["local_base_url"], "http://127.0.0.1:17777/v1")
            self.assertEqual(state["local_model"], "gpt-local")
            self.assertEqual(state["official_model"], "gpt-official")

            show = run_tool(home, "config", "show")
            self.assertEqual(show.returncode, 0, show.stderr)
            self.assertIn("local_base_url: http://127.0.0.1:17777/v1", show.stdout)
            self.assertIn("local_model: gpt-local", show.stdout)
            self.assertIn("official_model: gpt-official", show.stdout)

    def test_config_show_uses_public_default_api_address(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)

            show = run_tool(home, "config", "show")

            self.assertEqual(show.returncode, 0, show.stderr)
            self.assertIn("local_base_url: https://jp.icodeeasy.cc", show.stdout)

    def test_claude_local_switch_writes_env_and_redacts_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".claude"
            home.mkdir()
            (home / "settings.json").write_text(
                json.dumps(
                    {
                        "agentPushNotifEnabled": False,
                        "env": {"ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-4-6"},
                    }
                ),
                encoding="utf-8",
            )

            result = run_tool_with_env(
                {"CLAUDE_CONFIG_DIR": str(home), "CODEX_HOME": str(home.parent / ".codex")},
                "claude-local",
                "--base-url",
                "https://claude.example.com",
                "--model",
                "router/claude-sonnet",
                "--auth-token-stdin",
                input_text="sk-claude-secret\n",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("sk-claude-secret", result.stdout)
            settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
            self.assertFalse(settings["agentPushNotifEnabled"])
            # Claude Code is routed through the local adapter; the real upstream URL
            # is stored separately, not written into ANTHROPIC_BASE_URL.
            self.assertEqual(
                settings["env"]["ANTHROPIC_BASE_URL"],
                f"http://{cli_module.DEFAULT_ADAPTER_HOST}:{cli_module.DEFAULT_ADAPTER_PORT}",
            )
            self.assertEqual(settings["env"]["ANTHROPIC_AUTH_TOKEN"], "sk-claude-secret")
            self.assertEqual(settings["env"]["ANTHROPIC_MODEL"], "router/claude-sonnet")
            self.assertNotIn("ANTHROPIC_API_KEY", settings["env"])

    def test_claude_official_switch_removes_custom_route_and_preserves_saved_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".claude"
            home.mkdir()
            (home / "settings.json").write_text(
                json.dumps(
                    {
                        "env": {
                            "ANTHROPIC_BASE_URL": "https://claude.example.com",
                            "ANTHROPIC_AUTH_TOKEN": "sk-claude-secret",
                            "ANTHROPIC_MODEL": "router/claude-sonnet",
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = run_claude_tool(home, "claude-official", "--model", "claude-opus-4-6")

            self.assertEqual(result.returncode, 0, result.stderr)
            settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
            self.assertNotIn("ANTHROPIC_BASE_URL", settings["env"])
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", settings["env"])
            self.assertEqual(settings["env"]["ANTHROPIC_MODEL"], "claude-opus-4-6")
            state = json.loads((home / "claude-switch-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["local_api_key"], "sk-claude-secret")

    def test_claude_local_switch_rejects_empty_token_from_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".claude"
            home.mkdir()
            (home / "settings.json").write_text("{}", encoding="utf-8")

            result = run_claude_tool(home, "claude-local", "--auth-token-stdin", input_text="\n")

            self.assertEqual(result.returncode, 2)
            self.assertIn("stdin was empty", result.stderr)

    def test_official_switch_uses_cached_official_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_local_config(home)
            (home / "codex-switch-state.json").write_text(
                json.dumps({"official_model": "gpt-cached-official"}),
                encoding="utf-8",
            )
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-test-secret"}),
                encoding="utf-8",
            )

            result = run_tool(home, "official")

            self.assertEqual(result.returncode, 0, result.stderr)
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('model = "gpt-cached-official"', config)

    def test_restart_codex_flag_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-test-secret"}),
                encoding="utf-8",
            )

            result = run_tool(home, "official", "--migrate-latest")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Restarted Codex.app", result.stdout)

    def test_restart_does_not_retag_conversations(self) -> None:
        # Verified empirically: the desktop does NOT filter the conversation list
        # by model_provider, so restart must leave saved conversations untouched.
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_local_config(home)  # model_provider = "custom"
            write_thread_database(home)
            args = SimpleNamespace(restart_codex=True, migrate_latest=False)

            run_ok = mock.Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
            with mock.patch.object(cli_module.subprocess, "run", run_ok), mock.patch.object(cli_module.time, "sleep"):
                cli_module.restart_codex(args, home, "custom", "gpt-5.5")

            with closing(sqlite3.connect(home / "sqlite" / "state_5.sqlite")) as connection:
                providers = {row[0] for row in connection.execute("SELECT model_provider FROM threads")}
                models = {row[0] for row in connection.execute("SELECT model FROM threads")}
            # Threads keep their original provider/model.
            self.assertEqual(providers, {"openai", "custom"})
            self.assertEqual(models, {"gpt-5.5", "codex-switch/gpt-5.5"})

    def test_restart_launch_failure_warns_instead_of_failing(self) -> None:
        # LaunchServices can refuse to relaunch (-609) while the old process is
        # still terminating. The switch already succeeded by then, so restart
        # must warn and return instead of raising.
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)  # openai/gpt-5: verification would raise for custom/gpt-5.5
            args = SimpleNamespace(restart_codex=True, migrate_latest=False)

            def fake_run(cmd, **kwargs):
                if cmd[0] == "pgrep":
                    return SimpleNamespace(returncode=1)
                if cmd[0] == "open":
                    return SimpleNamespace(
                        returncode=1,
                        stdout="",
                        stderr="_LSOpenURLsWithCompletionHandler() failed with error -609.",
                    )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            captured = io.StringIO()
            with mock.patch.object(cli_module.subprocess, "run", side_effect=fake_run) as run_mock, \
                    mock.patch.object(cli_module.time, "sleep"):
                from contextlib import redirect_stdout
                with redirect_stdout(captured):
                    cli_module.restart_codex(args, home, "custom", "gpt-5.5")

            output = captured.getvalue()
            self.assertIn("restart_warn", output)
            self.assertIn("error -609", output)
            self.assertNotIn("Restarted Codex.app", output)
            # The relaunch was retried before giving up.
            open_calls = [c for c in run_mock.call_args_list if c.args[0][0] == "open"]
            self.assertEqual(len(open_calls), 3)

    def test_restart_does_not_touch_conversations_on_provider_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            write_thread_database(home)
            rollout = write_rollout(home, "current-custom", "custom")
            args = SimpleNamespace(restart_codex=True, migrate_latest=True)

            run_ok = mock.Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
            with mock.patch.object(cli_module.subprocess, "run", run_ok), mock.patch.object(cli_module.time, "sleep"):
                with self.assertRaisesRegex(cli_module.SwitchError, "restarted with provider/model openai/gpt-5, not custom/gpt-5.5"):
                    cli_module.restart_codex(args, home, "custom", "gpt-5.5")

            # Even on mismatch, conversations are never rewritten.
            meta = json.loads(rollout.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(meta["payload"]["model_provider"], "custom")
            self.assertEqual(meta["payload"]["model"], "codex-switch/gpt-5.5")
            with closing(sqlite3.connect(home / "sqlite" / "state_5.sqlite")) as connection:
                providers = {row[0] for row in connection.execute("SELECT model_provider FROM threads")}
            self.assertEqual(providers, {"openai", "custom"})


if __name__ == "__main__":
    unittest.main()
