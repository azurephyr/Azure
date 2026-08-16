"""
Azure Audit System

Handles logging critical operations to the database and
dispatching real-time notifications to the Discord Owner/Admin channel.
"""
import logging
import time

from .database import AuditLogEntry, DatabaseManager

logger = logging.getLogger("azure.audit")

class AuditSystem:
    def __init__(self, db: DatabaseManager, bot=None, admin_channel_id: int | None = None):
        self.db = db
        self.bot = bot
        self.admin_channel_id = admin_channel_id

    async def log_action(self, action: str, user_name: str, discord_id: str,
                         subsystem: str, reason: str = "",
                         old_value: str = "", new_value: str = "",
                         ip_address: str = "", session_id: str = "",
                         is_critical: bool = False) -> None:
        """Log an action and notify admins/owner."""
        entry = AuditLogEntry(
            timestamp=time.time(),
            user_name=user_name,
            discord_id=discord_id,
            action=action,
            subsystem=subsystem,
            reason=reason,
            old_value=str(old_value) if old_value else "",
            new_value=str(new_value) if new_value else "",
            ip_address=ip_address,
            session_id=session_id
        )

        # 1. Save to Database (KL-4 fix: serialize concurrent writers)
        with self.db._wlock:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO audit_logs (
                    timestamp, user_name, discord_id, ip_address, session_id,
                    action, old_value, new_value, reason, subsystem
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.timestamp, entry.user_name, entry.discord_id, entry.ip_address,
                entry.session_id, entry.action, entry.old_value, entry.new_value,
                entry.reason, entry.subsystem
            ))
            conn.commit()
        logger.info(f"[audit] {user_name} ({discord_id}) -> {action} [{subsystem}]")

        # 2. Notify Discord Live
        if self.bot and self.admin_channel_id:
            try:
                import discord
                channel = self.bot.get_channel(int(self.admin_channel_id))
                if channel:
                    embed = discord.Embed(
                        title=f"🛡️ Audit: {action}",
                        color=discord.Color.brand_red() if "delete" in action.lower() or "ban" in action.lower() else discord.Color.blurple(),
                        timestamp=discord.utils.utcnow()
                    )
                    embed.add_field(name="User", value=f"{user_name} (`{discord_id}`)", inline=True)
                    embed.add_field(name="Subsystem", value=subsystem, inline=True)
                    if reason:
                        embed.add_field(name="Reason", value=reason, inline=False)
                    if old_value or new_value:
                        embed.add_field(name="Change", value=f"`{old_value}` ➔ `{new_value}`", inline=False)

                    await channel.send(embed=embed)
            except Exception as e:
                logger.error(f"[audit] Failed to notify Discord: {e}")

        # 3. Critical-security DMs always fire when bot is reachable,
        # independently of the admin-channel configuration. Critical
        # ownership alerts must never be silently dropped just because
        # no admin channel is configured.
        if is_critical and self.bot:
            try:
                app_info = await self.bot.application_info()
                owner = app_info.owner
                if owner:
                    await owner.send(
                        f"⚠️ **CRITICAL AZURE ALERT** ⚠️\n{action}\n"
                        f"Subsystem: {subsystem}\nReason: {reason}"
                    )
            except Exception as dm_err:
                logger.error(f"[audit] Failed to DM owner: {dm_err}")

    def get_logs(self, limit: int = 100) -> list[dict]:
        """Fetch recent audit logs."""
        with self.db._wlock:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]
