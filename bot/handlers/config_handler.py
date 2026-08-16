"""Bot Config Portability handler.

Provides:
  /config export     — Export bot configuration as JSON
  /config import     — Import bot configuration from JSON
  /config template   — Download an empty config template
  /config apply      — Apply a specific section from a config file
  /config view       — View current config summary
"""

from __future__ import annotations

import contextlib
import json
import logging
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from azure.config_portability import (
    apply_llm_settings,
    apply_server_config,
    build_export_package,
    import_from_package,
    validate_import_package,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger("azure.discord.config")

COLOR_INFO = 0x5865F2
COLOR_SUCCESS = 0x57F287
COLOR_DANGER = 0xED4245

ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = ROOT / ".env"
CONFIGS_DIR = ROOT / "configs"
HEALTH_PATH = CONFIGS_DIR / "model_health.json"

TEMPLATE = {
    "version": 1,
    "exported_at": "<timestamp>",
    "description": "Paste your server's exported config into the import command.",
    "env_settings": {
        "AZURE_WELCOME_LOOKBACK_HOURS": "1",
        "AZURE_N_THREADS": "4",
    },
    "server_configs": [
        {
            "guild_id": "<your_guild_id>",
            "guild_name": "<server_name>",
            "moderation_phase": "dry_run",
            "admin_channel_id": "",
            "chat_mode": "anyone",
            "exempt_channels": [],
            "exempt_users": [],
            "exempt_roles": [],
            "trusted_roles": [],
        },
    ],
    "auto_mod_config": {
        "enabled": True,
        "dry_run": True,
        "auto_delete_threshold": 0.6,
        "auto_warn_threshold": 0.7,
        "auto_timeout_threshold": 0.8,
        "never_auto_kick": True,
        "never_auto_ban": True,
        "timeout_first": 300,
        "timeout_second": 3600,
        "timeout_third": 86400,
        "max_actions_per_minute": 10,
        "max_timeouts_per_hour": 20,
        "max_kicks_per_hour": 5,
        "max_bans_per_hour": 3,
        "warn_cooldown": 3600,
        "timeout_cooldown": 7200,
    },
    "llm_settings": {
        "settings": {
            "smart_mode": False,
        },
    },
}


def _is_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    user = interaction.user
    if isinstance(user, discord.Member):
        return user.guild_permissions.administrator or interaction.guild.owner_id == user.id
    return False


def _get_server_config_summary(interaction: discord.Interaction) -> dict[str, Any] | None:
    """Get the ServerConfig for this guild from disk, if it exists."""
    if not interaction.guild:
        return None
    filepath = CONFIGS_DIR / f"guild_{interaction.guild.id}.json"
    if not filepath.exists():
        return None
    try:
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def register_config_commands(tree: app_commands.CommandTree) -> None:
    """Register all /config slash commands."""

    config_group = app_commands.Group(
        name="config",
        description="Bot configuration export/import commands",
    )

    # ── /config export ────────────────────────────────────────────────
    @config_group.command(
        name="export",
        description="Export bot configuration as a portable JSON file",
    )
    @app_commands.describe(
        include_env="Include .env settings (default: True)",
        include_llm="Include LLM provider settings (default: True)",
    )
    async def export_cmd(
        interaction: discord.Interaction,
        include_env: bool = True,
        include_llm: bool = True,
    ):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only server admins can export config.", ephemeral=True,
                )

            await interaction.response.defer(ephemeral=True)

            server_config = _get_server_config_summary(interaction)

            package = build_export_package(
                ENV_PATH,
                CONFIGS_DIR,
                HEALTH_PATH,
                guild_id=str(interaction.guild.id) if interaction.guild else None,
                server_config=server_config,
                include_env=include_env,
                include_llm=include_llm,
            )

            json_bytes = json.dumps(package, indent=2, default=str).encode("utf-8")
            file = discord.File(
                BytesIO(json_bytes),
                filename=f"azure_config_{interaction.guild.id if interaction.guild else 'global'}.json",
            )

            embed = discord.Embed(
                title="Config Exported",
                description=(
                    f"Configuration exported successfully.\n\n"
                    f"**Includes:**\n"
                    f"• Environment settings ({'yes' if include_env else 'no'})\n"
                    f"• Server configuration (current guild)\n"
                    f"• LLM provider settings ({'yes' if include_llm else 'no'})\n\n"
                    f"API keys and tokens have been **redacted**.\n"
                    f"Use `/config import` to apply this config on another server."
                ),
                color=COLOR_SUCCESS,
            )
            await interaction.followup.send(embed=embed, file=file, ephemeral=True)

        except Exception:
            logger.exception("[config export] failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    # ── /config import ────────────────────────────────────────────────
    @config_group.command(
        name="import",
        description="Import bot configuration from a JSON file",
    )
    @app_commands.describe(
        file="The JSON config file to import",
        overwrite="Overwrite existing settings (default: False)",
    )
    async def import_cmd(
        interaction: discord.Interaction,
        file: discord.Attachment,
        overwrite: bool = False,
    ):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only server admins can import config.", ephemeral=True,
                )

            if not file.filename.endswith(".json"):
                return await interaction.response.send_message(
                    "Please upload a `.json` file.", ephemeral=True,
                )

            if file.size > 1_000_000:
                return await interaction.response.send_message(
                    "File too large (max 1MB).", ephemeral=True,
                )

            await interaction.response.defer(ephemeral=True)

            content = await file.read()
            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                return await interaction.followup.send(
                    f"Invalid JSON: {e}", ephemeral=True,
                )

            valid, msg = validate_import_package(data)
            if not valid:
                return await interaction.followup.send(
                    f"Invalid config package: {msg}", ephemeral=True,
                )

            summary = import_from_package(
                data,
                ENV_PATH,
                CONFIGS_DIR,
                HEALTH_PATH,
                overwrite=overwrite,
            )

            embed = discord.Embed(
                title="Config Imported",
                description=(
                    f"Configuration import completed.\n\n"
                    f"**Applied:**\n"
                    f"• Environment keys: {summary['env_keys']}\n"
                    f"• Server configs: {summary['server_configs']}\n"
                    f"• LLM settings: {summary['llm_settings']}\n\n"
                    f"**Note:** Some changes may require a bot restart to take full effect."
                ),
                color=COLOR_INFO,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[config import] failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    # ── /config template ──────────────────────────────────────────────
    @config_group.command(
        name="template",
        description="Download an empty config template",
    )
    async def template_cmd(interaction: discord.Interaction):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only server admins can download the template.", ephemeral=True,
                )

            json_bytes = json.dumps(TEMPLATE, indent=2).encode("utf-8")
            file = discord.File(
                BytesIO(json_bytes),
                filename="azure_config_template.json",
            )

            embed = discord.Embed(
                title="Config Template",
                description=(
                    "Fill in this template and use `/config import` to apply it.\n\n"
                    "**Fields:**\n"
                    "• `env_settings` — Global bot settings\n"
                    "• `server_configs` — Per-server configuration\n"
                    "• `auto_mod_config` — Auto moderation settings\n"
                    "• `llm_settings` — LLM provider/model selection\n\n"
                    "Only fields you include will be applied."
                ),
                color=COLOR_INFO,
            )
            await interaction.response.send_message(embed=embed, file=file, ephemeral=True)

        except Exception:
            logger.exception("[config template] failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /config view ──────────────────────────────────────────────────
    @config_group.command(
        name="view",
        description="View current configuration summary",
    )
    async def view_cmd(interaction: discord.Interaction):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only server admins can view config.", ephemeral=True,
                )

            await interaction.response.defer(ephemeral=True)

            embed = discord.Embed(
                title="Configuration Summary",
                color=COLOR_INFO,
            )

            # Server config
            server_cfg = _get_server_config_summary(interaction)
            if server_cfg:
                embed.add_field(
                    name="Server Config",
                    value=(
                        f"**Phase:** {server_cfg.get('moderation_phase', 'N/A')}\n"
                        f"**Chat Mode:** {server_cfg.get('chat_mode', 'N/A')}\n"
                        f"**Confirmation:** {server_cfg.get('confirmation_mode', 'N/A')}\n"
                        f"**Exempt Channels:** {len(server_cfg.get('exempt_channels', []))}\n"
                        f"**Exempt Users:** {len(server_cfg.get('exempt_users', []))}\n"
                        f"**Trusted Roles:** {len(server_cfg.get('trusted_roles', []))}"
                    ),
                    inline=True,
                )
            else:
                embed.add_field(name="Server Config", value="No per-server config found", inline=True)

            # Health file
            health_path = HEALTH_PATH
            if health_path.exists():
                try:
                    with open(health_path, encoding="utf-8") as f:
                        health = json.load(f)
                    settings = health.get("settings", {})
                    embed.add_field(
                        name="LLM Settings",
                        value=(
                            f"**Provider:** {settings.get('provider', 'N/A')}\n"
                            f"**Model:** {settings.get('model', 'N/A')}\n"
                            f"**Smart Mode:** {settings.get('smart_mode', False)}"
                        ),
                        inline=True,
                    )
                except Exception:
                    pass

            # Imported configs
            configs_dir = CONFIGS_DIR
            if configs_dir.exists():
                guild_files = list(configs_dir.glob("guild_*.json"))
                health_file = configs_dir / "model_health.json"
                len(guild_files) + (1 if health_file.exists() else 0)
                embed.add_field(
                    name="Config Files",
                    value=f"{len(guild_files)} server configs\n"
                          f"{'✓' if health_file.exists() else '✗'} LLM health",
                    inline=True,
                )

            embed.set_footer(text=f"Config directory: {CONFIGS_DIR}")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[config view] failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    # ── /config apply ─────────────────────────────────────────────────
    @config_group.command(
        name="apply",
        description="Apply a specific section from an attached config file",
    )
    @app_commands.describe(
        file="The JSON config file",
        section="Which section to apply",
    )
    @app_commands.choices(section=[
        app_commands.Choice(name="Server Config", value="server_configs"),
        app_commands.Choice(name="LLM Settings", value="llm_settings"),
        app_commands.Choice(name="Auto Mod Config", value="auto_mod_config"),
    ])
    async def apply_cmd(
        interaction: discord.Interaction,
        file: discord.Attachment,
        section: str,
    ):
        try:
            if not _is_admin(interaction):
                return await interaction.response.send_message(
                    "Only server admins can apply config.", ephemeral=True,
                )

            if not file.filename.endswith(".json"):
                return await interaction.response.send_message(
                    "Please upload a `.json` file.", ephemeral=True,
                )

            await interaction.response.defer(ephemeral=True)

            content = await file.read()
            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                return await interaction.followup.send(
                    f"Invalid JSON: {e}", ephemeral=True,
                )

            section_data = data.get(section)
            if not section_data:
                return await interaction.followup.send(
                    f"Section `{section}` not found in file.", ephemeral=True,
                )

            count = 0
            if section == "server_configs" and isinstance(section_data, list):
                for cfg in section_data:
                    if isinstance(cfg, dict):
                        cfg["guild_id"] = cfg.get("guild_id", str(interaction.guild.id))
                        ok = apply_server_config(CONFIGS_DIR, cfg, overwrite=True)
                        if ok:
                            count += 1
            elif section == "server_configs" and isinstance(section_data, dict):
                section_data["guild_id"] = section_data.get("guild_id", str(interaction.guild.id))
                ok = apply_server_config(CONFIGS_DIR, section_data, overwrite=True)
                if ok:
                    count = 1
            elif section in ("llm_settings",):
                ok = apply_llm_settings(HEALTH_PATH, section_data, overwrite=True)
                if ok:
                    count = 1
            else:
                return await interaction.followup.send(
                    f"Cannot apply section `{section}` via this command.", ephemeral=True,
                )

            embed = discord.Embed(
                title=f"Applied {section}",
                description=f"{count} item(s) applied successfully.",
                color=COLOR_SUCCESS,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception:
            logger.exception("[config apply] failed")
            with contextlib.suppress(Exception):
                await interaction.followup.send("An error occurred.", ephemeral=True)

    tree.add_command(config_group)
