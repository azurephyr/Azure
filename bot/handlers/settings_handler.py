"""Discord /settings slash commands for LLM provider configuration.

Covers all 7 providers: openai, anthropic, google, groq, mistral, openrouter, nararouter.
Admin-only. Uses discord.py 2.4.0 slash commands with autocomplete and UI.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands

if TYPE_CHECKING:
    from azure.model_selector import ModelSelector

logger = logging.getLogger("azure.discord.settings")


# ---------------------------------------------------------------------------
# .env persistence helpers
# ---------------------------------------------------------------------------

_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
_env_write_lock = threading.Lock()


def _env_read_key(key: str) -> str:
    """Read a single key's value from the .env file. Returns '' if not found."""
    if not _ENV_PATH.exists():
        return ""
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$", re.MULTILINE)
    match = pattern.search(_ENV_PATH.read_text(encoding="utf-8"))
    if match:
        return match.group(1).strip()
    return ""


def _env_write_key(key: str, value: str) -> None:
    """Write or update a key in the .env file. Preserves comments and structure."""
    with _env_write_lock:
        text = _ENV_PATH.read_text(encoding="utf-8") if _ENV_PATH.exists() else ""
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        line = f"{key}={value}"
        if pattern.search(text):
            text = pattern.sub(line, text)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text += line + "\n"
        _ENV_PATH.write_text(text, encoding="utf-8")


def _env_remove_key(key: str) -> None:
    """Remove a key line from the .env file."""
    if not _ENV_PATH.exists():
        return
    text = _ENV_PATH.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(key)}=.*\n?", re.MULTILINE)
    text = pattern.sub("", text)
    _ENV_PATH.write_text(text, encoding="utf-8")

PROVIDER_CHOICES = [
    app_commands.Choice(name="Auto (Smart Select)", value="auto"),
    app_commands.Choice(name="OpenAI", value="openai"),
    app_commands.Choice(name="Anthropic", value="anthropic"),
    app_commands.Choice(name="Google AI Studio", value="google"),
    app_commands.Choice(name="Groq", value="groq"),
    app_commands.Choice(name="Mistral AI", value="mistral"),
    app_commands.Choice(name="OpenRouter", value="openrouter"),
    app_commands.Choice(name="NaraRouter", value="nararouter"),
]

SETTINGS_COLOR = 0x5865F2
SUCCESS_COLOR = 0x57F287
ERROR_COLOR = 0xED4245
WARNING_COLOR = 0xFEE75C


def _admin_check(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator


def _is_user_allowed(interaction: discord.Interaction) -> tuple[bool, str]:
    """Check if user is allowed to use commands. Returns (allowed, reason)."""
    user = interaction.user
    # Check access control bans
    try:
        from bot.context import ctx
        if ctx and ctx.db:
            user_perm = ctx.db.get_access_control(str(user.id))
            if user_perm == "deny":
                return False, "You are banned from using this bot."
            if interaction.guild:
                guild_perm = ctx.db.get_access_control(str(interaction.guild.id))
                if guild_perm == "deny":
                    return False, "This server is banned from using this bot."
    except Exception:
        pass
    # Check admin (handle DM interactions where user is a Member)
    try:
        if not user.guild_permissions.administrator:
            return False, "Admin only."
    except AttributeError:
        return False, "This command can only be used in a server."
    return True, ""


_PROVIDER_KEY_ENV = {
    "openai": "AZURE_OPENAI_API_KEY",
    "anthropic": "AZURE_ANTHROPIC_API_KEY",
    "google": "AZURE_GOOGLE_API_KEY",
    "groq": "AZURE_GROQ_API_KEY",
    "mistral": "AZURE_MISTRAL_API_KEY",
    "openrouter": "AZURE_OPENROUTER_API_KEY",
    "nararouter": "AZURE_NARAROUTER_API_KEY",
}


def _sync_env_from_selector(selector: ModelSelector) -> None:
    settings = selector.get_settings()
    provider = settings.get("provider", "openrouter")
    os.environ["AZURE_LLM_PROVIDER"] = provider
    _env_write_key("AZURE_LLM_PROVIDER", provider)


def _reload_active_llm() -> None:
    """Hot-reload the running ApiLLM instance after settings change."""
    import logging
    _log = logging.getLogger("azure.settings")
    try:
        from azure.api_llm import ApiLLM
        if ApiLLM._active_llm is not None:
            ok = ApiLLM._active_llm.reload_from_selector()
            _log.info(
                "[settings] reload ActiveLLM provider=%s model=%s ok=%s",
                ApiLLM._active_llm._provider,
                ApiLLM._active_llm._model,
                ok,
            )
        else:
            _log.warning("[settings] reload skipped: ApiLLM._active_llm is None (LLM not initialized)")
    except Exception as e:
        _log.warning("[settings] reload ActiveLLM failed: %s", e)


class _ModelAutocomplete:
    def __init__(self, selector: ModelSelector):
        self.selector = selector
        self._model_cache: dict[str, list[dict]] = {}

    def _get_provider(self, interaction: discord.Interaction) -> str:
        try:
            ns = interaction.namespace
            for attr in ("provider", "name"):
                val = getattr(ns, attr, None)
                if val and val in ("openai", "anthropic", "google", "groq", "mistral", "openrouter", "nararouter"):
                    return str(val)
        except Exception as e:
            logger.warning("Failed to read interaction namespace: %s", e)
        return self.selector.get_active_config().get("provider", "openrouter")

    def _get_models_for(self, provider: str) -> list[dict]:
        if provider in self._model_cache:
            return self._model_cache[provider]

        try:
            models = self.selector.fetch_provider_models(provider)
            self._model_cache[provider] = models
            return models
        except Exception as e:
            logger.warning("Failed to fetch %s models: %s", provider, e)

        from azure.model_catalog import PROVIDER_CATALOGS
        cat = PROVIDER_CATALOGS.get(provider, {})
        models = [
            {"id": m.id, "name": m.name, "is_free": m.free_tier,
             "context_length": m.context_window, "prompt_price": m.input_price,
             "completion_price": m.output_price, "description": m.description}
            for m in cat.get("models", [])
        ]
        self._model_cache[provider] = models
        return models

    async def get_choices(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
        provider = self._get_provider(interaction)
        try:
            models = await asyncio.to_thread(self._get_models_for, provider)
        except Exception as e:
            logger.warning("Model list fetch failed for provider %s: %s", provider, e)
            models = []

        query = current.lower()
        results: list[app_commands.Choice] = []
        for m in models:
            mid = m.get("id", "") if isinstance(m, dict) else str(m)
            name = m.get("name", mid) if isinstance(m, dict) else mid
            if query and query not in mid.lower() and query not in name.lower():
                continue
            is_free = m.get("is_free", False) if isinstance(m, dict) else ":free" in mid
            m.get("description", "") if isinstance(m, dict) else ""
            label = f"{name}"
            if is_free:
                label = f"\U0001f193 {label}"
            results.append(app_commands.Choice(name=label[:100], value=mid[:100]))
            if len(results) >= 25:
                break
        return results


def _build_health_embed(selector: ModelSelector) -> discord.Embed:
    settings = selector.get_settings()
    health = selector.get_provider_health()
    config = selector.get_active_config()

    embed = discord.Embed(title="\u2699\ufe0f LLM Settings", color=SETTINGS_COLOR)

    provider_label = selector.get_provider_display_name(config["provider"])
    smart_str = "ON" if config["smart_mode"] else "OFF"
    embed.add_field(
        name="Active Configuration",
        value=(
            f"**Provider:** {provider_label}\n"
            f"**Model:** `{config['model']}`\n"
            f"**Smart Mode:** {smart_str}\n"
            f"**Tier:** {config.get('tier', 'unknown')}"
        ),
        inline=False,
    )

    fb_name = selector.get_provider_display_name(settings.get("fallback_provider", ""))
    embed.add_field(
        name="Fallback",
        value=f"**Provider:** {fb_name}\n**Model:** `{settings.get('fallback_model', 'none')}`",
        inline=False,
    )

    lines = []
    from azure.model_selector import ALL_PROVIDERS
    for pname in ALL_PROVIDERS:
        h = health.get(pname, {})
        has_key = h.get("has_api_key", False)
        if not has_key:
            emoji = "\u274c"
            status = "No key"
        else:
            cf = h.get("consecutive_failures", 0)
            if cf >= 5:
                emoji = "\u26a0\ufe0f"
            elif cf > 0:
                emoji = "\U0001f7e1"
            else:
                emoji = "\u2705"
            status = f"Tier: {h.get('tier', '?')}"
        display = selector.get_provider_display_name(pname)
        lines.append(f"{emoji} **{display}**: {status}")

    embed.add_field(name="Provider Health", value="\n".join(lines), inline=False)
    return embed


class ProviderSelectView(discord.ui.View):
    def __init__(self, selector: ModelSelector):
        super().__init__(timeout=120)
        self.selector = selector

    @discord.ui.select(
        placeholder="Choose a provider...",
        options=[
            discord.SelectOption(label="Auto (Smart Select)", value="auto", description="Bot picks best provider", emoji="\u2699\ufe0f"),
            discord.SelectOption(label="OpenAI", value="openai", description="GPT-4o, GPT-4o-mini", emoji="\U0001f916"),
            discord.SelectOption(label="Anthropic", value="anthropic", description="Claude 4, Claude 3.5", emoji="\U0001f9e0"),
            discord.SelectOption(label="Google AI Studio", value="google", description="Gemini 2.5, Gemini 2.0", emoji="\U0001f310"),
            discord.SelectOption(label="Groq", value="groq", description="Llama 3.3, Mixtral", emoji="\u26a1"),
            discord.SelectOption(label="Mistral AI", value="mistral", description="Mistral Large, Medium", emoji="\U0001f32c\ufe0f"),
            discord.SelectOption(label="OpenRouter", value="openrouter", description="Gateway to 200+ models", emoji="\U0001f517"),
            discord.SelectOption(label="NaraRouter", value="nararouter", description="Multi-provider gateway", emoji="\U0001f310"),
        ],
    )
    async def provider_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        allowed, reason = _is_user_allowed(interaction)
        if not allowed:
            return await interaction.response.send_message(reason, ephemeral=True)
        value = select.values[0]
        if value == "auto":
            self.selector.update_settings(smart_mode=True)
            _sync_env_from_selector(self.selector)
            _reload_active_llm()
            await interaction.response.send_message(
                "\u2699\ufe0f **Smart mode enabled** — the bot will auto-select the best provider.",
                ephemeral=True,
            )
        else:
            model = await asyncio.to_thread(self.selector.get_recommended_model, value)
            self.selector.update_settings(smart_mode=False, provider=value, model=model)
            _sync_env_from_selector(self.selector)
            _reload_active_llm()
            display = self.selector.get_provider_display_name(value)
            await interaction.response.send_message(
                f"\u2705 Provider: **{display}**\nModel: `{model}`",
                ephemeral=True,
            )

    @discord.ui.button(label="Smart Mode: Toggle", style=discord.ButtonStyle.secondary)
    async def smart_toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        allowed, reason = _is_user_allowed(interaction)
        if not allowed:
            return await interaction.response.send_message(reason, ephemeral=True)
        settings = self.selector.get_settings()
        new_val = not settings.get("smart_mode", True)
        self.selector.update_settings(smart_mode=new_val)
        _sync_env_from_selector(self.selector)
        _reload_active_llm()
        await interaction.response.send_message(
            f"\u2699\ufe0f Smart mode: **{'ON' if new_val else 'OFF'}**",
            ephemeral=True,
        )


class ResetConfirmView(discord.ui.View):
    def __init__(self, selector: ModelSelector):
        super().__init__(timeout=30)
        self.selector = selector

    @discord.ui.button(label="Yes, Reset", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        allowed, reason = _is_user_allowed(interaction)
        if not allowed:
            return await interaction.response.send_message(reason, ephemeral=True)
        self.selector.update_settings(
            smart_mode=True,
            provider="openrouter",
            model="nvidia/nemotron-3-ultra-550b-a55b:free",
            fallback_provider="openrouter",
            fallback_model="nvidia/nemotron-3-super-120b-a12b:free",
        )
        _sync_env_from_selector(self.selector)
        _reload_active_llm()
        self.stop()
        await interaction.response.edit_message(content="\u2705 Settings reset to defaults.", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="\u274c Reset cancelled.", view=None)


def register_settings(tree: app_commands.CommandTree, selector: ModelSelector) -> None:
    from azure.api_llm import ApiLLM
    ApiLLM._model_selector = selector
    _sync_env_from_selector(selector)
    ac = _ModelAutocomplete(selector)

    # ── /settings ────────────────────────────────────────────────────
    @tree.command(name="settings", description="Configure LLM providers and models (admin only)")
    @app_commands.describe(action="What to configure")
    @app_commands.choices(action=[
        app_commands.Choice(name="View current settings", value="view"),
        app_commands.Choice(name="Toggle smart mode", value="smart"),
        app_commands.Choice(name="Test current config", value="test"),
        app_commands.Choice(name="Reset to defaults", value="reset"),
        app_commands.Choice(name="Refresh all provider models", value="refresh"),
        app_commands.Choice(name="Detect Google tier", value="google_tier"),
    ])
    async def settings_cmd(interaction: discord.Interaction, action: str):
        allowed, reason = _is_user_allowed(interaction)
        if not allowed:
            return await interaction.response.send_message(reason, ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        if action == "view":
            embed = _build_health_embed(selector)
            view = ProviderSelectView(selector)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        elif action == "smart":
            settings = selector.get_settings()
            new_val = not settings.get("smart_mode", True)
            selector.update_settings(smart_mode=new_val)
            _sync_env_from_selector(selector)
            _reload_active_llm()
            state = "ON" if new_val else "OFF"
            embed = discord.Embed(
                title=f"Smart Mode: {state}",
                description=(
                    "When **ON**, the bot automatically picks the best provider and model "
                    "based on health, rate limits, and availability.\n\n"
                    "When **OFF**, the bot uses your manually selected provider and model."
                ),
                color=SUCCESS_COLOR if new_val else WARNING_COLOR,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == "test":
            config = selector.get_active_config()
            try:
                result = await asyncio.to_thread(selector.test_provider, config["provider"], config["model"])
                embed = _test_result_embed(result)
            except Exception as e:
                embed = discord.Embed(
                    title="Test Failed",
                    description=f"Error: {e}",
                    color=WARNING_COLOR,
                )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == "reset":
            view = ResetConfirmView(selector)
            await interaction.followup.send(
                "\u26a0\ufe0f Are you sure you want to reset all LLM settings to defaults?",
                view=view, ephemeral=True,
            )

        elif action == "refresh":
            try:
                from azure.model_selector import ALL_PROVIDERS
                provider_lines = []
                for pname in ALL_PROVIDERS:
                    try:
                        models = await asyncio.to_thread(selector.fetch_provider_models, pname, True)
                        free = sum(1 for m in models if isinstance(m, dict) and m.get("is_free"))
                        paid = len(models) - free
                        provider_lines.append(f"**{selector.get_provider_display_name(pname)}:** {len(models)} models ({free} free, {paid} paid)")
                    except Exception as e:
                        provider_lines.append(f"**{selector.get_provider_display_name(pname)}:** Error — {e}")
                embed = discord.Embed(
                    title="\U0001f504 All Provider Models Refreshed",
                    description="\n".join(provider_lines),
                    color=SUCCESS_COLOR,
                )
            except Exception as e:
                embed = discord.Embed(
                    title="Refresh Failed",
                    description=f"Error: {e}",
                    color=WARNING_COLOR,
                )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif action == "google_tier":
            tier = await asyncio.to_thread(selector.detect_google_tier)
            embed = discord.Embed(
                title="Google AI Studio Tier Detection",
                description=f"**Detected tier:** {tier}",
                color=SUCCESS_COLOR if tier in ("free", "paid") else WARNING_COLOR,
            )
            if tier == "no_key":
                embed.description = "No Google API key configured."
            await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /discover ──────────────────────────────────────────────────
    @tree.command(name="discover", description="Probe all API keys and discover available models (admin only)")
    async def discover_cmd(interaction: discord.Interaction):
        try:
            allowed, reason = _is_user_allowed(interaction)
            if not allowed:
                return await interaction.response.send_message(reason, ephemeral=True)
            await interaction.response.defer(ephemeral=True)

            from azure.model_selector import ALL_PROVIDERS, _first_env
            provider_details = []
            best_recommendation = None
            has_any_key = False

            for pname in ALL_PROVIDERS:
                key_envs = {
                    "openai": ("AZURE_OPENAI_API_KEY", "OPENAI_API_KEY"),
                    "anthropic": ("AZURE_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
                    "google": ("AZURE_GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
                    "groq": ("AZURE_GROQ_API_KEY", "GROQ_API_KEY"),
                    "mistral": ("AZURE_MISTRAL_API_KEY", "MISTRAL_API_KEY"),
                    "openrouter": ("AZURE_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
                    "nararouter": ("AZURE_NARAROUTER_API_KEY", "NARAROUTER_API_KEY"),
                }
                envs = key_envs.get(pname, ())
                key = _first_env(*envs)
                if not key:
                    provider_details.append(f"\u274c **{selector.get_provider_display_name(pname)}** — no key")
                    continue
                has_any_key = True
                try:
                    models = await asyncio.to_thread(selector.fetch_provider_models, pname, True)
                    if not models:
                        provider_details.append(f"\u26a0\ufe0f **{selector.get_provider_display_name(pname)}** — key found, but returned empty model list")
                        continue
                    free = sum(1 for m in models if isinstance(m, dict) and m.get("is_free"))
                    paid = len(models) - free
                    top = models[:3]
                    top_names = ", ".join(
                        f"`{m.get('id', '?')}`" + (" \U0001f193" if m.get("is_free") else "")
                        for m in top
                    )
                    provider_details.append(
                        f"\u2705 **{selector.get_provider_display_name(pname)}** — {len(models)} models "
                        f"({free} free, {paid} paid)\n"
                        f"  \u2192 {top_names}..."
                    )
                    if not best_recommendation and free > 0:
                        best_recommendation = (pname, models[0]["id"])
                except Exception as e:
                    provider_details.append(f"\u274c **{selector.get_provider_display_name(pname)}** — {e}")

            if not has_any_key:
                embed = discord.Embed(
                    title="\U0001f50d No API Keys Found",
                    description=(
                        "No API keys are configured for any provider.\n\n"
                        "Use `/setkey` to add a key, e.g.:\n"
                        "\u2022 `/setkey provider:google key:YOUR_KEY`\n"
                        "\u2022 `/setkey provider:openrouter key:YOUR_KEY`"
                    ),
                    color=WARNING_COLOR,
                )
            else:
                desc = "\n\n".join(provider_details)
                embed = discord.Embed(
                    title="\U0001f50d Provider Discovery Complete",
                    description=desc,
                    color=SUCCESS_COLOR,
                )
                if best_recommendation:
                    embed.set_footer(text=f"Tip: try /provider {best_recommendation[0]} to use {best_recommendation[1]}")

            embed.set_footer(text="Use /settings refresh to re-scan")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            logger.exception("[discover] command failed")

    # ── /provider ────────────────────────────────────────────────────
    @tree.command(name="provider", description="Quick-switch LLM provider (admin only)")
    @app_commands.describe(name="Provider to use")
    @app_commands.choices(name=PROVIDER_CHOICES)
    async def provider_cmd(interaction: discord.Interaction, name: str):
        try:
            allowed, reason = _is_user_allowed(interaction)
            if not allowed:
                return await interaction.response.send_message(reason, ephemeral=True)
            if name == "auto":
                selector.update_settings(smart_mode=True)
                _sync_env_from_selector(selector)
                _reload_active_llm()
                await interaction.response.send_message(
                    "\u2699\ufe0f **Smart mode enabled** — auto-selecting best provider.",
                    ephemeral=True,
                )
            else:
                model = await asyncio.to_thread(selector.get_recommended_model, name)
                selector.update_settings(smart_mode=False, provider=name, model=model)
                _sync_env_from_selector(selector)
                _reload_active_llm()
                display = selector.get_provider_display_name(name)
                await interaction.response.send_message(
                    f"\u2705 Provider: **{display}**\nModel: `{model}`",
                    ephemeral=True,
                )
        except Exception:
            logger.exception("[provider] command failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── /model ───────────────────────────────────────────────────────
    @tree.command(name="model", description="Set a specific LLM model (admin only)")
    @app_commands.describe(name="Model ID (use autocomplete)")
    async def model_cmd(interaction: discord.Interaction, name: str):
        try:
            allowed, reason = _is_user_allowed(interaction)
            if not allowed:
                return await interaction.response.send_message(reason, ephemeral=True)
            selector.update_settings(model=name, smart_mode=False)
            _sync_env_from_selector(selector)
            _reload_active_llm()
            await interaction.response.send_message(
                f"\u2705 Model set to `{name}`",
                ephemeral=True,
            )
        except Exception:
            logger.exception("[model] command failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    @model_cmd.autocomplete("name")
    async def model_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
        return await ac.get_choices(interaction, current)

    # ── /fallback ────────────────────────────────────────────────────
    @tree.command(name="fallback", description="Set fallback provider and model (admin only)")
    @app_commands.describe(provider="Fallback provider", model="Fallback model")
    @app_commands.choices(provider=PROVIDER_CHOICES)
    async def fallback_cmd(interaction: discord.Interaction, provider: str, model: str):
        try:
            allowed, reason = _is_user_allowed(interaction)
            if not allowed:
                return await interaction.response.send_message(reason, ephemeral=True)
            if provider == "auto":
                provider = "openrouter"
            selector.update_settings(fallback_provider=provider, fallback_model=model)
            _sync_env_from_selector(selector)
            display = selector.get_provider_display_name(provider)
            await interaction.response.send_message(
                f"\u2705 Fallback: **{display}** / `{model}`",
                ephemeral=True,
            )
        except Exception:
            logger.exception("[fallback] command failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    @fallback_cmd.autocomplete("model")
    async def fallback_model_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
        return await ac.get_choices(interaction, current)

    # ── /test ────────────────────────────────────────────────────────
    @tree.command(name="test", description="Test current LLM configuration (admin only)")
    async def test_cmd(interaction: discord.Interaction):
        try:
            allowed, reason = _is_user_allowed(interaction)
            if not allowed:
                return await interaction.response.send_message(reason, ephemeral=True)
            await interaction.response.defer(ephemeral=True)
            config = selector.get_active_config()
            try:
                result = await asyncio.to_thread(selector.test_provider, config["provider"], config["model"])
                embed = _test_result_embed(result)
            except Exception as e:
                embed = discord.Embed(
                    title="Test Failed",
                    description=f"Error: {e}",
                    color=WARNING_COLOR,
                )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            logger.exception("[test] command failed")

    # ── /setkey ────────────────────────────────────────────────────
    @tree.command(name="setkey", description="Set API key for a provider (saved to .env)")
    @app_commands.describe(provider="Provider to set key for", key="Your API key")
    @app_commands.choices(provider=PROVIDER_CHOICES)
    async def setkey_cmd(interaction: discord.Interaction, provider: str, key: str):
        try:
            allowed, reason = _is_user_allowed(interaction)
            if not allowed:
                return await interaction.response.send_message(reason, ephemeral=True)
            if provider == "auto":
                return await interaction.response.send_message(
                    "Cannot set a key for 'auto' — pick a specific provider.", ephemeral=True,
                )
            env_key = _PROVIDER_KEY_ENV.get(provider)
            if not env_key:
                return await interaction.response.send_message(
                    f"Unknown provider `{provider}`.", ephemeral=True,
                )
            os.environ[env_key] = key
            _env_write_key(env_key, key)
            display = selector.get_provider_display_name(provider)
            await interaction.response.send_message(
                f"\u2705 API key for **{display}** saved to `.env` (`{env_key}`) and loaded.",
                ephemeral=True,
            )
        except Exception:
            logger.exception("[setkey] command failed")
            with contextlib.suppress(Exception):
                await interaction.response.send_message("An error occurred.", ephemeral=True)

    # ── Startup: apply persisted model/provider to the running LLM ──────
    _reload_active_llm()


def _test_result_embed(result: dict) -> discord.Embed:
    if result["success"]:
        return discord.Embed(
            title="\u2705 Test Successful",
            description=(
                f"**Provider:** {result['provider']}\n"
                f"**Model:** `{result['model']}`\n"
                f"**Latency:** {result['latency']}s"
            ),
            color=SUCCESS_COLOR,
        )
    return discord.Embed(
        title="\u274c Test Failed",
        description=(
            f"**Provider:** {result['provider']}\n"
            f"**Model:** `{result['model']}`\n"
            f"**Error:** {result['error']}\n"
            f"**Latency:** {result.get('latency', '?')}s"
        ),
        color=ERROR_COLOR,
    )
