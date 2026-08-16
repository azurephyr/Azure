"""
RoleContext — Deep Role Mirroring for Azure.

Captures a user's LIVE Discord role hierarchy and translates it into a
structured security context that the LLM and planning engine can reason about.

This eliminates the binary `is_admin` bool — the LLM now knows:
  - What roles the user holds (ordered by position in the hierarchy)
  - What permission tier they are: OWNER, ADMIN, MODERATOR, MEMBER, GUEST
  - Exactly which Discord permission flags they have
  - Which tool-tiers they are allowed to invoke

Used by: CognitivePipeline, PlanningEngine, ReasonerAgent
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

# ---------------------------------------------------------------------------
# Permission tier — coarser than Discord's 40+ flags
# ---------------------------------------------------------------------------

class PermissionTier(StrEnum):
    OWNER     = "OWNER"      # Server owner — unrestricted
    ADMIN     = "ADMIN"      # administrator flag set
    MODERATOR = "MODERATOR"  # Has ban_members, kick_members, or manage_messages
    MEMBER    = "MEMBER"     # Verified member with no special perms
    GUEST     = "GUEST"      # Unverified / no roles


# ---------------------------------------------------------------------------
# Tool permission matrix
# ---------------------------------------------------------------------------

# Which tiers are allowed to invoke which tool groups.
# Anything NOT listed here is implicitly blocked for that tier.
TOOL_PERMISSIONS: dict[str, list[PermissionTier]] = {
    # Tier 1 — Safe reads (any member can trigger these via the bot)
    "get_server_info":    [PermissionTier.GUEST, PermissionTier.MEMBER, PermissionTier.MODERATOR, PermissionTier.ADMIN, PermissionTier.OWNER],
    "list_channels":      [PermissionTier.GUEST, PermissionTier.MEMBER, PermissionTier.MODERATOR, PermissionTier.ADMIN, PermissionTier.OWNER],
    "list_roles":         [PermissionTier.GUEST, PermissionTier.MEMBER, PermissionTier.MODERATOR, PermissionTier.ADMIN, PermissionTier.OWNER],
    "list_members":       [PermissionTier.MEMBER, PermissionTier.MODERATOR, PermissionTier.ADMIN, PermissionTier.OWNER],

    # Tier 2 — Moderation (Moderator+)
    "timeout_member":     [PermissionTier.MODERATOR, PermissionTier.ADMIN, PermissionTier.OWNER],
    "delete_message":     [PermissionTier.MODERATOR, PermissionTier.ADMIN, PermissionTier.OWNER],
    "warn_member":        [PermissionTier.MODERATOR, PermissionTier.ADMIN, PermissionTier.OWNER],

    # Tier 3 — Destructive (Admin+)
    "kick_member":        [PermissionTier.ADMIN, PermissionTier.OWNER],
    "ban_member":         [PermissionTier.ADMIN, PermissionTier.OWNER],
    "create_channel":     [PermissionTier.ADMIN, PermissionTier.OWNER],
    "delete_channel":     [PermissionTier.ADMIN, PermissionTier.OWNER],
    "create_role":        [PermissionTier.ADMIN, PermissionTier.OWNER],
    "delete_role":        [PermissionTier.ADMIN, PermissionTier.OWNER],
    "assign_role":        [PermissionTier.ADMIN, PermissionTier.OWNER],
    "set_permissions":    [PermissionTier.ADMIN, PermissionTier.OWNER],
    "sync_permissions":   [PermissionTier.ADMIN, PermissionTier.OWNER],
    "save_template":      [PermissionTier.ADMIN, PermissionTier.OWNER],
    "load_template":      [PermissionTier.ADMIN, PermissionTier.OWNER],

    # Tier 4 — Owner only
    "transfer_ownership": [PermissionTier.OWNER],
    "delete_server":      [PermissionTier.OWNER],
}


# ---------------------------------------------------------------------------
# RoleContext dataclass
# ---------------------------------------------------------------------------

@dataclass
class RoleContext:
    """
    Mirrors a Discord member's live role/permission state into the cognitive pipeline.

    Build this from a discord.Member object using `RoleContext.from_member(member)`.
    When no guild is available (DM), use `RoleContext.dm()`.
    """
    tier: PermissionTier = PermissionTier.GUEST
    role_names: list[str] = field(default_factory=list)         # Names of all roles, highest-position first
    role_ids: list[int] = field(default_factory=list)           # Corresponding role IDs
    is_server_owner: bool = False
    is_administrator: bool = False
    can_ban: bool = False
    can_kick: bool = False
    can_manage_channels: bool = False
    can_manage_roles: bool = False
    can_manage_messages: bool = False
    can_moderate_members: bool = False  # Timeout permission
    is_dm: bool = False

    # A short natural-language summary for the LLM
    @property
    def summary(self) -> str:
        if self.is_dm:
            return "User is in a DM (no guild context — no server management allowed)"
        parts = [f"Tier: {self.tier.value}", f"Roles: {', '.join(self.role_names[:5]) or 'None'}"]
        perms = []
        if self.is_server_owner:
            perms.append("Server Owner")
        if self.is_administrator:
            perms.append("Administrator")
        if self.can_ban:
            perms.append("Ban Members")
        if self.can_kick:
            perms.append("Kick Members")
        if self.can_manage_channels:
            perms.append("Manage Channels")
        if self.can_manage_roles:
            perms.append("Manage Roles")
        if self.can_manage_messages:
            perms.append("Manage Messages")
        if self.can_moderate_members:
            perms.append("Timeout Members")
        if perms:
            parts.append(f"Permissions: {', '.join(perms)}")
        return " | ".join(parts)

    def can_use_tool(self, tool_name: str) -> bool:
        """Return True if this user's tier is allowed to invoke a given tool."""
        allowed_tiers = TOOL_PERMISSIONS.get(tool_name)
        if allowed_tiers is None:
            # Unknown tool — deny by default for non-admins
            return self.tier in (PermissionTier.ADMIN, PermissionTier.OWNER)
        return self.tier in allowed_tiers

    def blocked_tools(self, requested_tools: list[str]) -> list[str]:
        """Return list of tools from the request that this user cannot use."""
        return [t for t in requested_tools if not self.can_use_tool(t)]

    @classmethod
    def dm(cls) -> RoleContext:
        """Build a RoleContext for a direct message (no guild)."""
        return cls(tier=PermissionTier.MEMBER, is_dm=True)

    @classmethod
    def from_member(cls, member) -> RoleContext:
        """
        Build a RoleContext from a live discord.Member object.

        Computes the permission tier from the member's actual guild permissions
        at call-time — not cached or passed in as an argument.
        """
        perms = member.guild_permissions
        guild  = member.guild

        is_owner   = guild.owner_id == member.id
        is_admin   = perms.administrator or is_owner

        can_ban    = perms.ban_members or is_admin
        can_kick   = perms.kick_members or is_admin
        can_chan   = perms.manage_channels or is_admin
        can_roles  = perms.manage_roles or is_admin
        can_msgs   = perms.manage_messages or is_admin
        can_mod    = perms.moderate_members or is_admin

        is_moderator = (can_ban or can_kick or can_mod or can_msgs) and not is_admin

        if is_owner:
            tier = PermissionTier.OWNER
        elif is_admin:
            tier = PermissionTier.ADMIN
        elif is_moderator:
            tier = PermissionTier.MODERATOR
        elif len(member.roles) > 1:  # @everyone is always in roles
            tier = PermissionTier.MEMBER
        else:
            tier = PermissionTier.GUEST

        # Roles sorted highest position first (excluding @everyone)
        sorted_roles = sorted(
            [r for r in member.roles if r.name != "@everyone"],
            key=lambda r: r.position,
            reverse=True,
        )

        return cls(
            tier=tier,
            role_names=[r.name for r in sorted_roles],
            role_ids=[r.id for r in sorted_roles],
            is_server_owner=is_owner,
            is_administrator=is_admin,
            can_ban=can_ban,
            can_kick=can_kick,
            can_manage_channels=can_chan,
            can_manage_roles=can_roles,
            can_manage_messages=can_msgs,
            can_moderate_members=can_mod,
        )


# ---------------------------------------------------------------------------
# RoleGate — blocks the pipeline before tool execution
# ---------------------------------------------------------------------------

class RoleGate:
    """
    Lightweight enforcement layer.

    Called by CognitivePipeline._execute() BEFORE any tool is run.
    Returns a (allowed, reason) tuple. If allowed is False, the tool
    call is blocked and the reason is returned to the user.
    """

    @staticmethod
    def check(tool_name: str, role_ctx: RoleContext) -> tuple[bool, str]:
        """
        Check if role_ctx permits calling tool_name.

        Returns:
            (True, "") if allowed
            (False, reason_message) if denied
        """
        if role_ctx.is_dm:
            return False, (
                f"⛔ **Server management unavailable in DMs.**\n"
                f"The tool `{tool_name}` requires a server context."
            )

        if role_ctx.can_use_tool(tool_name):
            return True, ""

        # Construct a helpful denial message based on what tier is required
        required_tiers = TOOL_PERMISSIONS.get(tool_name, [PermissionTier.ADMIN, PermissionTier.OWNER])
        required_names = " or ".join(t.value for t in required_tiers)
        return False, (
            f"⛔ **Permission denied for `{tool_name}`.**\n"
            f"Required: **{required_names}** — you are **{role_ctx.tier.value}**.\n"
            f"Your roles: {', '.join(role_ctx.role_names[:3]) or 'None'}"
        )
