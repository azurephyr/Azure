"""
Azure Discord Agentic Management Tools

Provides Discord server management capabilities for the agentic AI.
The bot can:
  - Analyze server state (roles, channels, categories, permissions)
  - Create roles with custom colors and permissions
  - Create channels (text, voice) and categories
  - Set channel permissions for specific roles
  - Execute multi-step plans with live progress updates

All actions require appropriate Discord bot permissions:
  - MANAGE_GUILD, MANAGE_CHANNELS, MANAGE_ROLES

Usage:
    from azure.discord_tools import DiscordManagementTools
    tools = DiscordManagementTools(bot)

    # Agentic setup
    plan = await tools.generate_plan(guild, "make the server good", llm)
    await tools.execute_plan(guild, plan, ctx)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import discord


def _resolve_color(color_name: str) -> int:
    basic = {
        "red": 0xE74C3C, "blue": 0x3498DB, "green": 0x2ECC71,
        "yellow": 0xF1C40F, "purple": 0x9B59B6, "orange": 0xE67E22,
        "pink": 0xE91E63, "white": 0xFFFFFF, "black": 0x000000,
        "grey": 0x95A5A6, "gray": 0x95A5A6, "brown": 0x8B4513,
        "cyan": 0x00FFFF, "magenta": 0xFF00FF, "lime": 0x00FF00,
    }
    return basic.get(color_name.lower().strip(), 0x99AAB5)


def _llm_reason(action: str, context: str = "") -> str:
    return f"Azure: {action} - {context}" if context else f"Azure: {action}"


def _embed_color(status: str = "info") -> int:
    colors = {"info": 0x3498DB, "success": 0x2ECC71, "warning": 0xE67E22, "error": 0xE74C3C}
    return colors.get(status, 0x3498DB)


@dataclass
class StepResult:
    success: bool
    action: str
    name: str
    detail: str = ""
    error: str = ""


class DiscordManagementTools:
    """
    Discord server management toolkit for agentic AI.
    """

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Server Analysis
    # ------------------------------------------------------------------

    async def get_server_state(self, guild) -> dict:
        """Get full server state for LLM analysis."""
        roles = []
        for r in guild.roles:
            if r.is_default():
                continue  # Skip @everyone
            roles.append({
                "name": r.name,
                "color": str(r.color),
                "position": r.position,
                "member_count": len(r.members),
                "permissions": [p[0] for p in r.permissions if p[1]],
            })

        channels = []
        for c in guild.channels:
            ch = {"name": c.name, "type": str(c.type), "id": c.id}
            if hasattr(c, "category") and c.category:
                ch["category"] = c.category.name
            channels.append(ch)

        categories = []
        for cat in guild.categories:
            categories.append({
                "name": cat.name,
                "channels": [c.name for c in cat.channels],
            })

        return {
            "server_name": guild.name,
            "member_count": guild.member_count,
            "roles": roles,
            "channels": channels,
            "categories": categories,
        }

    # ------------------------------------------------------------------
    # Action: Create Role
    # ------------------------------------------------------------------

    async def create_role(self, guild, name: str, color: str = None,
                          permissions: list[str] = None, hoist: bool = False,
                          mentionable: bool = False) -> StepResult:
        """Create a new role."""
        try:
            color_int = _resolve_color(color) if color else 0
            perms = self._build_permissions(permissions or [])

            await guild.create_role(
                name=name,
                color=color_int,
                permissions=perms,
                hoist=hoist,
                mentionable=mentionable,
                reason=_llm_reason("setup"),
            )
            return StepResult(
                success=True, action="create_role", name=name,
                detail=f"Color: {color or 'default'}, Permissions: {len(permissions or [])}",
            )
        except Exception as e:
            return StepResult(success=False, action="create_role", name=name, error=str(e))

    # ------------------------------------------------------------------
    # Action: Create Category
    # ------------------------------------------------------------------

    async def create_category(self, guild, name: str) -> StepResult:
        """Create a channel category."""
        try:
            await guild.create_category(name, reason=_llm_reason("setup"))
            return StepResult(success=True, action="create_category", name=name)
        except Exception as e:
            return StepResult(success=False, action="create_category", name=name, error=str(e))

    # ------------------------------------------------------------------
    # Action: Create Channel
    # ------------------------------------------------------------------

    async def create_channel(self, guild, name: str, channel_type: str = "text",
                              category: str = None, topic: str = None) -> StepResult:
        """Create a text or voice channel."""
        try:
            cat_obj = None
            if category:
                cat_obj = discord.utils.get(guild.categories, name=category)

            if channel_type == "voice":
                await guild.create_voice_channel(
                    name, category=cat_obj, reason=_llm_reason("setup")
                )
            elif channel_type == "stage_voice":
                await guild.create_stage_channel(
                    name, category=cat_obj, reason=_llm_reason("setup")
                )
            elif channel_type == "forum":
                await guild.create_forum(
                    name, category=cat_obj, reason=_llm_reason("setup")
                )
            else:  # text
                await guild.create_text_channel(
                    name, category=cat_obj, topic=topic,
                    reason=_llm_reason("setup")
                )
            return StepResult(success=True, action="create_channel", name=name,
                              detail=f"Type: {channel_type}, Category: {category or 'none'}")
        except Exception as e:
            return StepResult(success=False, action="create_channel", name=name, error=str(e))

    # ------------------------------------------------------------------
    # Action: Set Channel Permissions
    # ------------------------------------------------------------------

    async def set_channel_permissions(self, channel, role_name: str,
                                     allow: list[str] = None,
                                     deny: list[str] = None) -> StepResult:
        """Set permissions for a role in a channel."""
        try:
            import discord
            role = discord.utils.get(channel.guild.roles, name=role_name)
            if not role:
                return StepResult(
                    success=False, action="set_permissions", name=role_name,
                    error=f"Role '{role_name}' not found in {channel.guild.name}"
                )

            # PermissionOverwrite fields are set by permission NAME. Iterate the
            # requested name strings directly — do not funnel through
            # _build_permissions (which returns a discord.Permissions whose
            # iteration yields (name, value) tuples, not names).
            overwrite = discord.PermissionOverwrite()
            for perm in (allow or []):
                perm = perm.lower().strip()
                if hasattr(overwrite, perm):
                    setattr(overwrite, perm, True)
            for perm in (deny or []):
                perm = perm.lower().strip()
                if hasattr(overwrite, perm):
                    setattr(overwrite, perm, False)

            await channel.set_permissions(
                role, overwrite=overwrite, reason=_llm_reason("setup")
            )
            return StepResult(
                success=True, action="set_permissions", name=role_name,
                detail=f"Channel: {channel.name}, Allow: {allow or []}, Deny: {deny or []}",
            )
        except Exception as e:
            return StepResult(
                success=False, action="set_permissions", name=role_name, error=str(e)
            )

    # ------------------------------------------------------------------
    # Action: Delete Channel / Role
    # ------------------------------------------------------------------

    async def delete_channel(self, channel, safe: bool = True) -> StepResult:
        """Delete a channel. LLM decides if it should be protected."""
        try:
            await channel.delete(reason=_llm_reason("cleanup"))
            return StepResult(success=True, action="delete_channel", name=channel.name)
        except Exception as e:
            return StepResult(success=False, action="delete_channel", name=channel.name, error=str(e))

    async def delete_role(self, guild, role_name: str) -> StepResult:
        """Delete a role."""
        try:
            import discord
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                return StepResult(
                    success=False, action="delete_role", name=role_name,
                    error=f"Role '{role_name}' not found"
                )
            if role.is_default():
                return StepResult(
                    success=False, action="delete_role", name=role_name,
                    error="Cannot delete @everyone"
                )
            await role.delete(reason=_llm_reason("cleanup"))
            return StepResult(success=True, action="delete_role", name=role_name)
        except Exception as e:
            return StepResult(success=False, action="delete_role", name=role_name, error=str(e))

    # ------------------------------------------------------------------
    # Plan Generation (uses LLM)
    # ------------------------------------------------------------------

    async def generate_plan(self, guild, request: str, llm) -> dict:
        """
        Use the local LLM to generate a server management plan.

        Args:
            guild: Discord guild to analyze
            request: User's natural language request
            llm: LocalLLM instance

        Returns:
            dict with "analysis" and "steps" list
        """
        state = await self.get_server_state(guild)

        prompt = self._build_planning_prompt(state, request)

        messages = [
            {"role": "system", "content": f"You are a Discord server setup expert. User request: {request}\nGenerate a step-by-step plan."},
            {"role": "user", "content": prompt},
        ]

        # Run LLM in executor to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, lambda: llm.chat(messages, max_tokens=1024, temperature=0.3))
        plan = self._parse_plan(raw)
        return plan

    def _build_planning_prompt(self, state: dict, request: str) -> str:
        """Build the planning prompt for the LLM."""
        roles_str = ", ".join([r["name"] for r in state["roles"]]) or "(none)"
        channels_str = ", ".join([c["name"] for c in state["channels"]]) or "(none)"
        cats_str = ", ".join([c["name"] for c in state["categories"]]) or "(none)"

        return (
            f"You are a Discord server setup expert. User request: {request}\n"
            f"Generate a step-by-step plan.\n\n"
            f"SERVER STATE:\n"
            f"  Name: {state['server_name']}\n"
            f"  Members: {state['member_count']}\n"
            f"  Roles: {roles_str}\n"
            f"  Channels: {channels_str}\n"
            f"  Categories: {cats_str}\n\n"
            f"Return ONLY valid JSON with 'analysis' and 'steps'."
        )

    def _parse_plan(self, raw: str) -> dict:
        """Parse LLM output into a structured plan."""
        # Try to extract JSON from the response
        raw = raw.strip()
        # Find JSON block
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start:end + 1]
        try:
            plan = json.loads(raw)
            if "steps" not in plan:
                plan["steps"] = []
            if "analysis" not in plan:
                plan["analysis"] = "No analysis provided."
            return plan
        except json.JSONDecodeError:
            return {
                "analysis": "Failed to parse LLM plan. Using fallback.",
                "steps": [],
                "raw": raw,
            }

    # ------------------------------------------------------------------
    # Plan Execution with Live Progress
    # ------------------------------------------------------------------

    async def execute_plan(self, guild, plan: dict, ctx,
                           confirm_destructive: bool = True) -> list[StepResult]:
        """
        Execute a multi-step plan with live progress updates.

        Args:
            guild: Discord guild
            plan: dict with "steps" list
            ctx: Discord context (for sending progress messages)
            confirm_destructive: Whether to ask for confirmation before destructive actions

        Returns:
            List of StepResult for each step
        """
        import discord
        steps = plan.get("steps", [])
        if not steps:
            await ctx.send("No steps in the plan. Nothing to do.")
            return []

        # Build initial progress embed
        total = len(steps)
        embed = discord.Embed(
            title="🛠️ Agentic Server Setup",
            description=f"**Plan:** {plan.get('analysis', 'No analysis')}\n\n"
                        f"Steps: 0/{total} completed\n\n⏳ Starting...",
            color=_embed_color("info"),
        )
        progress_msg = await ctx.send(embed=embed)

        results = []
        for i, step in enumerate(steps, 1):
            action = step.get("action", "unknown")
            name = step.get("name", step.get("channel", "unknown"))

            # Update embed to show current step
            embed.description = (
                f"**Plan:** {plan.get('analysis', 'No analysis')}\n\n"
                f"Steps: {i - 1}/{total} completed\n\n"
                f"⏳ Current: {action} '{name}'..."
            )
            await progress_msg.edit(embed=embed)

            # Execute the step
            result = await self._execute_single_step(guild, step, confirm_destructive)
            results.append(result)

            # Update progress
            status_icon = "✅" if result.success else "❌"
            lines = [f"{status_icon} {result.action}: {result.name}"]
            if result.detail:
                lines.append(f"   └─ {result.detail}")
            if result.error:
                lines.append(f"   └─ Error: {result.error}")

            completed = [r for r in results if r.success]
            embed.description = (
                f"**Plan:** {plan.get('analysis', 'No analysis')}\n\n"
                f"Steps: {len(completed)}/{total} completed\n\n"
                + "\n".join(lines)
            )
            embed.color = _embed_color("success") if len(completed) == total else _embed_color("warning")
            await progress_msg.edit(embed=embed)

        # Final summary
        completed = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        embed.description = (
            f"**Plan:** {plan.get('analysis', 'No analysis')}\n\n"
            f"**Completed:** {len(completed)}/{total}\n"
            f"**Failed:** {len(failed)}\n\n"
            + "\n".join([f"{'✅' if r.success else '❌'} {r.action}: {r.name}" for r in results])
        )
        embed.color = _embed_color("success") if len(failed) == 0 else (_embed_color("warning") if len(completed) > 0 else _embed_color("error"))
        await progress_msg.edit(embed=embed)

        return results

    async def _execute_single_step(self, guild, step: dict, confirm_destructive: bool) -> StepResult:
        """Execute a single plan step."""
        import discord
        action = step.get("action", "unknown")
        name = step.get("name", step.get("channel", "unknown"))

        if action == "create_role":
            return await self.create_role(
                guild, name=name,
                color=step.get("color"),
                permissions=step.get("permissions", []),
                hoist=step.get("hoist", False),
                mentionable=step.get("mentionable", False),
            )

        elif action == "create_category":
            return await self.create_category(guild, name=name)

        elif action == "create_channel":
            return await self.create_channel(
                guild, name=name,
                channel_type=step.get("type", "text"),
                category=step.get("category"),
                topic=step.get("topic"),
            )

        elif action == "set_permissions":
            channel = discord.utils.get(guild.channels, name=step.get("channel", ""))
            if not channel:
                return StepResult(
                    success=False, action=action, name=name,
                    error=f"Channel '{step.get('channel')}' not found"
                )
            return await self.set_channel_permissions(
                channel, role_name=step.get("role", ""),
                allow=step.get("allow", []),
                deny=step.get("deny", []),
            )

        elif action == "delete_channel":
            channel = discord.utils.get(guild.channels, name=name)
            if not channel:
                return StepResult(success=False, action=action, name=name, error="Channel not found")
            return await self.delete_channel(channel)

        elif action == "delete_role":
            return await self.delete_role(guild, name)

        else:
            return StepResult(
                success=False, action=action, name=name,
                error=f"Unknown action: {action}"
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_permissions(self, perm_list: list[str]):
        """Build Discord permissions from string list."""
        import discord
        perms = discord.Permissions()
        for p in perm_list:
            p_lower = p.lower().strip()
            if hasattr(perms, p_lower):
                setattr(perms, p_lower, True)
        return perms
