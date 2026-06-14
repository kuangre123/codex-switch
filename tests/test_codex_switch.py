import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "codex_switch" / "cli.py"


def run_tool(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
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


def read_state(home: Path) -> dict:
    return json.loads((home / "codex-switch-state.json").read_text(encoding="utf-8"))


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


if __name__ == "__main__":
    unittest.main()
