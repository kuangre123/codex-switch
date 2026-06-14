#!/usr/bin/env python3
"""Switch Codex auth/config between ChatGPT login and local Responses API."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path


DEFAULT_BASE_URL = "https://jp.icodeeasy.cc"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_OFFICIAL_MODEL = "gpt-5.5"
CONFIG_KEYS = ("local_base_url", "local_model", "official_model")


class SwitchError(RuntimeError):
    pass


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def redacted_key(value: str | None) -> str:
    if not value:
        return "(none)"
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}...{value[-4:]}"


def load_auth(auth_path: Path) -> dict[str, object]:
    if not auth_path.exists():
        return {}
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SwitchError(f"Invalid JSON in {auth_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SwitchError(f"Expected {auth_path} to contain a JSON object")
    return data


def state_path(home: Path) -> Path:
    return home / "codex-switch-state.json"


def load_state(home: Path) -> dict[str, object]:
    path = state_path(home)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SwitchError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SwitchError(f"Expected {path} to contain a JSON object")
    return data


def save_state(home: Path, state: dict[str, object]) -> None:
    atomic_write(state_path(home), json.dumps(state, indent=2, sort_keys=True) + "\n", 0o600)


def effective_setting(state: dict[str, object], key: str, fallback: str) -> str:
    value = state.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def remember_current_auth(home: Path, auth: dict[str, object], state: dict[str, object]) -> None:
    api_key = auth.get("OPENAI_API_KEY")
    if isinstance(api_key, str) and api_key.strip():
        state["local_api_key"] = api_key.strip()
    state.pop("official_auth", None)


def backup_file(path: Path, backup_dir: Path) -> Path | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{path.name}.{timestamp}.bak"
    counter = 1
    while target.exists():
        target = backup_dir / f"{path.name}.{timestamp}.{counter}.bak"
        counter += 1
    shutil.copy2(path, target)
    return target


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def write_auth(auth_path: Path, auth_mode: str, api_key: str | None) -> None:
    atomic_write(
        auth_path,
        json.dumps({"auth_mode": auth_mode, "OPENAI_API_KEY": api_key}, indent=2) + "\n",
        0o600,
    )


def split_toml_sections(content: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    current = ""
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            sections.append((current, lines))
            current = stripped
            lines = [line]
        else:
            lines.append(line)
    sections.append((current, lines))
    return sections


def set_key(lines: list[str], key: str, value: str) -> list[str]:
    pattern = re.compile(rf"^(\s*){re.escape(key)}\s*=")
    rendered = f'{key} = "{value}"\n'
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = rendered
            return lines
    insert_at = len(lines)
    while insert_at > 0 and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    lines.insert(insert_at, rendered)
    return lines


def set_bool_key(lines: list[str], key: str, value: bool) -> list[str]:
    pattern = re.compile(rf"^(\s*){re.escape(key)}\s*=")
    rendered = f"{key} = {'true' if value else 'false'}\n"
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = rendered
            return lines
    insert_at = len(lines)
    while insert_at > 0 and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    lines.insert(insert_at, rendered)
    return lines


def ensure_custom_provider(sections: list[tuple[str, list[str]]], base_url: str) -> list[tuple[str, list[str]]]:
    updated: list[tuple[str, list[str]]] = []
    found = False
    for section, lines in sections:
        if section == "[model_providers.custom]":
            found = True
            lines = set_key(lines, "base_url", base_url)
            lines = set_key(lines, "name", "custom")
            lines = set_bool_key(lines, "requires_openai_auth", True)
            lines = set_key(lines, "wire_api", "responses")
        updated.append((section, lines))
    if not found:
        lines = [
            "\n" if updated and updated[-1][1] and updated[-1][1][-1].strip() else "",
            "[model_providers.custom]\n",
            f'base_url = "{base_url}"\n',
            'name = "custom"\n',
            "requires_openai_auth = true\n",
            'wire_api = "responses"\n',
        ]
        updated.append(("[model_providers.custom]", lines))
    return updated


def rewrite_config_for_local(content: str, base_url: str, model: str) -> str:
    sections = split_toml_sections(content)
    rewritten: list[tuple[str, list[str]]] = []
    for section, lines in sections:
        if section == "":
            lines = set_key(lines, "model_provider", "custom")
            lines = set_key(lines, "model", model)
            lines = set_key(lines, "preferred_auth_method", "apikey")
        rewritten.append((section, lines))
    rewritten = ensure_custom_provider(rewritten, base_url)
    return "".join(line for _, lines in rewritten for line in lines)


def rewrite_config_for_official(content: str, model: str) -> str:
    sections = split_toml_sections(content)
    rewritten: list[tuple[str, list[str]]] = []
    for section, lines in sections:
        if section == "":
            lines = set_key(lines, "model_provider", "openai")
            lines = set_key(lines, "model", model)
            lines = set_key(lines, "preferred_auth_method", "chatgpt")
        rewritten.append((section, lines))
    return "".join(line for _, lines in rewritten for line in lines)


def read_config(config_path: Path) -> str:
    if not config_path.exists():
        return ""
    return config_path.read_text(encoding="utf-8")


def switch_local(args: argparse.Namespace) -> int:
    home = codex_home()
    auth_path = home / "auth.json"
    config_path = home / "config.toml"
    backup_dir = home / "backups"

    auth = load_auth(auth_path)
    state = load_state(home)
    remember_current_auth(home, auth, state)
    base_url = args.base_url or effective_setting(state, "local_base_url", DEFAULT_BASE_URL)
    model = args.model or effective_setting(state, "local_model", DEFAULT_MODEL)
    cached_key = state.get("local_api_key")
    api_key = args.api_key or str(auth.get("OPENAI_API_KEY") or "").strip()
    if not api_key and isinstance(cached_key, str):
        api_key = cached_key.strip()
    if not api_key:
        raise SwitchError("No API key found. Re-run with --api-key, or login once with `codex login --with-api-key`.")
    state["local_api_key"] = api_key

    backup_file(auth_path, backup_dir)
    backup_file(config_path, backup_dir)
    save_state(home, state)
    write_auth(auth_path, "apikey", api_key)
    config = rewrite_config_for_local(read_config(config_path), base_url, model)
    atomic_write(config_path, config, 0o600)

    print("Switched Codex to local relay API mode.")
    print(f"codex_home: {home}")
    print(f"base_url: {base_url}")
    print(f"model: {model}")
    print(f"api_key: {redacted_key(api_key)}")
    print(f"backup_dir: {backup_dir}")
    return 0


def switch_official(args: argparse.Namespace) -> int:
    home = codex_home()
    auth_path = home / "auth.json"
    config_path = home / "config.toml"
    backup_dir = home / "backups"

    auth = load_auth(auth_path)
    state = load_state(home)
    remember_current_auth(home, auth, state)
    model = args.model or effective_setting(state, "official_model", DEFAULT_OFFICIAL_MODEL)
    official_auth = {"auth_mode": "chatgpt", "OPENAI_API_KEY": None}

    backup_file(auth_path, backup_dir)
    backup_file(config_path, backup_dir)
    save_state(home, state)
    atomic_write(auth_path, json.dumps(official_auth, indent=2, sort_keys=True) + "\n", 0o600)
    config = rewrite_config_for_official(read_config(config_path), model)
    atomic_write(config_path, config, 0o600)

    print("Switched Codex to official ChatGPT login mode.")
    print(f"codex_home: {home}")
    print("auth_mode: chatgpt")
    print("model_provider: openai")
    print(f"model: {model}")
    print(f"backup_dir: {backup_dir}")
    print("If Codex asks you to sign in, run: codex login")
    return 0


def status(_: argparse.Namespace) -> int:
    home = codex_home()
    auth_path = home / "auth.json"
    config_path = home / "config.toml"
    auth = load_auth(auth_path)
    config = read_config(config_path)

    print(f"codex_home: {home}")
    print(f"auth_mode: {auth.get('auth_mode') or '(missing)'}")
    print(f"api_key: {redacted_key(auth.get('OPENAI_API_KEY') if isinstance(auth.get('OPENAI_API_KEY'), str) else None)}")
    for key in ("model_provider", "model", "preferred_auth_method"):
        match = re.search(rf"^{re.escape(key)}\s*=\s*\"([^\"]*)\"", config, re.MULTILINE)
        print(f"{key}: {match.group(1) if match else '(missing)'}")
    base_url = re.search(
        r"^\[model_providers\.custom\][\s\S]*?^base_url\s*=\s*\"([^\"]*)\"",
        config,
        re.MULTILINE,
    )
    print(f"custom.base_url: {base_url.group(1) if base_url else '(missing)'}")
    return 0


def config_show(_: argparse.Namespace) -> int:
    home = codex_home()
    state = load_state(home)
    print(f"codex_home: {home}")
    print(f"local_base_url: {effective_setting(state, 'local_base_url', DEFAULT_BASE_URL)}")
    print(f"local_model: {effective_setting(state, 'local_model', DEFAULT_MODEL)}")
    print(f"official_model: {effective_setting(state, 'official_model', DEFAULT_OFFICIAL_MODEL)}")
    return 0


def config_set(args: argparse.Namespace) -> int:
    home = codex_home()
    state = load_state(home)
    updates = {
        "local_base_url": args.local_base_url,
        "local_model": args.local_model,
        "official_model": args.official_model,
    }
    for key, value in updates.items():
        if value is not None:
            stripped = value.strip()
            if not stripped:
                raise SwitchError(f"{key} cannot be empty")
            state[key] = stripped
    save_state(home, state)
    print("Saved Codex Switch settings.")
    return config_show(args)


def prompt_api_key(args: argparse.Namespace) -> int:
    key = getpass.getpass("OpenAI/local relay API key: ").strip()
    if not key:
        raise SwitchError("API key was empty.")
    args.api_key = key
    return switch_local(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-switch",
        description="Switch Codex between official ChatGPT login and local relay OpenAI-compatible API mode.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    local = subparsers.add_parser("local", help="Use local relay API mode.")
    local.add_argument("--api-key", help="API key to store in auth.json. Omit to reuse existing key.")
    local.add_argument("--base-url", help=f"Local relay API base URL. Default: saved setting or {DEFAULT_BASE_URL}")
    local.add_argument("--model", help=f"Model name. Default: saved setting or {DEFAULT_MODEL}")
    local.set_defaults(func=switch_local)

    local_login = subparsers.add_parser("local-login", help="Prompt for an API key, then switch to local relay API mode.")
    local_login.add_argument("--base-url", help=f"Local relay API base URL. Default: saved setting or {DEFAULT_BASE_URL}")
    local_login.add_argument("--model", help=f"Model name. Default: saved setting or {DEFAULT_MODEL}")
    local_login.set_defaults(func=prompt_api_key, api_key=None)

    official = subparsers.add_parser("official", help="Use official ChatGPT login mode.")
    official.add_argument("--model", help=f"Official Codex model. Default: saved setting or {DEFAULT_OFFICIAL_MODEL}")
    official.set_defaults(func=switch_official)

    status_parser = subparsers.add_parser("status", help="Show current Codex switch-relevant state.")
    status_parser.set_defaults(func=status)

    config_parser = subparsers.add_parser("config", help="Show or update Codex Switch defaults.")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    config_show_parser = config_subparsers.add_parser("show", help="Show saved switch defaults.")
    config_show_parser.set_defaults(func=config_show)
    config_set_parser = config_subparsers.add_parser("set", help="Update saved switch defaults.")
    config_set_parser.add_argument("--local-base-url", help="Default local relay API base URL.")
    config_set_parser.add_argument("--local-model", help="Default local model.")
    config_set_parser.add_argument("--official-model", help="Default official Codex model.")
    config_set_parser.set_defaults(func=config_set)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SwitchError as exc:
        print(f"codex-switch: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
