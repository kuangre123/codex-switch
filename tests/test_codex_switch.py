import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Optional


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "codex_switch" / "cli.py"


def run_tool(home: Path, *args: str, extra_env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


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
    text = text.replace('preferred_auth_method = "chatgpt"', 'preferred_auth_method = "apikey"', 1)
    (home / "config.toml").write_text(text, encoding="utf-8")


def write_config_without_custom_provider(home: Path) -> None:
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        """model_provider = "openai"
model = "gpt-5"
preferred_auth_method = "chatgpt"
""",
        encoding="utf-8",
    )


def read_state(home: Path) -> dict:
    return json.loads((home / "codex-switch-state.json").read_text(encoding="utf-8"))


def write_session_file(home: Path, folder: str, session_id: str, cwd: str, timestamp: str) -> None:
    target = home / folder / "2026" / "06" / "14" / f"{session_id}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": session_id,
                            "timestamp": timestamp,
                            "cwd": cwd,
                            "model_provider": "custom",
                        },
                    }
                ),
                json.dumps({"type": "event_msg", "payload": {"timestamp": timestamp}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


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
                {"auth_mode": "apikey", "OPENAI_API_KEY": "sk-test-secret"},
            )
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('model_provider = "custom"', config)
            self.assertIn('model = "gpt-5.5"', config)
            self.assertIn('preferred_auth_method = "apikey"', config)
            self.assertIn('base_url = "http://127.0.0.1:15721/v1"', config)
            self.assertIn('requires_openai_auth = true', config)
            self.assertIn('wire_api = "responses"', config)
            self.assertIn('[projects."/Users/sirchen/Documents/aimashi"]', config)
            self.assertTrue((home / "backups").is_dir())
            self.assertTrue(any(p.name.startswith("config.toml.") for p in (home / "backups").iterdir()))
            self.assertEqual(stat.S_IMODE((home / "auth.json").stat().st_mode), 0o600)

    def test_switch_snapshots_session_index_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "session_index.jsonl").write_text(
                json.dumps({"id": "session-1", "thread_name": "Old title", "updated_at": "2026-06-14T10:00:00Z"}) + "\n",
                encoding="utf-8",
            )
            (home / ".codex-global-state.json").write_text("{}", encoding="utf-8")
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None}),
                encoding="utf-8",
            )

            result = run_tool(home, "local", "--api-key", "sk-test-secret")

            self.assertEqual(result.returncode, 0, result.stderr)
            backup_names = [p.name for p in (home / "backups").iterdir()]
            self.assertTrue(any(name.startswith("session_index.jsonl.") for name in backup_names))
            self.assertTrue(any(name.startswith(".codex-global-state.json.") for name in backup_names))
            self.assertIn("session_snapshot:", result.stdout)

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
                {"auth_mode": "chatgpt", "OPENAI_API_KEY": None},
            )
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('model_provider = "openai"', config)
            self.assertIn('model = "gpt-5.5"', config)
            self.assertIn('preferred_auth_method = "chatgpt"', config)
            self.assertIn('base_url = "http://127.0.0.1:9999/v1"', config)
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
                {"auth_mode": "apikey", "OPENAI_API_KEY": "sk-cached-secret"},
            )
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('base_url = "http://127.0.0.1:18888/v1"', config)
            self.assertIn('model = "local-model"', config)
            self.assertNotIn("sk-cached-secret", result.stdout)

    def test_local_switch_uses_public_default_api_address(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_config_without_custom_provider(home)
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

    def test_local_switch_prefers_existing_codex_custom_base_url(self) -> None:
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
            show = run_tool(home, "config", "show")

            self.assertEqual(result.returncode, 0, result.stderr)
            config = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('base_url = "http://127.0.0.1:9999/v1"', config)
            self.assertIn("local_base_url: http://127.0.0.1:9999/v1", show.stdout)

    def test_config_show_uses_env_base_url_when_no_saved_or_configured_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir(parents=True)

            show = run_tool(
                home,
                "config",
                "show",
                extra_env={"OPENAI_BASE_URL": "https://env-relay.example.com"},
            )

            self.assertEqual(show.returncode, 0, show.stderr)
            self.assertIn("local_base_url: https://env-relay.example.com", show.stdout)

    def test_local_switch_saves_passed_base_url_and_model_as_defaults(self) -> None:
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
                "https://relay.example.com",
                "--model",
                "codex-relay",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state = read_state(home)
            self.assertEqual(state["local_base_url"], "https://relay.example.com")
            self.assertEqual(state["local_model"], "codex-relay")

    def test_official_switch_does_not_restore_cached_oauth_tokens(self) -> None:
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
                json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-test-secret"}),
                encoding="utf-8",
            )

            result = run_tool(home, "official")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads((home / "auth.json").read_text(encoding="utf-8")),
                {"auth_mode": "chatgpt", "OPENAI_API_KEY": None},
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

    def test_status_zh_and_needs_setup_support_app_onboarding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_sample_config(home)
            (home / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None}),
                encoding="utf-8",
            )

            needs_setup = run_tool(home, "needs-setup")
            status_zh = run_tool(home, "status-zh")

            self.assertEqual(needs_setup.returncode, 0, needs_setup.stderr)
            self.assertEqual(needs_setup.stdout.strip(), "yes")
            self.assertEqual(status_zh.returncode, 0, status_zh.stderr)
            self.assertIn("当前模式: 官方 OpenAI", status_zh.stdout)
            self.assertIn("当前认证: ChatGPT 登录", status_zh.stdout)
            self.assertIn("已保存的自定义 API Key: 未配置", status_zh.stdout)
            self.assertIn("自定义 API 地址: http://127.0.0.1:9999/v1", status_zh.stdout)

    def test_needs_setup_uses_cached_local_api_key(self) -> None:
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

            needs_setup = run_tool(home, "needs-setup")
            status_zh = run_tool(home, "status-zh")

            self.assertEqual(needs_setup.returncode, 0, needs_setup.stderr)
            self.assertEqual(needs_setup.stdout.strip(), "no")
            self.assertIn("已保存的自定义 API Key: 已配置 sk-...cret", status_zh.stdout)

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
            write_config_without_custom_provider(home)

            show = run_tool(home, "config", "show")

            self.assertEqual(show.returncode, 0, show.stderr)
            self.assertIn("local_base_url: https://jp.icodeeasy.cc", show.stdout)

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

    def test_sessions_rebuild_index_adds_missing_and_preserves_titles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir(parents=True)
            session_existing = "019existing-session"
            session_missing = "019missing-session"
            (home / "session_index.jsonl").write_text(
                json.dumps(
                    {
                        "id": session_existing,
                        "thread_name": "Keep This Title",
                        "updated_at": "2026-06-14T09:00:00.000000Z",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            write_session_file(
                home,
                "sessions",
                session_existing,
                "/Users/example/old-project",
                "2026-06-14T10:00:00.000000Z",
            )
            write_session_file(
                home,
                "sessions",
                session_missing,
                "/Users/example/new-project",
                "2026-06-14T11:00:00.000000Z",
            )
            write_session_file(
                home,
                "archived_sessions",
                session_missing,
                "/Users/example/new-project-archive",
                "2026-06-14T08:00:00.000000Z",
            )

            result = run_tool(home, "sessions", "rebuild-index")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("sessions_added: 1", result.stdout)
            self.assertIn("sessions_discovered: 2", result.stdout)
            rows = [
                json.loads(line)
                for line in (home / "session_index.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            by_id = {row["id"]: row for row in rows}
            self.assertEqual(by_id[session_existing]["thread_name"], "Keep This Title")
            self.assertEqual(by_id[session_missing]["thread_name"], "new-project")
            self.assertEqual(by_id[session_missing]["updated_at"], "2026-06-14T11:00:00.000000Z")
            self.assertTrue(any(p.name.startswith("session_index.jsonl.") for p in (home / "backups").iterdir()))

    def test_sessions_snapshot_command_backs_up_lightweight_session_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            home.mkdir(parents=True)
            (home / "session_index.jsonl").write_text("{}", encoding="utf-8")
            (home / ".codex-global-state.json").write_text("{}", encoding="utf-8")

            result = run_tool(home, "sessions", "snapshot")

            self.assertEqual(result.returncode, 0, result.stderr)
            backup_names = [p.name for p in (home / "backups").iterdir()]
            self.assertTrue(any(name.startswith("session_index.jsonl.") for name in backup_names))
            self.assertTrue(any(name.startswith(".codex-global-state.json.") for name in backup_names))


if __name__ == "__main__":
    unittest.main()
