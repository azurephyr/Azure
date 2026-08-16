"""
Azure Server Templates

Save, load, and apply server configurations as reusable templates.
A template captures: roles, categories, channels, and permission overwrites.

Usage:
    from azure.server_templates import ServerTemplateManager
    tm = ServerTemplateManager(template_dir=Path("templates"))

    # Save current server
    await tm.save_template(guild, "gaming", "My gaming server setup")

    # Apply to another server
    await tm.apply_template(guild, "gaming", ctx)

    # List available templates
    templates = tm.list_templates()
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("azure.server_templates")


@dataclass
class ServerTemplate:
    """A captured server configuration."""
    name: str
    description: str
    created_at: float
    roles: list[dict]       # name, color, permissions, hoist, mentionable
    categories: list[dict]   # name, position
    channels: list[dict]     # name, type, category, topic, slowmode, nsfw
    permission_overwrites: list[dict]  # channel, role, allow, deny


class ServerTemplateManager:
    """Manage server templates for save/load/apply."""

    @staticmethod
    def _parse_color(color_val) -> str | None:
        """Parse a color value from various formats (hex, int, discord Color)."""
        if color_val is None:
            return None
        s = str(color_val).strip()
        # Already a clean hex like "e74c3c" or "E74C3C"
        if re.match(r'^[0-9a-fA-F]{6}$', s):
            return s.lower()
        # Has "0x" prefix like "0xE74C3C"
        if s.startswith("0x") or s.startswith("0X"):
            try:
                return f"{int(s, 16):06x}"
            except ValueError:
                return None
        # Has "#" prefix like "#E74C3C"
        if s.startswith("#"):
            s = s[1:]
            if re.match(r'^[0-9a-fA-F]{6}$', s):
                return s.lower()
        # Pure decimal integer
        try:
            return f"{int(s):06x}"
        except ValueError:
            return None

    def __init__(self, template_dir: Path | None = None):
        self.template_dir = Path(template_dir) if template_dir else Path("templates")
        self.template_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Save Template
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_template_name(name: str) -> str:
        """
        Strict validation of a template name before use as part of a filename.

        Reasons:
        - Block path traversal (`..`, `/`, `\\`) that would let users write
          or read JSON files outside `template_dir`.
        - Block null bytes and control characters.
        - Limit length to avoid filesystem-name limits.
        """
        if not isinstance(name, str):
            raise ValueError("Template name must be a string.")
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("Template name cannot be empty.")
        if len(cleaned) > 64:
            raise ValueError("Template name must be <= 64 characters.")
        # Disallow traversal / separators / null bytes / control chars.
        for forbidden in ("..", "/", "\\", "\x00", "\n", "\r", "\t"):
            if forbidden in cleaned:
                raise ValueError(f"Template name contains forbidden sequence: {forbidden!r}")
        # ASCII-printable whitelist (letters, digits, space, dash, underscore).
        if not all(c.isalnum() or c in (" ", "-", "_") for c in cleaned):
            raise ValueError("Template name may only contain letters, digits, spaces, dashes, and underscores.")
        return cleaned

    def _safe_template_path(self, name: str) -> Path:
        """Resolve the actual filesystem path for a template with defense-in-depth."""
        cleaned = self._validate_template_name(name)
        path = (self.template_dir / f"{cleaned}.json").resolve()
        # Defense-in-depth: ensure the resolved path is still under template_dir.
        if not str(path).startswith(str(self.template_dir.resolve())):
            raise ValueError("Template name resolves outside the templates directory.")
        return path

    async def save_template(self, guild, name: str, description: str = "") -> str:
        """
        Capture current server state as a template.

        Returns:
            Path to saved template file.
        """
        name = self._validate_template_name(name)
        roles = []
        for r in guild.roles:
            if r.is_default() or r.managed:
                continue
            roles.append({
                "name": r.name,
                "color": str(r.color),
                "permissions": [p[0] for p in r.permissions if p[1]],
                "hoist": r.hoist,
                "mentionable": r.mentionable,
                "position": r.position,
            })

        categories = []
        for cat in guild.categories:
            categories.append({
                "name": cat.name,
                "position": cat.position,
            })

        channels = []
        for ch in guild.channels:
            if ch.type.value == 4:  # category
                continue
            ch_data = {
                "name": ch.name,
                "type": str(ch.type),
                "position": ch.position,
            }
            if hasattr(ch, "category") and ch.category:
                ch_data["category"] = ch.category.name
            if hasattr(ch, "topic"):
                ch_data["topic"] = ch.topic or ""
            if hasattr(ch, "slowmode_delay"):
                ch_data["slowmode"] = ch.slowmode_delay
            if hasattr(ch, "nsfw"):
                ch_data["nsfw"] = ch.nsfw
            if hasattr(ch, "bitrate"):
                ch_data["bitrate"] = ch.bitrate
            if hasattr(ch, "user_limit"):
                ch_data["user_limit"] = ch.user_limit
            channels.append(ch_data)

        # Capture permission overwrites
        overwrites = []
        for ch in guild.channels:
            if ch.type.value == 4:
                continue
            for target, overwrite in ch.overwrites.items():
                if hasattr(target, "name"):
                    allow = [p[0] for p in overwrite if p[1] is True]
                    deny = [p[0] for p in overwrite if p[1] is False]
                    overwrites.append({
                        "channel": ch.name,
                        "target": target.name,
                        "target_type": "role" if hasattr(target, "hoist") else "member",
                        "allow": allow,
                        "deny": deny,
                    })

        template = ServerTemplate(
            name=name,
            description=description,
            created_at=time.time(),
            roles=roles,
            categories=categories,
            channels=channels,
            permission_overwrites=overwrites,
        )

        path = self._safe_template_path(name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(template), f, ensure_ascii=False, indent=2)

        return str(path)

    # ------------------------------------------------------------------
    # Load / Apply
    # ------------------------------------------------------------------

    def load_template(self, name: str) -> ServerTemplate | None:
        """Load a template by name."""
        try:
            path = self._safe_template_path(name)
        except ValueError as e:
            logger.error(f"[server_templates] refused template name: {e}")
            return None
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return ServerTemplate(**data)
        except Exception as e:
            logger.error(f"[server_templates] load failed: {e}")

            return None

    def list_templates(self) -> list[dict]:
        """List all available templates."""
        templates = []
        for f in sorted(self.template_dir.glob("*.json")):
            try:
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                templates.append({
                    "name": data.get("name", f.stem),
                    "description": data.get("description", ""),
                    "created_at": data.get("created_at", 0),
                    "roles_count": len(data.get("roles", [])),
                    "channels_count": len(data.get("channels", [])),
                    "file": str(f),
                })
            except Exception as e:
                logger.info(f"[server_templates] corrupted template {f.name}: {e}")

        return templates

    def delete_template(self, name: str) -> bool:
        """Delete a template."""
        try:
            path = self._safe_template_path(name)
        except ValueError as e:
            logger.error(f"[server_templates] refused template name: {e}")
            return False
        if path.exists():
            path.unlink()
            return True
        return False

    def to_plan(self, name: str) -> dict:
        """
        Convert a template to a management plan (for the Discord tools executor).
        Returns a plan dict with steps.
        """
        template = self.load_template(name)
        if not template:
            return {"analysis": f"Template '{name}' not found.", "steps": []}

        steps = []

        # 1. Create roles (highest position first to maintain order)
        for r in sorted(template.roles, key=lambda x: x.get("position", 0), reverse=True):
            steps.append({
                "action": "create_role",
                "name": r["name"],
                "color": self._parse_color(r.get("color")) if r.get("color") else None,
                "permissions": r.get("permissions", []),
                "hoist": r.get("hoist", False),
                "mentionable": r.get("mentionable", False),
            })

        # 2. Create categories
        for cat in sorted(template.categories, key=lambda x: x.get("position", 0)):
            steps.append({
                "action": "create_category",
                "name": cat["name"],
            })

        # 3. Create channels
        for ch in sorted(template.channels, key=lambda x: x.get("position", 0)):
            steps.append({
                "action": "create_channel",
                "name": ch["name"],
                "type": ch.get("type", "text"),
                "category": ch.get("category"),
                "topic": ch.get("topic"),
                "slowmode": ch.get("slowmode"),
                "nsfw": ch.get("nsfw", False),
                "bitrate": ch.get("bitrate"),
                "user_limit": ch.get("user_limit"),
            })

        # 4. Set permission overwrites
        for ow in template.permission_overwrites:
            steps.append({
                "action": "set_permissions",
                "channel": ow["channel"],
                "role": ow["target"],
                "allow": ow.get("allow", []),
                "deny": ow.get("deny", []),
            })

        return {
            "analysis": f"Applying template '{name}': {template.description}",
            "steps": steps,
            "template_name": name,
        }

    # ------------------------------------------------------------------
    # Built-in default templates
    # ------------------------------------------------------------------

    def create_default_templates(self):
        """Create some built-in templates if they don't exist."""
        # Gaming template
        if not self.load_template("gaming"):
            gaming = ServerTemplate(
                name="gaming",
                description="Perfect for gaming communities with voice channels and roles.",
                created_at=time.time(),
                roles=[
                    {"name": "Admin", "color": "FF0000", "permissions": ["manage_guild", "manage_channels", "manage_roles", "ban_members", "kick_members"], "hoist": True, "mentionable": True, "position": 5},
                    {"name": "Moderator", "color": "3498DB", "permissions": ["manage_messages", "kick_members", "ban_members"], "hoist": True, "mentionable": True, "position": 4},
                    {"name": "Streamer", "color": "9B59B6", "permissions": ["send_messages", "connect", "speak"], "hoist": True, "mentionable": False, "position": 3},
                    {"name": "Member", "color": "2ECC71", "permissions": ["send_messages", "read_messages", "connect", "speak"], "hoist": False, "mentionable": False, "position": 1},
                ],
                categories=[
                    {"name": "📋 Info", "position": 0},
                    {"name": "💬 General", "position": 1},
                    {"name": "🎮 Gaming", "position": 2},
                    {"name": "🔊 Voice", "position": 3},
                ],
                channels=[
                    {"name": "rules", "type": "text", "category": "📋 Info", "topic": "Server rules"},
                    {"name": "announcements", "type": "text", "category": "📋 Info", "topic": "Important announcements"},
                    {"name": "general-chat", "type": "text", "category": "💬 General"},
                    {"name": "memes", "type": "text", "category": "💬 General"},
                    {"name": "looking-for-group", "type": "text", "category": "🎮 Gaming", "topic": "Find people to play with"},
                    {"name": "clips-and-highlights", "type": "text", "category": "🎮 Gaming"},
                    {"name": "General Voice", "type": "voice", "category": "🔊 Voice"},
                    {"name": "Squad 1", "type": "voice", "category": "🔊 Voice", "user_limit": 5},
                    {"name": "Squad 2", "type": "voice", "category": "🔊 Voice", "user_limit": 5},
                    {"name": "Streaming Room", "type": "stage_voice", "category": "🔊 Voice"},
                ],
                permission_overwrites=[
                    {"channel": "announcements", "target": "Member", "target_type": "role", "allow": ["read_messages"], "deny": ["send_messages"]},
                ],
            )
            path = self.template_dir / "gaming.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(asdict(gaming), f, ensure_ascii=False, indent=2)

        # Community template
        if not self.load_template("community"):
            community = ServerTemplate(
                name="community",
                description="General purpose community server with moderation and discussion areas.",
                created_at=time.time(),
                roles=[
                    {"name": "Admin", "color": "E74C3C", "permissions": ["manage_guild", "manage_channels", "manage_roles", "ban_members", "kick_members"], "hoist": True, "mentionable": True, "position": 5},
                    {"name": "Moderator", "color": "3498DB", "permissions": ["manage_messages", "kick_members", "read_message_history"], "hoist": True, "mentionable": True, "position": 4},
                    {"name": "VIP", "color": "F1C40F", "permissions": ["send_messages", "embed_links", "attach_files"], "hoist": True, "mentionable": False, "position": 3},
                    {"name": "Member", "color": "2ECC71", "permissions": ["send_messages", "read_messages", "add_reactions"], "hoist": False, "mentionable": False, "position": 1},
                ],
                categories=[
                    {"name": "📢 Information", "position": 0},
                    {"name": "💬 Discussion", "position": 1},
                    {"name": "🎨 Creative", "position": 2},
                    {"name": "🔊 Voice Lounge", "position": 3},
                ],
                channels=[
                    {"name": "welcome", "type": "text", "category": "📢 Information", "topic": "New member welcome area"},
                    {"name": "rules", "type": "text", "category": "📢 Information", "topic": "Server rules and guidelines"},
                    {"name": "announcements", "type": "text", "category": "📢 Information", "topic": "Server announcements"},
                    {"name": "general", "type": "text", "category": "💬 Discussion"},
                    {"name": "off-topic", "type": "text", "category": "💬 Discussion"},
                    {"name": "feedback", "type": "text", "category": "💬 Discussion", "topic": "Share your feedback and suggestions"},
                    {"name": "art-showcase", "type": "text", "category": "🎨 Creative"},
                    {"name": "music-share", "type": "text", "category": "🎨 Creative"},
                    {"name": "General Voice", "type": "voice", "category": "🔊 Voice Lounge"},
                    {"name": "Music Room", "type": "voice", "category": "🔊 Voice Lounge"},
                    {"name": "AFK", "type": "voice", "category": "🔊 Voice Lounge"},
                ],
                permission_overwrites=[
                    {"channel": "announcements", "target": "Member", "target_type": "role", "allow": ["read_messages"], "deny": ["send_messages"]},
                    {"channel": "welcome", "target": "Member", "target_type": "role", "allow": ["read_messages"], "deny": ["send_messages"]},
                ],
            )
            path = self.template_dir / "community.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(asdict(community), f, ensure_ascii=False, indent=2)

        # Minimal template
        if not self.load_template("minimal"):
            minimal = ServerTemplate(
                name="minimal",
                description="Bare minimum setup for a small server.",
                created_at=time.time(),
                roles=[
                    {"name": "Admin", "color": "E74C3C", "permissions": ["manage_guild", "manage_channels", "manage_roles", "ban_members", "kick_members"], "hoist": True, "mentionable": True, "position": 2},
                ],
                categories=[
                    {"name": "General", "position": 0},
                ],
                channels=[
                    {"name": "general", "type": "text", "category": "General"},
                    {"name": "General Voice", "type": "voice", "category": "General"},
                ],
                permission_overwrites=[],
            )
            path = self.template_dir / "minimal.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(asdict(minimal), f, ensure_ascii=False, indent=2)
