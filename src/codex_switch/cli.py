#!/usr/bin/env python3
"""Switch Codex auth/config between ChatGPT login and local Responses API."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path


DEFAULT_BASE_URL = "https://jp.icodeeasy.cc"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_OFFICIAL_MODEL = "gpt-5.5"
CLAUDE_DEFAULT_BASE_URL = "http://127.0.0.1:15721"
CLAUDE_DEFAULT_MODEL = "claude-sonnet-4-6"
CLAUDE_DEFAULT_OFFICIAL_MODEL = "claude-sonnet-4-6"
RESTART_SETTLE_SECONDS = 3.0
CONFIG_KEYS = ("local_base_url", "local_model", "official_model")
CUSTOM_PROVIDER_ID = "custom"
MODEL_CATALOG_NAME = "codex-switch-model-catalog.json"


class SwitchError(RuntimeError):
    pass


@dataclass
class RolloutSyncInfo:
    changed: bool = False
    thread_id: str | None = None
    cwd: str | None = None
    has_user_event: bool = False


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()


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


def load_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SwitchError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SwitchError(f"Expected {path} to contain a JSON object")
    return data


def state_path(home: Path) -> Path:
    return home / "codex-switch-state.json"


def claude_state_path(home: Path) -> Path:
    return home / "claude-switch-state.json"


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


def load_claude_state(home: Path) -> dict[str, object]:
    path = claude_state_path(home)
    if not path.exists():
        return {}
    data = load_json_object(path)
    return data


def save_claude_state(home: Path, state: dict[str, object]) -> None:
    atomic_write(claude_state_path(home), json.dumps(state, indent=2, sort_keys=True) + "\n", 0o600)


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


def write_auth(auth_path: Path, auth: dict[str, object]) -> None:
    atomic_write(auth_path, json.dumps(auth, indent=2, sort_keys=True) + "\n", 0o600)


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


def set_raw_key(lines: list[str], key: str, rendered_value: str) -> list[str]:
    pattern = re.compile(rf"^(\s*){re.escape(key)}\s*=")
    rendered = f"{key} = {rendered_value}\n"
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = rendered
            return lines
    insert_at = len(lines)
    while insert_at > 0 and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    lines.insert(insert_at, rendered)
    return lines


def quoted_array(values: list[str]) -> str:
    return "[" + ", ".join(json.dumps(value) for value in values) + "]"


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


def remove_key(lines: list[str], key: str) -> list[str]:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    return [line for line in lines if not pattern.match(line)]


def ensure_custom_provider(sections: list[tuple[str, list[str]]], base_url: str, api_key: str, models: list[str]) -> list[tuple[str, list[str]]]:
    updated: list[tuple[str, list[str]]] = []
    found = False
    for section, lines in sections:
        if section == f"[model_providers.{CUSTOM_PROVIDER_ID}]":
            found = True
            lines = set_key(lines, "base_url", base_url)
            lines = set_key(lines, "name", CUSTOM_PROVIDER_ID)
            lines = set_bool_key(lines, "requires_openai_auth", True)
            lines = set_key(lines, "wire_api", "responses")
            lines = set_key(lines, "experimental_bearer_token", api_key)
            lines = set_raw_key(lines, "models", quoted_array(models))
        updated.append((section, lines))
    if not found:
        lines = [
            "\n" if updated and updated[-1][1] and updated[-1][1][-1].strip() else "",
            f"[model_providers.{CUSTOM_PROVIDER_ID}]\n",
            f'base_url = "{base_url}"\n',
            f'name = "{CUSTOM_PROVIDER_ID}"\n',
            "requires_openai_auth = true\n",
            'wire_api = "responses"\n',
            f'experimental_bearer_token = "{api_key}"\n',
            f"models = {quoted_array(models)}\n",
        ]
        updated.append((f"[model_providers.{CUSTOM_PROVIDER_ID}]", lines))
    return updated


def rewrite_config_for_local(content: str, base_url: str, model: str, api_key: str, catalog_path: Path) -> str:
    sections = split_toml_sections(content)
    rewritten: list[tuple[str, list[str]]] = []
    for section, lines in sections:
        if section == "":
            lines = set_key(lines, "model_provider", CUSTOM_PROVIDER_ID)
            lines = set_key(lines, "model", model)
            lines = set_key(lines, "preferred_auth_method", "chatgpt")
            lines = set_key(lines, "model_catalog_json", str(catalog_path))
        rewritten.append((section, lines))
    rewritten = ensure_custom_provider(rewritten, base_url, api_key, [model])
    return "".join(line for _, lines in rewritten for line in lines)


def rewrite_config_for_official(content: str, model: str) -> str:
    sections = split_toml_sections(content)
    rewritten: list[tuple[str, list[str]]] = []
    for section, lines in sections:
        if section == "":
            lines = set_key(lines, "model_provider", "openai")
            lines = set_key(lines, "model", model)
            lines = set_key(lines, "preferred_auth_method", "chatgpt")
            lines = remove_key(lines, "model_catalog_json")
        elif section == f"[model_providers.{CUSTOM_PROVIDER_ID}]":
            lines = remove_key(lines, "experimental_bearer_token")
        rewritten.append((section, lines))
    return "".join(line for _, lines in rewritten for line in lines)


def rewrite_config_for_parallel(
    content: str,
    base_url: str,
    official_model: str,
    custom_model: str,
    api_key: str,
    catalog_path: Path,
) -> str:
    sections = split_toml_sections(content)
    rewritten: list[tuple[str, list[str]]] = []
    for section, lines in sections:
        if section == "":
            lines = set_key(lines, "model_provider", "openai")
            lines = set_key(lines, "model", official_model)
            lines = set_key(lines, "preferred_auth_method", "chatgpt")
            lines = set_key(lines, "model_catalog_json", str(catalog_path))
        rewritten.append((section, lines))
    rewritten = ensure_custom_provider(rewritten, base_url, api_key, [custom_model])
    return "".join(line for _, lines in rewritten for line in lines)


def read_config(config_path: Path) -> str:
    if not config_path.exists():
        return ""
    return config_path.read_text(encoding="utf-8")


def configured_provider(content: str) -> str:
    for section, lines in split_toml_sections(content):
        if section != "":
            continue
        for line in lines:
            match = re.match(r'^\s*model_provider\s*=\s*"([^"]+)"', line)
            if match:
                return match.group(1)
    return "openai"


def model_catalog_path(home: Path) -> Path:
    return home / MODEL_CATALOG_NAME


def custom_model_entry(model: str, display_name: str) -> dict[str, object]:
    return {
        "slug": model,
        "display_name": display_name,
        "description": "Custom model registered by Codex Switch",
        "supported_in_api": True,
        "context_window": 200000,
        "max_output_tokens": 100000,
        "supports_reasoning_summaries": True,
        "supported_reasoning_levels": [
            {"effort": "low", "description": "Fast responses with lighter reasoning"},
            {"effort": "medium", "description": "Balances speed and reasoning depth for everyday tasks"},
            {"effort": "high", "description": "Greater reasoning depth for complex problems"},
        ],
        "default_reasoning_level": "medium",
        "supports_parallel_tool_calls": True,
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
    }


def base_catalog_models(home: Path, custom_model: str) -> list[dict[str, object]]:
    source = home / "models_cache.json"
    if not source.exists():
        return []
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    models = data.get("models")
    if not isinstance(models, list):
        return []
    output: list[dict[str, object]] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or slug == custom_model:
            continue
        output.append(dict(item))
    return output


def custom_model_catalog(
    home: Path,
    model: str,
    display_name: str,
    additional_models: list[tuple[str, str]] | None = None,
) -> dict[str, object]:
    models = base_catalog_models(home, model)
    seen = {item.get("slug") for item in models if isinstance(item.get("slug"), str)}
    for slug, name in additional_models or []:
        if slug and slug not in seen and slug != model:
            models.append(custom_model_entry(slug, name or slug))
            seen.add(slug)
    models.append(custom_model_entry(model, display_name))
    return {"models": models}


def write_model_catalog(
    path: Path,
    model: str,
    display_name: str | None = None,
    additional_models: list[tuple[str, str]] | None = None,
) -> None:
    atomic_write(
        path,
        json.dumps(
            custom_model_catalog(path.parent, model, display_name or model, additional_models),
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        0o600,
    )


def rollout_files(home: Path) -> list[Path]:
    roots = [home / "sessions", home / "archived_sessions"]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(root.rglob("rollout-*.jsonl"))
    return sorted(files)


def rewrite_rollout_provider(path: Path, provider: str) -> RolloutSyncInfo:
    info = RolloutSyncInfo()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return info
    info.has_user_event = '\"user_message\"' in text or '\"user_input\"' in text
    output: list[str] = []
    for segment in text.splitlines(keepends=True):
        line = segment[:-1] if segment.endswith("\n") else segment
        ending = "\n" if segment.endswith("\n") else ""
        if line.endswith("\r"):
            line = line[:-1]
            ending = "\r\n"
        next_line = line
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            output.append(segment)
            continue
        if record.get("type") == "session_meta" and isinstance(record.get("payload"), dict):
            payload = record["payload"]
            if info.thread_id is None and isinstance(payload.get("id"), str):
                info.thread_id = payload["id"]
            if info.cwd is None and isinstance(payload.get("cwd"), str):
                info.cwd = payload["cwd"]
            if payload.get("model_provider") != provider:
                payload["model_provider"] = provider
                next_line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                info.changed = True
        output.append(next_line + ending)
    if info.changed:
        original_stat = path.stat()
        atomic_write(path, "".join(output), stat.S_IMODE(original_stat.st_mode) or 0o600)
        os.utime(path, (original_stat.st_atime, original_stat.st_mtime))
    return info


def provider_sync_backup(home: Path) -> Path:
    backup_dir = home / "backups_state" / "provider-sync" / datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in ("config.toml", ".codex-global-state.json"):
        source = home / name
        if source.exists():
            shutil.copy2(source, backup_dir / name)
    return backup_dir


def sqlite_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def sync_sqlite_provider(home: Path, provider: str, rollouts: list[RolloutSyncInfo]) -> int:
    databases = list((home / "sqlite").glob("state_*.sqlite"))
    databases.extend(home.glob("state_*.sqlite"))
    updated = 0
    user_event_thread_ids = {info.thread_id for info in rollouts if info.thread_id and info.has_user_event}
    cwd_by_thread_id = {info.thread_id: info.cwd for info in rollouts if info.thread_id and info.cwd}
    for database in databases:
        try:
            connection = sqlite3.connect(database)
            try:
                columns = sqlite_columns(connection, "threads")
                if "model_provider" not in columns:
                    continue
                cursor = connection.execute(
                    "UPDATE threads SET model_provider = ? WHERE COALESCE(model_provider, '') <> ?",
                    (provider, provider),
                )
                updated += cursor.rowcount if cursor.rowcount is not None else 0
                if "has_user_event" in columns:
                    for thread_id in user_event_thread_ids:
                        cursor = connection.execute(
                            "UPDATE threads SET has_user_event = 1 WHERE id = ? AND COALESCE(has_user_event, 0) <> 1",
                            (thread_id,),
                        )
                        updated += cursor.rowcount if cursor.rowcount is not None else 0
                if "cwd" in columns:
                    for thread_id, cwd in cwd_by_thread_id.items():
                        cursor = connection.execute(
                            "UPDATE threads SET cwd = ? WHERE id = ? AND COALESCE(cwd, '') <> ?",
                            (cwd, thread_id, cwd),
                        )
                        updated += cursor.rowcount if cursor.rowcount is not None else 0
                connection.commit()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise SwitchError(f"Could not sync thread provider in {database}: {exc}") from exc
    return updated


def sync_provider_metadata(home: Path, provider: str) -> tuple[int, int, Path]:
    backup_dir = provider_sync_backup(home)
    rollouts = [rewrite_rollout_provider(path, provider) for path in rollout_files(home)]
    changed_rollouts = sum(1 for info in rollouts if info.changed)
    sqlite_rows = sync_sqlite_provider(home, provider, rollouts)
    return changed_rollouts, sqlite_rows, backup_dir


def print_migration(home: Path, provider: str, _model: str, args: argparse.Namespace) -> None:
    if not getattr(args, "migrate_latest", False):
        return
    changed_rollouts, sqlite_rows, backup_dir = sync_provider_metadata(home, provider)
    print(f"Provider-synced existing thread context to {provider}.")
    print(f"changed_session_files: {changed_rollouts}")
    print(f"sqlite_rows_updated: {sqlite_rows}")
    print(f"provider_sync_backup: {backup_dir}")


def restart_codex(args: argparse.Namespace, home: Path, expected_provider: str) -> None:
    if not getattr(args, "restart_codex", False):
        return
    subprocess.run(
        ["osascript", "-e", 'tell application "Codex" to quit'],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)
    subprocess.run(["open", "-a", "Codex"], check=True)
    time.sleep(RESTART_SETTLE_SECONDS)
    actual_provider = configured_provider(read_config(home / "config.toml"))
    if actual_provider != expected_provider:
        repair_summary = ""
        if getattr(args, "migrate_latest", False):
            changed_rollouts, sqlite_rows, backup_dir = sync_provider_metadata(home, actual_provider)
            repair_summary = (
                f" Restored thread metadata to {actual_provider} "
                f"({changed_rollouts} session files, {sqlite_rows} database rows; backup: {backup_dir})."
            )
        raise SwitchError(
            f"Codex restarted with provider {actual_provider}, not {expected_provider}."
            f"{repair_summary}"
        )
    print("Restarted Codex.app to reload the selected provider.")


def switch_local(args: argparse.Namespace) -> int:
    home = codex_home()
    auth_path = home / "auth.json"
    config_path = home / "config.toml"
    catalog_path = model_catalog_path(home)
    backup_dir = home / "backups"

    auth = load_auth(auth_path)
    state = load_state(home)
    remember_current_auth(home, auth, state)
    base_url = args.base_url or effective_setting(state, "local_base_url", DEFAULT_BASE_URL)
    model = args.model or effective_setting(state, "local_model", DEFAULT_MODEL)
    cached_key = state.get("local_api_key")
    stdin_key = ""
    if getattr(args, "api_key_stdin", False):
        stdin_key = sys.stdin.readline().strip()
        if not stdin_key:
            raise SwitchError("API key from stdin was empty.")
    api_key = stdin_key or args.api_key or str(auth.get("OPENAI_API_KEY") or "").strip()
    if not api_key and isinstance(cached_key, str):
        api_key = cached_key.strip()
    if not api_key:
        raise SwitchError("No API key found. Re-run with --api-key, or login once with `codex login --with-api-key`.")
    state["local_api_key"] = api_key

    backup_file(auth_path, backup_dir)
    backup_file(config_path, backup_dir)
    backup_file(catalog_path, backup_dir)
    save_state(home, state)
    auth["OPENAI_API_KEY"] = api_key
    auth["auth_mode"] = "chatgpt"
    write_auth(auth_path, auth)
    write_model_catalog(catalog_path, model)
    config = rewrite_config_for_local(read_config(config_path), base_url, model, api_key, catalog_path)
    atomic_write(config_path, config, 0o600)

    print("Switched Codex to local relay API mode.")
    print(f"codex_home: {home}")
    print(f"base_url: {base_url}")
    print(f"model: {model}")
    print(f"model_catalog_json: {catalog_path}")
    print(f"api_key: {redacted_key(api_key)}")
    print(f"backup_dir: {backup_dir}")
    print_migration(home, "custom", model, args)
    restart_codex(args, home, "custom")
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
    auth["auth_mode"] = "chatgpt"

    backup_file(auth_path, backup_dir)
    backup_file(config_path, backup_dir)
    save_state(home, state)
    write_auth(auth_path, auth)
    config = rewrite_config_for_official(read_config(config_path), model)
    atomic_write(config_path, config, 0o600)

    print("Switched Codex to official ChatGPT login mode.")
    print(f"codex_home: {home}")
    print("auth_mode: chatgpt")
    print("model_provider: openai")
    print(f"model: {model}")
    print(f"backup_dir: {backup_dir}")
    print("Existing ChatGPT login tokens, custom API key, and custom model catalog were preserved.")
    print_migration(home, "openai", model, args)
    restart_codex(args, home, "openai")
    return 0


def configure_codex(args: argparse.Namespace) -> int:
    home = codex_home()
    auth_path = home / "auth.json"
    config_path = home / "config.toml"
    catalog_path = model_catalog_path(home)
    backup_dir = home / "backups"

    auth = load_auth(auth_path)
    state = load_state(home)
    remember_current_auth(home, auth, state)
    base_url = args.base_url or effective_setting(state, "local_base_url", DEFAULT_BASE_URL)
    custom_model = args.custom_model or args.model or effective_setting(state, "local_model", DEFAULT_MODEL)
    display_name = args.custom_model_name or effective_setting(state, "local_model_display_name", custom_model)
    official_model = args.official_model or effective_setting(state, "official_model", DEFAULT_OFFICIAL_MODEL)
    cached_key = state.get("local_api_key")
    stdin_key = ""
    if getattr(args, "api_key_stdin", False):
        stdin_key = sys.stdin.readline().strip()
        if not stdin_key:
            raise SwitchError("API key from stdin was empty.")
    api_key = stdin_key or args.api_key or str(auth.get("OPENAI_API_KEY") or "").strip()
    if not api_key and isinstance(cached_key, str):
        api_key = cached_key.strip()
    if not api_key:
        raise SwitchError("No API key found. Re-run with --api-key, or login once with `codex login --with-api-key`.")

    backup_file(auth_path, backup_dir)
    backup_file(config_path, backup_dir)
    backup_file(catalog_path, backup_dir)
    state["local_api_key"] = api_key
    state["local_base_url"] = base_url
    state["local_model"] = custom_model
    state["local_model_display_name"] = display_name
    state["official_model"] = official_model
    save_state(home, state)

    auth["OPENAI_API_KEY"] = api_key
    auth["auth_mode"] = "chatgpt"
    write_auth(auth_path, auth)
    write_model_catalog(catalog_path, custom_model, display_name, [(official_model, official_model)])
    config = rewrite_config_for_parallel(read_config(config_path), base_url, official_model, custom_model, api_key, catalog_path)
    atomic_write(config_path, config, 0o600)

    print("Configured Codex official and custom providers in parallel.")
    print(f"codex_home: {home}")
    print("model_provider: openai")
    print(f"official_model: {official_model}")
    print(f"custom.base_url: {base_url}")
    print(f"custom_model: {custom_model}")
    print(f"custom_model_name: {display_name}")
    print(f"model_catalog_json: {catalog_path}")
    print(f"api_key: {redacted_key(api_key)}")
    print(f"backup_dir: {backup_dir}")
    restart_codex(args, home, "openai")
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
    for key in ("model_provider", "model", "preferred_auth_method", "model_catalog_json"):
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
    print(f"local_model_display_name: {effective_setting(state, 'local_model_display_name', effective_setting(state, 'local_model', DEFAULT_MODEL))}")
    print(f"official_model: {effective_setting(state, 'official_model', DEFAULT_OFFICIAL_MODEL)}")
    return 0


def config_set(args: argparse.Namespace) -> int:
    home = codex_home()
    state = load_state(home)
    updates = {
        "local_base_url": args.local_base_url,
        "local_model": args.local_model,
        "local_model_display_name": args.local_model_display_name,
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


def codex_register_model(args: argparse.Namespace) -> int:
    home = codex_home()
    state = load_state(home)
    model = args.model.strip()
    if not model:
        raise SwitchError("model cannot be empty")
    display_name = args.name.strip() if args.name else model
    if not display_name:
        raise SwitchError("model display name cannot be empty")
    catalog_path = model_catalog_path(home)
    backup_file(catalog_path, home / "backups")
    state["local_model"] = model
    state["local_model_display_name"] = display_name
    save_state(home, state)
    write_model_catalog(catalog_path, model, display_name)
    print("Registered custom Codex model catalog.")
    print(f"codex_home: {home}")
    print(f"model: {model}")
    print(f"custom_model_name: {display_name}")
    print(f"model_catalog_json: {catalog_path}")
    print("Switch to custom API mode to write this path into config.toml.")
    return 0


def load_claude_settings(settings_path: Path) -> dict[str, object]:
    return load_json_object(settings_path)


def save_claude_settings(settings_path: Path, settings: dict[str, object]) -> None:
    mode = 0o600
    if settings_path.exists():
        mode = stat.S_IMODE(settings_path.stat().st_mode) or 0o600
    atomic_write(settings_path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n", mode)


def claude_env(settings: dict[str, object]) -> dict[str, object]:
    env = settings.get("env")
    if isinstance(env, dict):
        return env
    env = {}
    settings["env"] = env
    return env


def remember_current_claude(settings: dict[str, object], state: dict[str, object]) -> None:
    env = claude_env(settings)
    token = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")
    if isinstance(token, str) and token.strip():
        state["local_api_key"] = token.strip()
    base_url = env.get("ANTHROPIC_BASE_URL")
    if isinstance(base_url, str) and base_url.strip():
        state["local_base_url"] = base_url.strip()
    model = env.get("ANTHROPIC_MODEL")
    if isinstance(model, str) and model.strip():
        if env.get("ANTHROPIC_BASE_URL"):
            state["local_model"] = model.strip()
        else:
            state["official_model"] = model.strip()


def claude_model_from_env(env: dict[str, object], fallback: str) -> str:
    for key in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL"):
        value = env.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def switch_claude_local(args: argparse.Namespace) -> int:
    home = claude_home()
    settings_path = home / "settings.json"
    backup_dir = home / "backups"
    settings = load_claude_settings(settings_path)
    state = load_claude_state(home)
    env = claude_env(settings)
    remember_current_claude(settings, state)

    base_url = args.base_url or effective_setting(state, "local_base_url", CLAUDE_DEFAULT_BASE_URL)
    model = args.model or effective_setting(state, "local_model", CLAUDE_DEFAULT_MODEL)
    cached_key = state.get("local_api_key")
    stdin_key = ""
    if getattr(args, "auth_token_stdin", False):
        stdin_key = sys.stdin.readline().strip()
        if not stdin_key:
            raise SwitchError("Claude API token from stdin was empty.")
    api_key = stdin_key or args.auth_token or str(env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key and isinstance(cached_key, str):
        api_key = cached_key.strip()
    if not api_key:
        raise SwitchError("No Claude API token found. Re-run with --auth-token or --auth-token-stdin.")

    backup_file(settings_path, backup_dir)
    state["local_base_url"] = base_url
    state["local_model"] = model
    state["local_api_key"] = api_key
    save_claude_state(home, state)

    env["ANTHROPIC_BASE_URL"] = base_url
    env["ANTHROPIC_AUTH_TOKEN"] = api_key
    env["ANTHROPIC_MODEL"] = model
    env.pop("ANTHROPIC_API_KEY", None)
    save_claude_settings(settings_path, settings)

    print("Switched Claude Code to custom API mode.")
    print(f"claude_home: {home}")
    print(f"base_url: {base_url}")
    print(f"model: {model}")
    print(f"api_key: {redacted_key(api_key)}")
    print(f"backup_dir: {backup_dir}")
    print("Restart Claude Code terminal sessions for the new settings to take effect.")
    return 0


def switch_claude_official(args: argparse.Namespace) -> int:
    home = claude_home()
    settings_path = home / "settings.json"
    backup_dir = home / "backups"
    settings = load_claude_settings(settings_path)
    state = load_claude_state(home)
    env = claude_env(settings)
    remember_current_claude(settings, state)

    model = args.model or effective_setting(state, "official_model", CLAUDE_DEFAULT_OFFICIAL_MODEL)
    backup_file(settings_path, backup_dir)
    state["official_model"] = model
    save_claude_state(home, state)

    env.pop("ANTHROPIC_BASE_URL", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env.pop("ANTHROPIC_API_KEY", None)
    env["ANTHROPIC_MODEL"] = model
    save_claude_settings(settings_path, settings)

    print("Switched Claude Code to official Claude login mode.")
    print(f"claude_home: {home}")
    print("model_provider: official")
    print(f"model: {model}")
    print(f"backup_dir: {backup_dir}")
    print("Saved custom Claude API settings were preserved for later.")
    print("Restart Claude Code terminal sessions for the new settings to take effect.")
    return 0


def claude_status(_: argparse.Namespace) -> int:
    home = claude_home()
    settings = load_claude_settings(home / "settings.json")
    env = claude_env(settings)
    base_url = env.get("ANTHROPIC_BASE_URL")
    token = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")
    provider = "custom" if isinstance(base_url, str) and base_url.strip() else "official"
    auth_mode = "auth_token" if env.get("ANTHROPIC_AUTH_TOKEN") else "api_key" if env.get("ANTHROPIC_API_KEY") else "claude-login"
    print(f"claude_home: {home}")
    print(f"auth_mode: {auth_mode}")
    print(f"api_key: {redacted_key(token if isinstance(token, str) else None)}")
    print(f"model_provider: {provider}")
    print(f"model: {claude_model_from_env(env, CLAUDE_DEFAULT_OFFICIAL_MODEL)}")
    print(f"custom.base_url: {base_url if isinstance(base_url, str) and base_url.strip() else '(missing)'}")
    return 0


def claude_config_show(_: argparse.Namespace) -> int:
    home = claude_home()
    settings = load_claude_settings(home / "settings.json")
    env = claude_env(settings)
    state = load_claude_state(home)
    print(f"claude_home: {home}")
    print(f"local_base_url: {effective_setting(state, 'local_base_url', str(env.get('ANTHROPIC_BASE_URL') or CLAUDE_DEFAULT_BASE_URL))}")
    print(f"local_model: {effective_setting(state, 'local_model', claude_model_from_env(env, CLAUDE_DEFAULT_MODEL))}")
    print(f"official_model: {effective_setting(state, 'official_model', CLAUDE_DEFAULT_OFFICIAL_MODEL)}")
    return 0


def claude_config_set(args: argparse.Namespace) -> int:
    home = claude_home()
    state = load_claude_state(home)
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
    save_claude_state(home, state)
    print("Saved Claude Code Switch settings.")
    return claude_config_show(args)


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
    key_source = local.add_mutually_exclusive_group()
    key_source.add_argument("--api-key", help="API key to store in auth.json. Omit to reuse existing key.")
    key_source.add_argument("--api-key-stdin", action="store_true", help="Read a replacement API key from stdin.")
    local.add_argument("--base-url", help=f"Local relay API base URL. Default: saved setting or {DEFAULT_BASE_URL}")
    local.add_argument("--model", help=f"Model name. Default: saved setting or {DEFAULT_MODEL}")
    local.add_argument("--migrate-latest", action="store_true", help="Sync existing thread metadata to this provider.")
    local.add_argument("--restart-codex", action="store_true", help="Gracefully quit and reopen Codex.app after switching.")
    local.add_argument("--no-open", action="store_true", help=argparse.SUPPRESS)
    local.set_defaults(func=switch_local)

    local_login = subparsers.add_parser("local-login", help="Prompt for an API key, then switch to local relay API mode.")
    local_login.add_argument("--base-url", help=f"Local relay API base URL. Default: saved setting or {DEFAULT_BASE_URL}")
    local_login.add_argument("--model", help=f"Model name. Default: saved setting or {DEFAULT_MODEL}")
    local_login.set_defaults(func=prompt_api_key, api_key=None)

    official = subparsers.add_parser("official", help="Use official ChatGPT login mode.")
    official.add_argument("--model", help=f"Official Codex model. Default: saved setting or {DEFAULT_OFFICIAL_MODEL}")
    official.add_argument("--migrate-latest", action="store_true", help="Sync existing thread metadata to this provider.")
    official.add_argument("--restart-codex", action="store_true", help="Gracefully quit and reopen Codex.app after switching.")
    official.add_argument("--no-open", action="store_true", help=argparse.SUPPRESS)
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
    config_set_parser.add_argument("--local-model-display-name", help="Display name for the custom Codex model.")
    config_set_parser.add_argument("--official-model", help="Default official Codex model.")
    config_set_parser.set_defaults(func=config_set)

    configure = subparsers.add_parser("configure", help="Configure Codex official and custom providers in parallel.")
    configure_key_source = configure.add_mutually_exclusive_group()
    configure_key_source.add_argument("--api-key", help="API key for the custom provider. Omit to reuse existing key.")
    configure_key_source.add_argument("--api-key-stdin", action="store_true", help="Read a replacement API key from stdin.")
    configure.add_argument("--base-url", help=f"Custom API base URL. Default: saved setting or {DEFAULT_BASE_URL}")
    configure.add_argument("--model", help=argparse.SUPPRESS)
    configure.add_argument("--custom-model", help=f"Custom model slug. Default: saved setting or {DEFAULT_MODEL}")
    configure.add_argument("--custom-model-name", help="Display name for the custom model.")
    configure.add_argument("--official-model", help=f"Official Codex default model. Default: saved setting or {DEFAULT_OFFICIAL_MODEL}")
    configure.add_argument("--restart-codex", action="store_true", help="Gracefully quit and reopen Codex.app after saving.")
    configure.set_defaults(func=configure_codex)

    register_model_parser = subparsers.add_parser("register-model", help="Write a Codex model catalog for a custom model.")
    register_model_parser.add_argument("model", help="Custom model slug to expose to Codex.")
    register_model_parser.add_argument("--name", help="Display name for the custom model.")
    register_model_parser.set_defaults(func=codex_register_model)

    claude_local = subparsers.add_parser("claude-local", help="Use Claude Code custom API mode.")
    claude_key_source = claude_local.add_mutually_exclusive_group()
    claude_key_source.add_argument("--auth-token", help="Claude-compatible API token. Omit to reuse existing token.")
    claude_key_source.add_argument("--auth-token-stdin", action="store_true", help="Read a replacement Claude API token from stdin.")
    claude_local.add_argument("--base-url", help=f"Claude API base URL. Default: saved setting or {CLAUDE_DEFAULT_BASE_URL}")
    claude_local.add_argument("--model", help=f"Claude model name. Default: saved setting or {CLAUDE_DEFAULT_MODEL}")
    claude_local.set_defaults(func=switch_claude_local)

    claude_official = subparsers.add_parser("claude-official", help="Use Claude Code official Claude login mode.")
    claude_official.add_argument("--model", help=f"Official Claude model. Default: saved setting or {CLAUDE_DEFAULT_OFFICIAL_MODEL}")
    claude_official.set_defaults(func=switch_claude_official)

    claude_status_parser = subparsers.add_parser("claude-status", help="Show current Claude Code switch-relevant state.")
    claude_status_parser.set_defaults(func=claude_status)

    claude_config_parser = subparsers.add_parser("claude-config", help="Show or update Claude Code Switch defaults.")
    claude_config_subparsers = claude_config_parser.add_subparsers(dest="claude_config_command", required=True)
    claude_config_show_parser = claude_config_subparsers.add_parser("show", help="Show saved Claude Code Switch defaults.")
    claude_config_show_parser.set_defaults(func=claude_config_show)
    claude_config_set_parser = claude_config_subparsers.add_parser("set", help="Update saved Claude Code Switch defaults.")
    claude_config_set_parser.add_argument("--local-base-url", help="Default Claude API base URL.")
    claude_config_set_parser.add_argument("--local-model", help="Default Claude custom model.")
    claude_config_set_parser.add_argument("--official-model", help="Default official Claude model.")
    claude_config_set_parser.set_defaults(func=claude_config_set)

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
