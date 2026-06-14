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
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_BASE_URL = "https://jp.icodeeasy.cc"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_OFFICIAL_MODEL = "gpt-5.5"
CONFIG_KEYS = ("local_base_url", "local_model", "official_model")
SESSION_SNAPSHOT_FILES = ("session_index.jsonl", ".codex-global-state.json")
SESSION_DIRS = ("sessions", "archived_sessions")
BASE_URL_ENV_KEYS = ("CODEX_SWITCH_LOCAL_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE")


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


def first_env_setting(keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    return None


def effective_local_base_url(state: dict[str, object], config: str = "") -> str:
    value = state.get("local_base_url")
    if isinstance(value, str) and value.strip():
        return value.strip()
    configured = config_custom_base_url(config)
    if configured:
        return configured
    env_value = first_env_setting(BASE_URL_ENV_KEYS)
    return env_value or DEFAULT_BASE_URL


def remember_current_auth(home: Path, auth: dict[str, object], state: dict[str, object]) -> None:
    api_key = auth.get("OPENAI_API_KEY")
    if isinstance(api_key, str) and api_key.strip():
        state["local_api_key"] = api_key.strip()
    state.pop("official_auth", None)


def backup_file(path: Path, backup_dir: Path, timestamp: str | None = None) -> Path | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
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


def snapshot_session_state(home: Path, backup_dir: Path | None = None) -> list[Path]:
    backup_dir = backup_dir or home / "backups"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    copied: list[Path] = []
    for name in SESSION_SNAPSHOT_FILES:
        backup = backup_file(home / name, backup_dir, timestamp)
        if backup is not None:
            copied.append(backup)
    return copied


def print_session_snapshot(copied: list[Path]) -> None:
    if copied:
        print("session_snapshot:")
        for path in copied:
            print(f"  {path}")
    else:
        print("session_snapshot: (no session index/global state found)")


def parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def render_iso_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def file_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def read_session_index(index_path: Path) -> dict[str, dict[str, object]]:
    if not index_path.exists():
        return {}
    entries: dict[str, dict[str, object]] = {}
    with index_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            session_id = entry.get("id")
            if isinstance(session_id, str) and session_id.strip():
                entries[session_id.strip()] = entry
    return entries


def iter_session_files(home: Path) -> list[Path]:
    files: list[Path] = []
    for name in SESSION_DIRS:
        root = home / name
        if root.is_file() and root.suffix == ".jsonl":
            files.append(root)
        elif root.is_dir():
            files.extend(path for path in root.rglob("*.jsonl") if path.is_file())
    return sorted(files)


def newest_datetime(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def parse_session_file(path: Path) -> dict[str, object] | None:
    session_id: str | None = None
    cwd: str | None = None
    updated_at: datetime | None = None

    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None

    with handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue

            payload = event.get("payload")
            payload_dict = payload if isinstance(payload, dict) else {}
            if event.get("type") == "session_meta" or "cwd" in payload_dict:
                candidate_id = payload_dict.get("id")
                if session_id is None and isinstance(candidate_id, str) and candidate_id.strip():
                    session_id = candidate_id.strip()
                candidate_cwd = payload_dict.get("cwd")
                if cwd is None and isinstance(candidate_cwd, str) and candidate_cwd.strip():
                    cwd = candidate_cwd.strip()

            for source in (event, payload_dict):
                for key in ("updated_at", "timestamp"):
                    updated_at = newest_datetime(updated_at, parse_iso_datetime(source.get(key)))

    if session_id is None:
        return None
    if updated_at is None:
        updated_at = file_mtime(path)
    return {"id": session_id, "cwd": cwd, "updated_at": updated_at, "path": path}


def fallback_thread_name(cwd: object, session_id: str) -> str:
    if isinstance(cwd, str) and cwd.strip():
        name = Path(cwd.strip()).name
        if name:
            return name
    return f"Untitled {session_id[:8]}"


def normalized_index_entry(entry: dict[str, object], session_id: str) -> dict[str, object]:
    thread_name = entry.get("thread_name")
    if not isinstance(thread_name, str) or not thread_name.strip():
        thread_name = fallback_thread_name(None, session_id)
    updated_at = entry.get("updated_at")
    if not isinstance(updated_at, str) or not updated_at.strip():
        updated_at = render_iso_datetime(datetime.fromtimestamp(0, tz=timezone.utc))
    return {"id": session_id, "thread_name": thread_name.strip(), "updated_at": updated_at.strip()}


def rebuild_session_index(home: Path) -> dict[str, object]:
    index_path = home / "session_index.jsonl"
    backup = backup_file(index_path, home / "backups")
    existing = read_session_index(index_path)
    discovered: dict[str, dict[str, object]] = {}
    scanned = 0

    for path in iter_session_files(home):
        scanned += 1
        record = parse_session_file(path)
        if record is None:
            continue
        session_id = record["id"]
        if not isinstance(session_id, str):
            continue
        previous = discovered.get(session_id)
        if previous is None:
            discovered[session_id] = record
            continue
        current_updated = record.get("updated_at") if isinstance(record.get("updated_at"), datetime) else None
        previous_updated = previous.get("updated_at") if isinstance(previous.get("updated_at"), datetime) else None
        if current_updated is not None and (previous_updated is None or current_updated > previous_updated):
            discovered[session_id] = record

    merged = {session_id: normalized_index_entry(entry, session_id) for session_id, entry in existing.items()}
    added = 0
    refreshed = 0
    for session_id, record in discovered.items():
        existing_entry = existing.get(session_id, {})
        if session_id not in existing:
            added += 1
        else:
            refreshed += 1
        existing_name = existing_entry.get("thread_name")
        if isinstance(existing_name, str) and existing_name.strip():
            thread_name = existing_name.strip()
        else:
            thread_name = fallback_thread_name(record.get("cwd"), session_id)
        updated_at = record.get("updated_at")
        if not isinstance(updated_at, datetime):
            updated_at = file_mtime(record["path"]) if isinstance(record.get("path"), Path) else datetime.now(timezone.utc)
        merged[session_id] = {
            "id": session_id,
            "thread_name": thread_name,
            "updated_at": render_iso_datetime(updated_at),
        }

    def sort_key(entry: dict[str, object]) -> tuple[datetime, str]:
        parsed = parse_iso_datetime(entry.get("updated_at")) or datetime.fromtimestamp(0, tz=timezone.utc)
        session_id = entry.get("id")
        return parsed, session_id if isinstance(session_id, str) else ""

    rows = sorted(merged.values(), key=sort_key)
    content = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    atomic_write(index_path, content, 0o600)
    return {
        "index_path": index_path,
        "backup": backup,
        "scanned": scanned,
        "discovered": len(discovered),
        "indexed": len(rows),
        "added": added,
        "refreshed": refreshed,
    }


def switch_local(args: argparse.Namespace) -> int:
    home = codex_home()
    auth_path = home / "auth.json"
    config_path = home / "config.toml"
    backup_dir = home / "backups"

    auth = load_auth(auth_path)
    state = load_state(home)
    remember_current_auth(home, auth, state)
    current_config = read_config(config_path)
    base_url = args.base_url or effective_local_base_url(state, current_config)
    model = args.model or effective_setting(state, "local_model", DEFAULT_MODEL)
    cached_key = state.get("local_api_key")
    api_key = args.api_key or str(auth.get("OPENAI_API_KEY") or "").strip()
    if not api_key and isinstance(cached_key, str):
        api_key = cached_key.strip()
    if not api_key:
        raise SwitchError("No API key found. Re-run with --api-key, or login once with `codex login --with-api-key`.")
    state["local_api_key"] = api_key
    state["local_base_url"] = base_url
    state["local_model"] = model

    session_snapshot = snapshot_session_state(home, backup_dir)
    backup_file(auth_path, backup_dir)
    backup_file(config_path, backup_dir)
    save_state(home, state)
    write_auth(auth_path, "apikey", api_key)
    config = rewrite_config_for_local(current_config, base_url, model)
    atomic_write(config_path, config, 0o600)

    print("Switched Codex to local relay API mode.")
    print(f"codex_home: {home}")
    print(f"base_url: {base_url}")
    print(f"model: {model}")
    print(f"api_key: {redacted_key(api_key)}")
    print(f"backup_dir: {backup_dir}")
    print_session_snapshot(session_snapshot)
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
    state["official_model"] = model
    official_auth = {"auth_mode": "chatgpt", "OPENAI_API_KEY": None}

    session_snapshot = snapshot_session_state(home, backup_dir)
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
    print_session_snapshot(session_snapshot)
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


def config_value(config: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}\s*=\s*\"([^\"]*)\"", config, re.MULTILINE)
    return match.group(1) if match else "(missing)"


def config_custom_base_url(config: str) -> str | None:
    match = re.search(
        r"^\[model_providers\.custom\][\s\S]*?^base_url\s*=\s*\"([^\"]*)\"",
        config,
        re.MULTILINE,
    )
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def custom_base_url(config: str) -> str:
    return config_custom_base_url(config) or DEFAULT_BASE_URL


def current_local_api_key(auth: dict[str, object], state: dict[str, object]) -> str | None:
    for value in (auth.get("OPENAI_API_KEY"), state.get("local_api_key")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def mode_label(auth_mode: object, provider: str) -> str:
    if auth_mode == "apikey" or provider == "custom":
        return "自定义 API"
    if auth_mode == "chatgpt" or provider == "openai":
        return "官方 OpenAI"
    return "未配置"


def auth_label(value: str) -> str:
    if value == "apikey":
        return "API Key"
    if value == "chatgpt":
        return "ChatGPT 登录"
    return value


def status_zh(_: argparse.Namespace) -> int:
    home = codex_home()
    auth_path = home / "auth.json"
    config_path = home / "config.toml"
    auth = load_auth(auth_path)
    state = load_state(home)
    config = read_config(config_path)
    auth_mode = auth.get("auth_mode") or "(missing)"
    provider = config_value(config, "model_provider")
    api_key = current_local_api_key(auth, state)

    print(f"Codex 目录: {home}")
    print(f"当前模式: {mode_label(auth_mode, provider)}")
    print(f"当前认证: {auth_label(str(auth_mode))}")
    print(f"已保存的自定义 API Key: {'已配置 ' + redacted_key(api_key) if api_key else '未配置'}")
    print(f"模型来源: {provider}")
    print(f"模型: {config_value(config, 'model')}")
    print(f"自定义 API 地址: {custom_base_url(config)}")
    return 0


def needs_setup(_: argparse.Namespace) -> int:
    home = codex_home()
    auth = load_auth(home / "auth.json")
    state = load_state(home)
    print("no" if current_local_api_key(auth, state) else "yes")
    return 0


def config_show(_: argparse.Namespace) -> int:
    home = codex_home()
    state = load_state(home)
    config = read_config(home / "config.toml")
    print(f"codex_home: {home}")
    print(f"local_base_url: {effective_local_base_url(state, config)}")
    print(f"local_model: {effective_setting(state, 'local_model', DEFAULT_MODEL)}")
    print(f"official_model: {effective_setting(state, 'official_model', DEFAULT_OFFICIAL_MODEL)}")
    return 0


def config_show_zh(_: argparse.Namespace) -> int:
    home = codex_home()
    state = load_state(home)
    config = read_config(home / "config.toml")
    print(f"Codex 目录: {home}")
    print(f"自定义 API 地址: {effective_local_base_url(state, config)}")
    print(f"自定义模型: {effective_setting(state, 'local_model', DEFAULT_MODEL)}")
    print(f"官方模型: {effective_setting(state, 'official_model', DEFAULT_OFFICIAL_MODEL)}")
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


def sessions_snapshot(_: argparse.Namespace) -> int:
    home = codex_home()
    backup_dir = home / "backups"
    copied = snapshot_session_state(home, backup_dir)
    print(f"codex_home: {home}")
    print(f"backup_dir: {backup_dir}")
    print_session_snapshot(copied)
    return 0


def sessions_rebuild_index(_: argparse.Namespace) -> int:
    home = codex_home()
    result = rebuild_session_index(home)
    print(f"codex_home: {home}")
    print(f"index_path: {result['index_path']}")
    print(f"backup: {result['backup'] or '(none)'}")
    print(f"session_files_scanned: {result['scanned']}")
    print(f"sessions_discovered: {result['discovered']}")
    print(f"sessions_added: {result['added']}")
    print(f"sessions_refreshed: {result['refreshed']}")
    print(f"sessions_indexed: {result['indexed']}")
    print("Restart Codex App if the session list is already open.")
    return 0


def sessions_list(_: argparse.Namespace) -> int:
    home = codex_home()
    entries = read_session_index(home / "session_index.jsonl")
    rows = sorted(
        (normalized_index_entry(entry, session_id) for session_id, entry in entries.items()),
        key=lambda entry: parse_iso_datetime(entry.get("updated_at")) or datetime.fromtimestamp(0, tz=timezone.utc),
    )
    print(f"codex_home: {home}")
    print(f"sessions_indexed: {len(rows)}")
    for entry in rows[-20:]:
        print(f"{entry['updated_at']}  {entry['id']}  {entry['thread_name']}")
    return 0


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

    status_zh_parser = subparsers.add_parser("status-zh", help="Show current Codex state in Chinese for the app UI.")
    status_zh_parser.set_defaults(func=status_zh)

    needs_setup_parser = subparsers.add_parser("needs-setup", help="Print yes when local API key setup is missing.")
    needs_setup_parser.set_defaults(func=needs_setup)

    config_parser = subparsers.add_parser("config", help="Show or update Codex Switch defaults.")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    config_show_parser = config_subparsers.add_parser("show", help="Show saved switch defaults.")
    config_show_parser.set_defaults(func=config_show)
    config_show_zh_parser = config_subparsers.add_parser("show-zh", help="Show saved switch defaults in Chinese.")
    config_show_zh_parser.set_defaults(func=config_show_zh)
    config_set_parser = config_subparsers.add_parser("set", help="Update saved switch defaults.")
    config_set_parser.add_argument("--local-base-url", help="Default local relay API base URL.")
    config_set_parser.add_argument("--local-model", help="Default local model.")
    config_set_parser.add_argument("--official-model", help="Default official Codex model.")
    config_set_parser.set_defaults(func=config_set)

    sessions_parser = subparsers.add_parser("sessions", help="Protect or recover Codex session list state.")
    sessions_subparsers = sessions_parser.add_subparsers(dest="sessions_command", required=True)
    sessions_snapshot_parser = sessions_subparsers.add_parser("snapshot", help="Back up Codex session list state.")
    sessions_snapshot_parser.set_defaults(func=sessions_snapshot)
    sessions_rebuild_parser = sessions_subparsers.add_parser(
        "rebuild-index",
        help="Rebuild session_index.jsonl from local session JSONL files.",
    )
    sessions_rebuild_parser.set_defaults(func=sessions_rebuild_index)
    sessions_list_parser = sessions_subparsers.add_parser("list", help="Show recent sessions from session_index.jsonl.")
    sessions_list_parser.set_defaults(func=sessions_list)

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
