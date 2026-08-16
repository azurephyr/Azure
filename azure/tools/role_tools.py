"""Role management tools."""
import logging

import discord

from .server_tools import _llm_reason
from .types import StepResult

logger = logging.getLogger("tools.role_tools")


def _role_error(guild, role) -> str | None:
    if getattr(role, "managed", False):
        return "Managed Discord roles cannot be changed"
    bot_role = getattr(getattr(guild, "me", None), "top_role", None)
    if bot_role is not None and role is not None and not bot_role > role:
        return "Bot's role is not high enough to manage this role"
    return None


class RoleToolsMixin:
    """Mixin providing role management for DiscordManagementTools."""

    async def create_role(self, guild: discord.Guild, name: str, color: str = None,
                          permissions: list = None, hoist: bool = False,
                          mentionable: bool = False, position: int = None) -> StepResult:
        try:
            if position is not None:
                bot_position = getattr(getattr(guild, "me", None), "top_role", None)
                bot_position = getattr(bot_position, "position", None)
                if bot_position is not None and position >= bot_position:
                    return StepResult(success=False, action="create_role", name=name, error="Requested role position is above the bot's highest role")
            color_int = self._parse_color(color)
            perms = self._build_permissions(permissions or [])

            logger.info(f"[tools] CREATE ROLE: name='{name}', color='{color}' -> {color_int}, guild='{guild.name}' ({guild.id})")

            role = await guild.create_role(
                name=name, color=color_int, permissions=perms,
                hoist=hoist, mentionable=mentionable,
                reason=_llm_reason("setup"),
            )
            logger.info(f"[tools] ROLE CREATED: id={role.id}, name='{role.name}', color={role.color}")

            if position is not None:
                try:
                    await role.edit(position=position)
                except Exception as e:
                    logger.error(f"[tools] position edit error: {e}")

            return StepResult(
                success=True, action="create_role", name=name,
                detail=f"Color: {color or 'default'}, ID: {role.id}",
                target_id=role.id,
                before_state=None,
                after_state={"name": name, "color": str(color_int), "permissions": permissions or []},
            )
        except Exception as e:
            return StepResult(success=False, action="create_role", name=name, error=str(e))

    async def edit_role(self, guild: discord.Guild, role_name: str, **kwargs) -> StepResult:
        try:
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                return StepResult(success=False, action="edit_role", name=role_name, error="Role not found")
            role_error = _role_error(guild, role)
            if role_error:
                return StepResult(success=False, action="edit_role", name=role_name, error=role_error)
            before = {"name": role.name, "color": str(role.color), "hoist": role.hoist, "mentionable": role.mentionable}
            if role.icon:
                before["icon"] = True
            if "color" in kwargs:
                kwargs["color"] = self._parse_color(kwargs["color"])
            if "permissions" in kwargs:
                kwargs["permissions"] = self._build_permissions(kwargs["permissions"])
            if "icon" in kwargs and isinstance(kwargs["icon"], str) and kwargs["icon"].lower() == "none":
                kwargs["icon"] = None
            await role.edit(**kwargs, reason=_llm_reason("edit"))
            after = {"name": role.name, "color": str(role.color), "hoist": role.hoist, "mentionable": role.mentionable}
            return StepResult(success=True, action="edit_role", name=role_name, detail="Updated", before_state=before, after_state=after, target_id=role.id)
        except Exception as e:
            return StepResult(success=False, action="edit_role", name=role_name, error=str(e))

    async def delete_role(self, guild: discord.Guild, role_name: str) -> StepResult:
        try:
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                return StepResult(success=False, action="delete_role", name=role_name, error="Role not found")
            if role.is_default():
                return StepResult(success=False, action="delete_role", name=role_name, error="Cannot delete @everyone")
            role_error = _role_error(guild, role)
            if role_error:
                return StepResult(success=False, action="delete_role", name=role_name, error=role_error)
            before = {"name": role.name, "color": str(role.color), "permissions": [p[0] for p in role.permissions if p[1]]}
            await role.delete(reason=_llm_reason("cleanup"))
            return StepResult(success=True, action="delete_role", name=role_name, before_state=before, after_state=None)
        except Exception as e:
            return StepResult(success=False, action="delete_role", name=role_name, error=str(e))

    async def assign_role(self, guild: discord.Guild, member_name_or_id: str, role_name: str) -> StepResult:
        try:
            member = await self._resolve_member(guild, member_name_or_id)
            if not member:
                return StepResult(success=False, action="assign_role", name=role_name, error=f"Member '{member_name_or_id}' not found")
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                return StepResult(success=False, action="assign_role", name=role_name, error="Role not found")
            role_error = _role_error(guild, role)
            if role_error:
                return StepResult(success=False, action="assign_role", name=role_name, error=role_error)
            await member.add_roles(role, reason=_llm_reason("management"))
            return StepResult(success=True, action="assign_role", name=role_name, detail=f"Assigned to {member.display_name}")
        except Exception as e:
            return StepResult(success=False, action="assign_role", name=role_name, error=str(e))

    async def remove_role(self, guild: discord.Guild, member_name_or_id: str, role_name: str) -> StepResult:
        try:
            member = await self._resolve_member(guild, member_name_or_id)
            if not member:
                return StepResult(success=False, action="remove_role", name=role_name, error=f"Member '{member_name_or_id}' not found")
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                return StepResult(success=False, action="remove_role", name=role_name, error="Role not found")
            role_error = _role_error(guild, role)
            if role_error:
                return StepResult(success=False, action="remove_role", name=role_name, error=role_error)
            await member.remove_roles(role, reason=_llm_reason("management"))
            return StepResult(success=True, action="remove_role", name=role_name, detail=f"Removed from {member.display_name}")
        except Exception as e:
            return StepResult(success=False, action="remove_role", name=role_name, error=str(e))
