#!/usr/bin/env python3
"""Switch Codex auth/config between ChatGPT login and local Responses API."""

from __future__ import annotations

import argparse
import copy
import getpass
import http.server
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
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape


DEFAULT_BASE_URL = "https://jp.icodeeasy.cc"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_OFFICIAL_MODEL = "gpt-5.5"
DEFAULT_CUSTOM_MODEL = "my-gpt-5.5"
DEFAULT_ADAPTER_HOST = "127.0.0.1"
DEFAULT_ADAPTER_PORT = 17638
DEFAULT_ADAPTER_BASE_URL = f"http://{DEFAULT_ADAPTER_HOST}:{DEFAULT_ADAPTER_PORT}/v1"
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
            lines = set_bool_key(lines, "requires_openai_auth", False)
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
            "requires_openai_auth = false\n",
            'wire_api = "responses"\n',
            f'experimental_bearer_token = "{api_key}"\n',
            f"models = {quoted_array(models)}\n",
        ]
        updated.append((f"[model_providers.{CUSTOM_PROVIDER_ID}]", lines))
    return updated


def ensure_profile(
    sections: list[tuple[str, list[str]]], profile: str, settings: dict[str, str]
) -> list[tuple[str, list[str]]]:
    # Profiles let the Codex CLI (`codex --profile <name>`) select a provider +
    # model without touching the top-level config the desktop app reads.
    header = f"[profiles.{profile}]"
    updated: list[tuple[str, list[str]]] = []
    found = False
    for section, lines in sections:
        if section == header:
            found = True
            for key, value in settings.items():
                lines = set_key(lines, key, value)
        updated.append((section, lines))
    if not found:
        lines = ["\n" if updated and updated[-1][1] and updated[-1][1][-1].strip() else "", f"{header}\n"]
        lines.extend(f'{key} = "{value}"\n' for key, value in settings.items())
        updated.append((header, lines))
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
    catalog_path: Path | None,
    default_provider: str = "openai",
    skip_login: bool = False,
) -> str:
    sections = split_toml_sections(content)
    rewritten: list[tuple[str, list[str]]] = []
    if default_provider == CUSTOM_PROVIDER_ID:
        selected_provider = CUSTOM_PROVIDER_ID
        selected_model = custom_model
    else:
        selected_provider = "openai"
        selected_model = official_model
    auth_method = "api-key" if skip_login else "chatgpt"
    for section, lines in sections:
        if section == "":
            lines = set_key(lines, "model_provider", selected_provider)
            lines = set_key(lines, "model", selected_model)
            lines = set_key(lines, "preferred_auth_method", auth_method)
            if catalog_path is not None:
                lines = set_key(lines, "model_catalog_json", str(catalog_path))
            else:
                lines = remove_key(lines, "model_catalog_json")
        rewritten.append((section, lines))
    rewritten = ensure_custom_provider(rewritten, base_url, api_key, [custom_model])
    # CLI parity: `codex --profile ccswitch` -> custom route,
    # `codex --profile official` -> official route.
    rewritten = ensure_profile(
        rewritten, "ccswitch", {"model_provider": CUSTOM_PROVIDER_ID, "model": custom_model}
    )
    rewritten = ensure_profile(
        rewritten, "official", {"model_provider": "openai", "model": official_model}
    )
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


def custom_model_entry(
    model: str,
    display_name: str,
    template: dict[str, object] | None = None,
) -> dict[str, object]:
    # Prefer cloning a real official model so the entry carries every field
    # Codex requires (shell_type, model_messages, base_instructions, etc.).
    if template is not None:
        entry = copy.deepcopy(template)
        entry["slug"] = model
        entry["display_name"] = display_name
        entry["description"] = "Custom model registered by Codex Switch"
        entry["priority"] = 100
        entry.pop("upgrade", None)
        entry.pop("availability_nux", None)
        return entry
    return {
        "slug": model,
        "display_name": display_name,
        "description": "Custom model registered by Codex Switch",
        "shell_type": "shell_command",
        "visibility": "list",
        "supported_in_api": True,
        "priority": 1,
        "additional_speed_tiers": [],
        "service_tiers": [],
        "availability_nux": None,
        "upgrade": None,
        "supports_reasoning_summaries": True,
        "default_reasoning_summary": "none",
        "support_verbosity": True,
        "default_verbosity": "medium",
        "apply_patch_tool_type": "freeform",
        "web_search_tool_type": "text_and_image",
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "supports_parallel_tool_calls": True,
        "supports_image_detail_original": True,
        "context_window": 200000,
        "max_context_window": 200000,
        "max_output_tokens": 100000,
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [],
        "supported_reasoning_levels": [
            {"effort": "low", "description": "Fast responses with lighter reasoning"},
            {"effort": "medium", "description": "Balances speed and reasoning depth for everyday tasks"},
            {"effort": "high", "description": "Greater reasoning depth for complex problems"},
        ],
        "default_reasoning_level": "medium",
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
        "supports_search_tool": True,
        "use_responses_lite": False,
    }


def load_official_models(home: Path) -> list[dict[str, object]]:
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
    return [dict(item) for item in models if isinstance(item, dict)]


def official_model_slugs(home: Path) -> set[str]:
    return {item["slug"] for item in load_official_models(home) if isinstance(item.get("slug"), str)}


def ensure_distinct_custom_model(home: Path, custom_model: str, official_model: str | None = None) -> None:
    pass


def historical_model_slugs(home: Path) -> list[str]:
    """Every model id referenced by existing threads, in stable order.

    Codex's model_catalog_json REPLACES the built-in catalog, so any model a
    saved conversation used must also be present here or the desktop app cannot
    resolve (and therefore cannot list/restore) that conversation. We collect
    the model ids from the local thread databases so the generated catalog stays
    a superset of what history needs.
    """
    slugs: list[str] = []
    seen: set[str] = set()
    databases = list((home / "sqlite").glob("state_*.sqlite")) + list(home.glob("state_*.sqlite"))
    for database in databases:
        try:
            connection = sqlite3.connect(database)
        except sqlite3.Error:
            continue
        try:
            if "model" not in sqlite_columns(connection, "threads"):
                continue
            for (value,) in connection.execute(
                "SELECT DISTINCT model FROM threads WHERE model IS NOT NULL AND model <> ''"
            ):
                if isinstance(value, str) and value not in seen:
                    seen.add(value)
                    slugs.append(value)
        except sqlite3.Error:
            continue
        finally:
            connection.close()
    return slugs


def custom_model_catalog(
    home: Path,
    model: str,
    display_name: str,
    additional_models: list[tuple[str, str]] | None = None,
    visible_provider: str = CUSTOM_PROVIDER_ID,
) -> dict[str, object]:
    # The catalog must be a SUPERSET of everything Codex history needs because
    # model_catalog_json replaces the built-in catalog. So we keep all official
    # models verbatim (original names), add any model referenced by existing
    # conversations (so they still resolve and restore), and finally add the
    # custom model with its user-defined display name.
    official = load_official_models(home)
    by_slug = {item.get("slug"): item for item in official if isinstance(item.get("slug"), str)}

    # Clone a real official model so synthesized entries carry every required field.
    template: dict[str, object] | None = None
    for slug, _ in additional_models or []:
        if slug in by_slug:
            template = by_slug[slug]
            break
    if template is None and official:
        template = official[0]

    models = list(official)
    seen = set(by_slug)

    # When the active provider is not openai, hide official models from the
    # picker so users only see models routable through the active provider.
    if visible_provider != "openai":
        for m in models:
            m["visibility"] = "hide"

    # Preserve historical models referenced by saved conversations, but hide
    # them from the picker so it isn't cluttered with old/experimental slugs.
    # They stay in the catalog only so those conversations still resolve.
    for slug in historical_model_slugs(home):
        if slug and slug not in seen and slug != model:
            entry = custom_model_entry(slug, slug, template)
            entry["visibility"] = "hide"
            models.append(entry)
            seen.add(slug)

    extras = list(additional_models or [])
    for slug, name in extras:
        if slug and slug not in seen:
            entry = custom_model_entry(slug, name or slug, template)
            entry["visibility"] = "list" if visible_provider == CUSTOM_PROVIDER_ID else "hide"
            models.append(entry)
            seen.add(slug)

    if model:
        vis = "list" if visible_provider == CUSTOM_PROVIDER_ID else "hide"
        if model in seen:
            for m in models:
                if m.get("slug") == model:
                    m["display_name"] = display_name
                    m["visibility"] = vis
                    break
        else:
            entry = custom_model_entry(model, display_name, template)
            entry["visibility"] = vis
            models.append(entry)
            seen.add(model)

    return {"models": models}


def write_model_catalog(
    path: Path,
    model: str,
    display_name: str | None = None,
    additional_models: list[tuple[str, str]] | None = None,
    visible_provider: str = CUSTOM_PROVIDER_ID,
) -> None:
    atomic_write(
        path,
        json.dumps(
            custom_model_catalog(path.parent, model, display_name or model, additional_models, visible_provider),
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


def strip_trailing_slash(value: str) -> str:
    return value.rstrip("/")


def chat_completions_url(base_url: str) -> str:
    base = strip_trailing_slash(base_url)
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def responses_url(base_url: str) -> str:
    base = strip_trailing_slash(base_url)
    if base.endswith("/v1"):
        return f"{base}/responses"
    return f"{base}/v1/responses"


def response_content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and item.get("type") in {"input_text", "output_text", "text"}:
                chunks.append(text)
        return "\n".join(chunks)
    return ""


def responses_input_to_chat_messages(request: dict[str, object]) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    instructions = request.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})
    raw_input = request.get("input")
    if isinstance(raw_input, str):
        messages.append({"role": "user", "content": raw_input})
    elif isinstance(raw_input, list):
        for item in raw_input:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            role = item.get("role")
            if item_type == "message" or role in {"system", "developer", "user", "assistant"}:
                chat_role = "system" if role == "developer" else role if isinstance(role, str) else "user"
                msg: dict[str, object] = {"role": chat_role, "content": response_content_to_text(item.get("content"))}
                if chat_role == "assistant":
                    reasoning = item.get("reasoning_content")
                    if not reasoning:
                        content = item.get("content")
                        if isinstance(content, list):
                            for part in content:
                                if isinstance(part, dict) and part.get("type") == "reasoning" and isinstance(part.get("text"), str):
                                    reasoning = part["text"]
                                    break
                    if reasoning:
                        msg["reasoning_content"] = reasoning
                messages.append(msg)
            elif item_type == "function_call":
                call_id = str(item.get("call_id") or item.get("id") or "")
                tool_call = {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": str(item.get("name") or "tool"),
                        "arguments": str(item.get("arguments") or "{}"),
                    },
                }
                if messages and messages[-1].get("role") == "assistant" and isinstance(messages[-1].get("tool_calls"), list):
                    messages[-1]["tool_calls"].append(tool_call)
                else:
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [tool_call],
                    })
            elif item_type == "function_call_output":
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(item.get("call_id") or ""),
                    "content": response_content_to_text(item.get("output")) or str(item.get("output") or ""),
                })
    if not messages:
        messages.append({"role": "user", "content": ""})
    return messages


def responses_tools_to_chat_tools(tools: object) -> list[dict[str, object]]:
    if not isinstance(tools, list):
        return []
    output: list[dict[str, object]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and isinstance(tool.get("name"), str):
            output.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description") or "",
                    "parameters": tool.get("parameters") or {},
                },
            })
    return output


def responses_request_to_chat_payload(request: dict[str, object], upstream_model: str, stream: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": upstream_model,
        "messages": responses_input_to_chat_messages(request),
        "stream": stream,
    }
    tools = responses_tools_to_chat_tools(request.get("tools"))
    if tools:
        payload["tools"] = tools
    for key in ("temperature", "top_p", "max_tokens", "max_completion_tokens"):
        if key in request:
            payload[key] = request[key]
    return payload


def chat_message_to_response_output(message: dict[str, object]) -> list[dict[str, object]]:
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        output: list[dict[str, object]] = []
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            output.append({
                "type": "function_call",
                "id": str(call.get("id") or f"fc_{uuid.uuid4().hex}"),
                "call_id": str(call.get("id") or f"call_{uuid.uuid4().hex}"),
                "name": str(function.get("name") or "tool"),
                "arguments": str(function.get("arguments") or "{}"),
                "status": "completed",
            })
        return output
    text = message.get("content")
    return [{
        "type": "message",
        "id": f"msg_{uuid.uuid4().hex}",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text if isinstance(text, str) else "", "annotations": []}],
    }]


def chat_response_to_responses(chat: dict[str, object], model: str) -> dict[str, object]:
    choices = chat.get("choices")
    message: dict[str, object] = {}
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        maybe_message = choices[0].get("message")
        if isinstance(maybe_message, dict):
            message = maybe_message
    raw_usage = chat.get("usage") or {}
    usage = {
        "input_tokens": raw_usage.get("input_tokens") or raw_usage.get("prompt_tokens") or 0,
        "output_tokens": raw_usage.get("output_tokens") or raw_usage.get("completion_tokens") or 0,
        "total_tokens": raw_usage.get("total_tokens") or 0,
    }
    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": chat_message_to_response_output(message),
        "usage": usage,
    }


class AdapterServer(http.server.ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], home: Path):
        super().__init__(server_address, AdapterHandler)
        self.home = home


class AdapterHandler(http.server.BaseHTTPRequestHandler):
    server: AdapterServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_json(self, status: int, payload: dict[str, object]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path in {"/health", "/v1/health"}:
            self.send_json(200, {"status": "ok"})
            return
        self.send_json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        if self.path not in {"/responses", "/v1/responses"}:
            self.send_json(404, {"error": {"message": "not found"}})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length)
            request = json.loads(body.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("request body must be a JSON object")
            self.proxy_response(request)
        except Exception as exc:
            self.send_json(500, {"error": {"message": str(exc)}})

    def proxy_response(self, request: dict[str, object]) -> None:
        state = load_state(self.server.home)
        auth = load_auth(self.server.home / "auth.json")
        upstream_base_url = effective_setting(state, "adapter_upstream_base_url", effective_setting(state, "local_base_url", DEFAULT_BASE_URL))
        upstream_model = effective_setting(state, "adapter_upstream_model", effective_setting(state, "local_model", DEFAULT_MODEL))
        api_key = effective_setting(state, "local_api_key", str(auth.get("OPENAI_API_KEY") or ""))
        if not api_key:
            raise SwitchError("adapter API key is missing")
        stream = bool(request.get("stream"))
        resp_id = f"resp_{uuid.uuid4().hex}"

        if stream:
            # Send SSE headers and initial event immediately so Codex
            # knows the connection is alive while we wait for upstream.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.write_sse("response.created", {
                "type": "response.created",
                "response": {
                    "id": resp_id,
                    "object": "response",
                    "created_at": int(time.time()),
                    "status": "in_progress",
                    "model": upstream_model,
                    "output": [],
                },
            })

        # Always ask the upstream Chat Completions endpoint for a complete
        # response. Many OpenAI-compatible relays stream text deltas but omit or
        # vary tool-call deltas; Codex needs reliable tool calls more than token
        # streaming when we bridge Chat Completions to Responses.
        payload = responses_request_to_chat_payload(request, upstream_model, False)
        upstream_request = urllib.request.Request(
            chat_completions_url(upstream_base_url),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(upstream_request, timeout=120) as response:
                chat = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            if "/v1/responses" in error_body or "responses" in error_body.lower():
                result = self.passthrough_responses(request, upstream_base_url, upstream_model, api_key, resp_id, stream)
                if result is not None:
                    if stream:
                        self.write_response_stream(result)
                    else:
                        self.send_json(200, result)
                    return
            if stream:
                self.write_sse("response.failed", {
                    "type": "response.failed",
                    "response": {
                        "id": resp_id,
                        "object": "response",
                        "created_at": int(time.time()),
                        "status": "failed",
                        "model": upstream_model,
                        "output": [],
                        "error": {"message": error_body},
                    },
                })
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
            self.send_json(exc.code, {"error": {"message": error_body}})
            return

        result = chat_response_to_responses(chat, upstream_model)
        result["id"] = resp_id
        if stream:
            self.write_response_stream(result)
            return
        self.send_json(200, result)

    def passthrough_responses(
        self, request: dict[str, object],
        upstream_base_url: str, upstream_model: str,
        api_key: str, resp_id: str, stream: bool,
    ) -> dict[str, object] | None:
        passthrough_body = dict(request)
        passthrough_body["model"] = upstream_model
        passthrough_body["stream"] = False
        upstream_req = urllib.request.Request(
            responses_url(upstream_base_url),
            data=json.dumps(passthrough_body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(upstream_req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
        if not isinstance(result, dict):
            return None
        result["id"] = resp_id
        raw_usage = result.get("usage") or {}
        if isinstance(raw_usage, dict) and "input_tokens" not in raw_usage:
            result["usage"] = {
                "input_tokens": raw_usage.get("input_tokens") or raw_usage.get("prompt_tokens") or 0,
                "output_tokens": raw_usage.get("output_tokens") or raw_usage.get("completion_tokens") or 0,
                "total_tokens": raw_usage.get("total_tokens") or 0,
            }
        return result

    def write_sse(self, event: str, payload: dict[str, object]) -> None:
        self.wfile.write(f"event: {event}\n".encode("utf-8"))
        self.wfile.write(("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def write_response_stream(self, response_payload: dict[str, object]) -> None:
        output = response_payload.get("output")
        if isinstance(output, list):
            for index, item in enumerate(output):
                if not isinstance(item, dict):
                    continue
                in_progress = dict(item)
                in_progress["status"] = "in_progress"
                item_id = str(item.get("id") or f"item_{uuid.uuid4().hex}")
                self.write_sse("response.output_item.added", {"type": "response.output_item.added", "output_index": index, "item": in_progress})
                if item.get("type") == "message":
                    content = item.get("content")
                    if isinstance(content, list):
                        for content_index, part in enumerate(content):
                            if not isinstance(part, dict):
                                continue
                            self.write_sse("response.content_part.added", {"type": "response.content_part.added", "output_index": index, "content_index": content_index, "item_id": item_id, "part": part})
                            text = part.get("text")
                            if isinstance(text, str) and text:
                                self.write_sse("response.output_text.delta", {"type": "response.output_text.delta", "output_index": index, "content_index": content_index, "item_id": item_id, "delta": text})
                                self.write_sse("response.output_text.done", {"type": "response.output_text.done", "output_index": index, "content_index": content_index, "item_id": item_id, "text": text})
                            self.write_sse("response.content_part.done", {"type": "response.content_part.done", "output_index": index, "content_index": content_index, "item_id": item_id, "part": part})
                self.write_sse("response.output_item.done", {"type": "response.output_item.done", "output_index": index, "item": item})
        self.write_sse("response.completed", {"type": "response.completed", "response": response_payload})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def run_adapter(args: argparse.Namespace) -> int:
    home = Path(args.codex_home).expanduser() if args.codex_home else codex_home()
    server = AdapterServer((args.host, args.port), home)
    print(f"Codex Switch adapter listening on http://{args.host}:{args.port}/v1")
    server.serve_forever()
    return 0


def adapter_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.kuangre.codex-switch.adapter.plist"


def launchctl_gui_target() -> str:
    return f"gui/{os.getuid()}"


def write_adapter_launch_agent(home: Path, host: str = DEFAULT_ADAPTER_HOST, port: int = DEFAULT_ADAPTER_PORT) -> Path:
    path = adapter_launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cli_path = str(Path(sys.argv[0]).resolve())
    log_path = str(home / "codex-switch-adapter.log")
    content = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.kuangre.codex-switch.adapter</string>
  <key>ProgramArguments</key>
  <array>
    <string>{xml_escape(cli_path)}</string>
    <string>adapter</string>
    <string>serve</string>
    <string>--host</string>
    <string>{host}</string>
    <string>--port</string>
    <string>{port}</string>
    <string>--codex-home</string>
    <string>{xml_escape(str(home))}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{xml_escape(log_path)}</string>
  <key>StandardErrorPath</key>
  <string>{xml_escape(log_path)}</string>
</dict>
</plist>
'''
    atomic_write(path, content, 0o644)
    return path


def start_adapter_launch_agent(home: Path) -> Path:
    path = write_adapter_launch_agent(home)
    subprocess.run(["launchctl", "bootout", launchctl_gui_target(), str(path)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "bootstrap", launchctl_gui_target(), str(path)], check=True)
    subprocess.run(["launchctl", "kickstart", "-k", f"{launchctl_gui_target()}/com.kuangre.codex-switch.adapter"], check=False)
    return path


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
    # Codex is now down, so the thread databases are unlocked. The desktop app
    # filters the conversation list by model_provider, so re-tag every saved
    # conversation to the active provider; otherwise switching providers hides
    # all conversations created under the other one.
    try:
        changed_rollouts, sqlite_rows, _ = sync_provider_metadata(home, expected_provider)
        if changed_rollouts or sqlite_rows:
            print(
                f"Synced existing conversations to provider {expected_provider} "
                f"({changed_rollouts} session files, {sqlite_rows} database rows)."
            )
    except SwitchError as exc:
        print(f"Warning: could not sync conversation provider metadata: {exc}")
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
    # The catalog must be a superset of every model history references, so keep
    # official + historical models alongside the custom one (model_catalog_json
    # replaces Codex's built-in catalog).
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
    custom_model = args.custom_model or args.model or effective_setting(state, "local_model", DEFAULT_CUSTOM_MODEL)
    display_name = args.custom_model_name or effective_setting(state, "local_model_display_name", custom_model)
    official_model = args.official_model or effective_setting(state, "official_model", DEFAULT_OFFICIAL_MODEL)
    default_provider = args.default_provider or effective_setting(state, "default_provider", "openai")
    if default_provider not in {"openai", CUSTOM_PROVIDER_ID}:
        raise SwitchError("default_provider must be openai or custom")
    ensure_distinct_custom_model(home, custom_model, official_model)
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
    state["default_provider"] = default_provider
    if getattr(args, "chat_adapter", False):
        state["chat_adapter"] = "true"
        state["adapter_upstream_base_url"] = base_url
        state["adapter_upstream_model"] = custom_model
        provider_base_url = DEFAULT_ADAPTER_BASE_URL
    else:
        state["chat_adapter"] = "false"
        provider_base_url = base_url
    skip_login = getattr(args, "skip_login", False)
    state["skip_login"] = "true" if skip_login else "false"
    save_state(home, state)

    auth["OPENAI_API_KEY"] = api_key
    if skip_login:
        auth["auth_mode"] = "api-key"
        auth.pop("tokens", None)
    else:
        auth["auth_mode"] = "chatgpt"
    write_auth(auth_path, auth)
    # The Codex picker has a single active provider, so the model catalog must
    # only expose models that route to it. In official mode we fall back to
    # Codex's built-in catalog (no custom file); in custom mode the catalog
    # lists ONLY the custom model so an official model can't be picked and then
    # force-routed through the custom proxy/adapter.
    # Always write the superset catalog (official + historical + custom) so every
    # model resolves in both the desktop app and the CLI profiles, regardless of
    # which provider is the default.
    write_model_catalog(catalog_path, custom_model, display_name, [(official_model, official_model)], visible_provider=default_provider)
    effective_catalog: Path | None = catalog_path
    adapter_plist = start_adapter_launch_agent(home) if getattr(args, "chat_adapter", False) else None
    config = rewrite_config_for_parallel(
        read_config(config_path),
        provider_base_url,
        official_model,
        custom_model,
        api_key,
        effective_catalog,
        default_provider,
        skip_login,
    )
    atomic_write(config_path, config, 0o600)

    print("Configured Codex official and custom providers in parallel.")
    print(f"codex_home: {home}")
    print(f"model_provider: {default_provider}")
    print(f"official_model: {official_model}")
    print(f"custom.base_url: {provider_base_url}")
    if adapter_plist is not None:
        print(f"adapter_upstream_base_url: {base_url}")
        print(f"adapter_launch_agent: {adapter_plist}")
    print(f"custom_model: {custom_model}")
    print(f"custom_model_name: {display_name}")
    print(f"model_catalog_json: {effective_catalog if effective_catalog is not None else '(built-in)'}")
    print(f"api_key: {redacted_key(api_key)}")
    print(f"backup_dir: {backup_dir}")
    restart_codex(args, home, default_provider)
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
    print(f"local_model: {effective_setting(state, 'local_model', DEFAULT_CUSTOM_MODEL)}")
    print(f"local_model_display_name: {effective_setting(state, 'local_model_display_name', effective_setting(state, 'local_model', DEFAULT_CUSTOM_MODEL))}")
    print(f"official_model: {effective_setting(state, 'official_model', DEFAULT_OFFICIAL_MODEL)}")
    print(f"default_provider: {effective_setting(state, 'default_provider', 'openai')}")
    print(f"chat_adapter: {effective_setting(state, 'chat_adapter', 'false')}")
    print(f"skip_login: {effective_setting(state, 'skip_login', 'false')}")
    print(f"adapter_upstream_base_url: {effective_setting(state, 'adapter_upstream_base_url', effective_setting(state, 'local_base_url', DEFAULT_BASE_URL))}")
    return 0


def config_set(args: argparse.Namespace) -> int:
    home = codex_home()
    state = load_state(home)
    updates = {
        "local_base_url": args.local_base_url,
        "local_model": args.local_model,
        "local_model_display_name": args.local_model_display_name,
        "official_model": args.official_model,
        "default_provider": args.default_provider,
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
    ensure_distinct_custom_model(home, model)
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
    config_set_parser.add_argument("--default-provider", choices=("openai", CUSTOM_PROVIDER_ID), help="Default Codex provider after saving.")
    config_set_parser.set_defaults(func=config_set)

    configure = subparsers.add_parser("configure", help="Configure Codex official and custom providers in parallel.")
    configure_key_source = configure.add_mutually_exclusive_group()
    configure_key_source.add_argument("--api-key", help="API key for the custom provider. Omit to reuse existing key.")
    configure_key_source.add_argument("--api-key-stdin", action="store_true", help="Read a replacement API key from stdin.")
    configure.add_argument("--base-url", help=f"Custom API base URL. Default: saved setting or {DEFAULT_BASE_URL}")
    configure.add_argument("--model", help=argparse.SUPPRESS)
    configure.add_argument("--custom-model", help=f"Custom model slug. Must be different from official Codex model IDs. Default: saved setting or {DEFAULT_CUSTOM_MODEL}")
    configure.add_argument("--custom-model-name", help="Display name for the custom model.")
    configure.add_argument("--official-model", help=f"Official Codex default model. Default: saved setting or {DEFAULT_OFFICIAL_MODEL}")
    configure.add_argument("--default-provider", choices=("openai", CUSTOM_PROVIDER_ID), help="Default Codex provider after saving. Defaults to saved setting or openai.")
    configure.add_argument("--chat-adapter", action="store_true", help="Route Codex Responses API traffic through the local chat-completions adapter.")
    configure.add_argument("--skip-login", action="store_true", help="Bypass ChatGPT OAuth login by using API-key auth mode.")
    configure.add_argument("--restart-codex", action="store_true", help="Gracefully quit and reopen Codex.app after saving.")
    configure.set_defaults(func=configure_codex)

    adapter = subparsers.add_parser("adapter", help="Run or manage the local Responses-to-Chat adapter.")
    adapter_subparsers = adapter.add_subparsers(dest="adapter_command", required=True)
    adapter_serve = adapter_subparsers.add_parser("serve", help="Serve the local Responses-to-Chat adapter.")
    adapter_serve.add_argument("--host", default=DEFAULT_ADAPTER_HOST)
    adapter_serve.add_argument("--port", type=int, default=DEFAULT_ADAPTER_PORT)
    adapter_serve.add_argument("--codex-home")
    adapter_serve.set_defaults(func=run_adapter)

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
