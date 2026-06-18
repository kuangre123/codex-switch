import json
import os
import stat
import subprocess
import sqlite3
import sys
import tempfile
import unittest
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
                cwd TEXT,
                updated_at INTEGER NOT NULL,
                updated_at_ms INTEGER,
                has_user_event INTEGER NOT NULL,
                archived INTEGER NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("older-official", "openai", None, 10, 10000, 0, 0),
                ("current-custom", "custom", None, 20, 20000, 0, 0),
                ("latest-official", "openai", None, 30, 30000, 0, 0),
                ("archived-official", "openai", None, 40, 40000, 0, 1),
            ],
        )
        connection.commit()


def write_rollout(home: Path, thread_id: str, provider: str, user_event: bool = False) -> Path:
    path = home / "sessions" / "2026" / "06" / "15" / f"rollout-test-{thread_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "timestamp": "2026-06-15T00:00:00Z",
                "type": "session_meta",
                "payload": {"id": thread_id, "cwd": "/tmp/example", "model_provider": provider},
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
            self.assertIn('requires_openai_auth = true', config)
            self.assertIn('wire_api = "responses"', config)
            self.assertIn('experimental_bearer_token = "sk-test-secret"', config)
            self.assertIn('model_catalog_json = "', config)
            self.assertIn('models = ["gpt-5.5"]', config)
            self.assertIn('[projects."/Users/sirchen/Documents/aimashi"]', config)
            catalog = json.loads((home / "codex-switch-model-catalog.json").read_text(encoding="utf-8"))
            self.assertEqual(catalog["models"][0]["slug"], "gpt-5.5")
            self.assertEqual(catalog["models"][0]["display_name"], "gpt-5.5")
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

    def test_register_model_writes_codex_model_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir()

            result = run_tool(home, "register-model", "my-custom-model", "--name", "我的模型")

            self.assertEqual(result.returncode, 0, result.stderr)
            catalog = json.loads((home / "codex-switch-model-catalog.json").read_text(encoding="utf-8"))
            self.assertEqual(catalog["models"][0]["slug"], "my-custom-model")
            self.assertEqual(catalog["models"][0]["display_name"], "我的模型")
            self.assertEqual(read_state(home)["local_model"], "my-custom-model")
            self.assertEqual(read_state(home)["local_model_display_name"], "我的模型")

    def test_configure_codex_keeps_official_and_custom_models_parallel(self) -> None:
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
            self.assertIn('model_provider = "openai"', config)
            self.assertIn('model = "gpt-official"', config)
            self.assertIn('model_catalog_json = "', config)
            self.assertIn('base_url = "https://custom.example/v1"', config)
            self.assertIn('models = ["vendor/custom-model"]', config)
            catalog = json.loads((home / "codex-switch-model-catalog.json").read_text(encoding="utf-8"))
            self.assertEqual([item["slug"] for item in catalog["models"]], ["gpt-official", "vendor/custom-model"])
            custom_entry = catalog["models"][1]
            self.assertEqual(custom_entry["display_name"], "我的模型")
            # The custom entry must inherit required fields from the official template
            # (Codex rejects the catalog otherwise) but must not carry an upgrade path.
            self.assertEqual(custom_entry["shell_type"], "shell_command")
            self.assertIn("model_messages", custom_entry)
            self.assertNotIn("upgrade", custom_entry)
            # Official entry is untouched.
            self.assertEqual(catalog["models"][0]["display_name"], "GPT Official")
            self.assertNotIn("Provider-synced", result.stdout)

    def test_configure_codex_rejects_duplicate_official_custom_model_id(self) -> None:
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
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("Custom model ID must be different", result.stderr)
            self.assertIn("display name will not appear", result.stderr)
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

    def test_local_switch_provider_syncs_existing_thread_to_custom_provider(self) -> None:
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
            self.assertIn("Provider-synced existing thread context to custom.", result.stdout)
            meta = json.loads(rollout.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(meta["payload"]["model_provider"], "custom")
            with closing(sqlite3.connect(home / "sqlite" / "state_5.sqlite")) as connection:
                providers = {row[0] for row in connection.execute("SELECT model_provider FROM threads")}
                cwd, has_user_event = connection.execute(
                    "SELECT cwd, has_user_event FROM threads WHERE id = 'latest-official'"
                ).fetchone()
            self.assertEqual(providers, {"custom"})
            self.assertEqual(cwd, "/tmp/example")
            self.assertEqual(has_user_event, 1)

    def test_local_switch_provider_syncs_all_threads_when_latest_is_custom(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            write_thread_database(home)
            with closing(sqlite3.connect(home / "sqlite" / "state_5.sqlite")) as connection:
                connection.execute(
                    "UPDATE threads SET updated_at_ms = 50000 WHERE id = 'current-custom'"
                )
                connection.commit()
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": "sk-test-secret"}),
                encoding="utf-8",
            )

            result = run_tool(home, "local", "--migrate-latest", "--no-open")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Provider-synced existing thread context to custom.", result.stdout)

    def test_official_switch_provider_syncs_existing_thread_to_openai(self) -> None:
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
            self.assertIn("Provider-synced existing thread context to openai.", result.stdout)
            meta = json.loads(rollout.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(meta["payload"]["model_provider"], "openai")
            with closing(sqlite3.connect(home / "sqlite" / "state_5.sqlite")) as connection:
                providers = {row[0] for row in connection.execute("SELECT model_provider FROM threads")}
            self.assertEqual(providers, {"openai"})

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

            result = run_claude_tool(
                home,
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
            self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], "https://claude.example.com")
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

    def test_restart_repairs_thread_metadata_when_codex_reverts_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            write_thread_database(home)
            rollout = write_rollout(home, "current-custom", "custom")
            args = SimpleNamespace(restart_codex=True, migrate_latest=True)

            with mock.patch.object(cli_module.subprocess, "run"), mock.patch.object(cli_module.time, "sleep"):
                with self.assertRaisesRegex(cli_module.SwitchError, "restarted with provider openai, not custom"):
                    cli_module.restart_codex(args, home, "custom")

            meta = json.loads(rollout.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(meta["payload"]["model_provider"], "openai")
            with closing(sqlite3.connect(home / "sqlite" / "state_5.sqlite")) as connection:
                providers = {row[0] for row in connection.execute("SELECT model_provider FROM threads")}
            self.assertEqual(providers, {"openai"})


if __name__ == "__main__":
    unittest.main()
