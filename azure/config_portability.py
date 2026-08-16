"""Bot Config Portability — export/import bot and server configuration.

Allows exporting bot configuration as a portable JSON file that can be
imported into another server or bot instance, with API keys automatically
redacted.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("azure.config_portability")

SENSITIVE_KEYS = {
    "AZURE_DISCORD_TOKEN",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY",
    "AZURE_OPENAI_KEY",
    "AZURE_SPEECH_KEY",
    "HUGGINGFACE_TOKEN",
    "REPLICATE_API_TOKEN",
}

EXPORT_VERSION = 1


def _redact_value(value: str) -> str:
    """Redact a sensitive value, showing only first 4 and last 4 chars."""
    if len(value) <= 12:
        return value[:4] + "..." + value[-4:] if len(value) > 8 else "****"
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def _looks_redacted(value: str) -> bool:
    """True if a value appears to be a redacted placeholder from _redact_value.

    _redact_value produces "****", "abcd...wxyz", or "abcd****...****wxyz", all
    of which contain a run of mask characters that never occur in a real secret.
    """
    return "****" in value or "..." in value


def _load_env_file(env_path: str | Path) -> dict[str, str]:
    """Load a .env file into a dict, preserving the last value for each key."""
    env_path = Path(env_path)
    if not env_path.exists():
        return {}
    result: dict[str, str] = {}
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            result[key] = value
    return result


def _load_json_safe(path: str | Path) -> dict[str, Any] | None:
    """Load a JSON file, returning None if missing or invalid."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not load %s: %s", path, e)
        return None


def _save_json_safe(path: str | Path, data: Any) -> bool:
    """Save data as JSON, creating parent directories if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return True
    except OSError as e:
        logger.error("Could not save %s: %s", path, e)
        return False


# ── Export ──────────────────────────────────────────────────────────────────


def collect_env_settings(env_path: str | Path, redact: bool = True) -> dict[str, str]:
    """Collect non-sensitive environment settings from .env file.

    If redact is True, sensitive keys (API keys, tokens) are partially masked.
    """
    raw = _load_env_file(env_path)
    result: dict[str, str] = {}
    for key, value in raw.items():
        if key in SENSITIVE_KEYS:
            if redact:
                result[key] = _redact_value(value)
            else:
                result[key] = value
        elif key.startswith("AZURE_"):
            result[key] = value
    return result


def collect_server_config(
    configs_dir: str | Path,
    guild_id: str | None = None,
) -> list[dict[str, Any]]:
    """Collect server configs from guild config files."""
    configs_dir = Path(configs_dir)
    configs: list[dict[str, Any]] = []

    if guild_id:
        filepath = configs_dir / f"guild_{guild_id}.json"
        data = _load_json_safe(filepath)
        if data:
            configs.append(data)
    else:
        if not configs_dir.exists():
            return configs
        for fpath in sorted(configs_dir.glob("guild_*.json")):
            data = _load_json_safe(fpath)
            if data:
                configs.append(data)

    return configs


def collect_llm_settings(health_path: str | Path) -> dict[str, Any]:
    """Collect LLM settings from model_health.json, redacting API keys."""
    data = _load_json_safe(health_path)
    if not data:
        return {}

    result: dict[str, Any] = {}

    settings = data.get("settings", {})
    if settings:
        result["settings"] = {
            k: v for k, v in settings.items()
            if k not in ("api_key", "key", "token", "secret")
        }

    providers = {}
    for pname, pdata in data.get("providers", {}).items():
        provider_info: dict[str, Any] = {}
        for k, v in pdata.items():
            if k in ("api_key", "key", "token", "secret"):
                if isinstance(v, str):
                    provider_info[k] = _redact_value(v)
            elif k != "health":
                provider_info[k] = v
        if pdata.get("health"):
            health = dict(pdata["health"])
            if "last_error" in health and health["last_error"]:
                provider_info["last_error"] = health["last_error"]
        providers[pname] = provider_info

    result["providers"] = providers
    result["selected_provider"] = data.get("selected_provider", "")
    result["last_fetched"] = data.get("last_fetched", "")

    return result


def build_export_package(
    env_path: str | Path,
    configs_dir: str | Path,
    health_path: str | Path,
    guild_id: str | None = None,
    server_config: dict[str, Any] | None = None,
    include_env: bool = True,
    include_llm: bool = True,
) -> dict[str, Any]:
    """Build a complete export package of bot configuration."""
    package: dict[str, Any] = {
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
    }

    if include_env:
        env_settings = collect_env_settings(env_path, redact=True)
        if env_settings:
            package["env_settings"] = env_settings

    if include_llm:
        llm_settings = collect_llm_settings(health_path)
        if llm_settings:
            package["llm_settings"] = llm_settings

    server_configs = collect_server_config(configs_dir, guild_id)
    if server_config:
        server_configs.append(server_config)
    if server_configs:
        package["server_configs"] = server_configs

    auto_mod_config = _collect_auto_mod_config()
    if auto_mod_config:
        package["auto_mod_config"] = auto_mod_config

    return package


def _collect_auto_mod_config() -> dict[str, Any] | None:
    """Try to collect AutoModConfig from the active moderation instance.

    Uses a module-level reference set at runtime.
    """
    ref = getattr(_collect_auto_mod_config, "_ref", None)
    if ref is None:
        return None
    try:
        if is_dataclass(ref):
            return asdict(ref)
        if hasattr(ref, "__dict__"):
            return {k: v for k, v in ref.__dict__.items() if not k.startswith("_")}
    except Exception:
        logger.exception("Could not serialize AutoModConfig")
    return None


def set_auto_mod_config_ref(config_obj: Any) -> None:
    """Set a reference to the active AutoModConfig for export."""
    _collect_auto_mod_config._ref = config_obj


def export_to_json(
    env_path: str | Path,
    configs_dir: str | Path,
    health_path: str | Path,
    output_path: str | Path,
    guild_id: str | None = None,
    server_config: dict[str, Any] | None = None,
) -> str | None:
    """Export configuration to a portable JSON file.

    Returns the output path on success, None on failure.
    """
    package = build_export_package(
        env_path, configs_dir, health_path,
        guild_id=guild_id,
        server_config=server_config,
    )
    if _save_json_safe(output_path, package):
        return str(output_path)
    return None


# ── Import ──────────────────────────────────────────────────────────────────


def validate_import_package(data: dict[str, Any]) -> tuple[bool, str]:
    """Validate an import package structure."""
    if not isinstance(data, dict):
        return False, "Invalid format: expected JSON object"
    if "version" not in data:
        return False, "Missing version field"
    version = data.get("version", 0)
    if not isinstance(version, int) or version < 1:
        return False, f"Unsupported version: {version}"

    has_content = bool(
        data.get("env_settings")
        or data.get("llm_settings")
        or data.get("server_configs")
        or data.get("auto_mod_config")
    )
    if not has_content:
        return False, "Package contains no configurable settings"

    return True, "Valid"


def apply_env_settings(
    env_path: str | Path,
    settings: dict[str, str],
    overwrite: bool = False,
) -> int:
    """Apply environment settings to .env file. Returns count of keys applied."""
    env_path = Path(env_path)
    existing = _load_env_file(env_path)

    # Exports redact SENSITIVE_KEYS to masked placeholders (e.g. "sk-a****wxyz").
    # Never write a redacted value back — doing so would overwrite a real
    # token/API key with asterisk garbage and break authentication. Drop any
    # sensitive key whose incoming value still looks masked.
    settings = {
        k: v
        for k, v in settings.items()
        if not (k in SENSITIVE_KEYS and _looks_redacted(v))
    }

    count = 0
    lines: list[str] = []
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()

    modified_keys: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in settings:
                if overwrite or key not in existing:
                    new_lines.append(f"{key}={settings[key]}\n")
                    modified_keys.add(key)
                    count += 1
                else:
                    new_lines.append(line)
                continue
        new_lines.append(line)

    for key, value in settings.items():
        if key not in modified_keys and (overwrite or key not in existing):
            new_lines.append(f"{key}={value}\n")
            modified_keys.add(key)
            count += 1

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    return count


def apply_server_config(
    configs_dir: str | Path,
    config: dict[str, Any],
    overwrite: bool = False,
) -> bool:
    """Apply a server config to the guild config file."""
    configs_dir = Path(configs_dir)
    guild_id = config.get("guild_id")
    if not guild_id:
        logger.warning("Server config missing guild_id, skipping")
        return False

    filepath = configs_dir / f"guild_{guild_id}.json"
    existing = _load_json_safe(filepath) or {}

    if not overwrite:
        for key in ("moderation_phase", "admin_channel_id", "chat_mode"):
            if key in existing and existing[key]:
                config.pop(key, None)

    merged = {**existing, **config}
    merged["updated_at"] = time.time()
    return _save_json_safe(filepath, merged)


def apply_llm_settings(
    health_path: str | Path,
    settings: dict[str, Any],
    overwrite: bool = False,
) -> bool:
    """Apply LLM settings to model_health.json."""
    health_path = Path(health_path)
    existing = _load_json_safe(health_path) or {}

    if not overwrite and existing.get("settings"):
        existing_settings = existing.get("settings", {})
        new_settings = settings.get("settings", {})
        for key in ("provider", "model", "smart_mode"):
            if key in existing_settings and existing_settings[key]:
                new_settings.pop(key, None)
        settings["settings"] = new_settings

    merged = {**existing, **settings}
    return _save_json_safe(health_path, merged)


def import_from_package(
    data: dict[str, Any],
    env_path: str | Path,
    configs_dir: str | Path,
    health_path: str | Path,
    overwrite: bool = False,
) -> dict[str, int]:
    """Import configuration from a validated package.

    Returns a summary dict with counts of applied items.
    """
    summary: dict[str, int] = {
        "env_keys": 0,
        "server_configs": 0,
        "llm_settings": 0,
    }

    env_settings = data.get("env_settings")
    if env_settings and isinstance(env_settings, dict):
        count = apply_env_settings(env_path, env_settings, overwrite=overwrite)
        summary["env_keys"] = count

    server_configs = data.get("server_configs")
    if server_configs and isinstance(server_configs, list):
        for cfg in server_configs:
            if isinstance(cfg, dict):
                ok = apply_server_config(configs_dir, cfg, overwrite=overwrite)
                if ok:
                    summary["server_configs"] += 1

    llm_settings = data.get("llm_settings")
    if llm_settings and isinstance(llm_settings, dict):
        ok = apply_llm_settings(health_path, llm_settings, overwrite=overwrite)
        if ok:
            summary["llm_settings"] = 1

    return summary
