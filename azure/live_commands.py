"""
Discord Commands for Live Intelligence System

Add these commands to your discord_bot_v1.py to control and monitor
the live intelligence system.

To integrate:
    from azure.live_commands import setup_live_commands
    setup_live_commands(bot, live_intelligence)
"""


import discord


def setup_live_commands(bot, live_intel):
    """
    Add live intelligence commands to the bot.

    Args:
        bot: Discord bot instance
        live_intel: LiveIntelligence instance
    """

    @bot.command(name="server_health")
    async def server_health_cmd(ctx):
        """Show real-time server health and insights."""
        if not ctx.guild:
            await ctx.send("This command only works in servers.")
            return

        insights = live_intel.get_server_insights(str(ctx.guild.id))

        embed = discord.Embed(
            title=f"🏥 {ctx.guild.name} - Server Health",
            color=discord.Color.green() if insights.health_score > 80 else (
                discord.Color.orange() if insights.health_score > 60 else discord.Color.red()
            )
        )

        # Health overview
        embed.add_field(
            name="Health Score",
            value=f"**{insights.health_score:.1f}/100**",
            inline=True
        )
        embed.add_field(
            name="Engagement Rate",
            value=f"{insights.engagement_rate:.1f}%",
            inline=True
        )
        embed.add_field(
            name="Messages (1h)",
            value=str(insights.messages_last_hour),
            inline=True
        )

        # Activity
        embed.add_field(
            name="📊 Activity",
            value=(
                f"**Now:** {insights.active_users_now} users\n"
                f"**Hour:** {insights.active_users_hour} users\n"
                f"**Day:** {insights.active_users_day} users\n"
                f"**Total:** {insights.total_users} members"
            ),
            inline=True
        )

        # Active channels
        if insights.most_active_channels:
            embed.add_field(
                name="🔥 Hot Channels",
                value="\n".join(f"#{ch}" for ch in insights.most_active_channels[:3]),
                inline=True
            )

        # Alerts
        if insights.alerts:
            embed.add_field(
                name="⚠️ Alerts",
                value="\n".join(insights.alerts[:3]),
                inline=False
            )

        # Suspicious activity
        if insights.suspicious_users:
            embed.add_field(
                name="🚨 Suspicious Activity",
                value=f"{len(insights.suspicious_users)} users flagged",
                inline=True
            )

        if insights.raid_probability > 0:
            embed.add_field(
                name="🛡️ Raid Risk",
                value=f"{insights.raid_probability:.0%}",
                inline=True
            )

        embed.set_footer(text="Updated in real-time • Use !suggestions for recommendations")
        await ctx.send(embed=embed)

    @bot.command(name="suggestions")
    async def suggestions_cmd(ctx):
        """Get proactive suggestions for improving the server."""
        if not ctx.guild:
            await ctx.send("This command only works in servers.")
            return

        if not isinstance(ctx.author, discord.Member) or not ctx.author.guild_permissions.manage_guild:
            await ctx.send("⚠️ This command requires 'Manage Server' permission.")
            return

        await ctx.send("🧠 Analyzing server and generating suggestions... (this may take a moment)")

        suggestions = await live_intel.generate_suggestions(ctx.guild, max_suggestions=5)

        if not suggestions:
            await ctx.send("✅ No issues detected! Your server is running smoothly.")
            return

        for i, suggestion in enumerate(suggestions[:3], 1):
            priority_emoji = {"urgent": "🚨", "high": "⚠️", "medium": "💡", "low": "ℹ️"}
            emoji = priority_emoji.get(suggestion.priority.value, "💡")

            embed = discord.Embed(
                title=f"{emoji} Suggestion #{i}: {suggestion.title}",
                description=suggestion.description,
                color=discord.Color.red() if suggestion.priority.value == "urgent" else (
                    discord.Color.orange() if suggestion.priority.value == "high" else
                    discord.Color.blue()
                )
            )

            embed.add_field(
                name="Why",
                value=suggestion.reasoning[:200],
                inline=False
            )

            if suggestion.recommended_actions:
                actions_text = "\n".join(f"• {action}" for action in suggestion.recommended_actions[:3])
                embed.add_field(
                    name="What You Can Do",
                    value=actions_text,
                    inline=False
                )

            embed.add_field(name="Priority", value=suggestion.priority.value.upper(), inline=True)
            embed.add_field(name="Confidence", value=f"{suggestion.confidence:.0%}", inline=True)

            if suggestion.expected_impact:
                embed.add_field(name="Expected Impact", value=suggestion.expected_impact, inline=False)

            embed.set_footer(text=f"Suggestion ID: {suggestion.suggestion_id[:8]}")
            await ctx.send(embed=embed)

    @bot.command(name="user_profile")
    async def user_profile_cmd(ctx, user: discord.Member = None):
        """Show activity profile for a user."""
        if not ctx.guild:
            await ctx.send("This command only works in servers.")
            return

        target = user or ctx.author
        guild_id = str(ctx.guild.id)
        user_id = str(target.id)

        activity = live_intel.get_user_activity(guild_id, user_id)

        if not activity:
            await ctx.send(f"No activity data for {target.display_name} yet.")
            return

        embed = discord.Embed(
            title=f"👤 Activity Profile: {target.display_name}",
            color=discord.Color.blue()
        )

        # Basic stats
        embed.add_field(
            name="Messages",
            value=f"**Total:** {activity.message_count}\n**Hour:** {activity.messages_last_hour}",
            inline=True
        )

        embed.add_field(
            name="Engagement",
            value=f"**Reactions Given:** {activity.reactions_given}\n**Received:** {activity.reactions_received}",
            inline=True
        )

        # Trust score
        trust_emoji = "🟢" if activity.trust_score > 70 else ("🟡" if activity.trust_score > 40 else "🔴")
        embed.add_field(
            name="Trust Score",
            value=f"{trust_emoji} **{activity.trust_score:.1f}/100**",
            inline=True
        )

        # Moderation history
        mod_history = live_intel.get_user_moderation_history(user_id)
        if mod_history:
            embed.add_field(
                name="⚠️ Moderation",
                value=f"**Warnings:** {activity.warnings}\n**Timeouts:** {activity.timeouts}",
                inline=True
            )

        # Behavioral flags
        if activity.suspicious_patterns:
            embed.add_field(
                name="🚩 Flags",
                value=", ".join(activity.suspicious_patterns[:3]),
                inline=True
            )

        # Activity hours
        if activity.active_hours:
            hours_text = f"{min(activity.active_hours)}-{max(activity.active_hours)}h (UTC)"
            embed.add_field(
                name="Active Hours",
                value=hours_text,
                inline=True
            )

        joined = target.joined_at
        embed.set_footer(text=f"Member since {discord.utils.format_dt(joined, 'R') if joined else 'unknown'}")
        await ctx.send(embed=embed)

    @bot.command(name="live_mod_stats")
    async def live_mod_stats_cmd(ctx):
        """Show live-intelligence moderation statistics."""
        if not isinstance(ctx.author, discord.Member) or not ctx.author.guild_permissions.moderate_members:
            await ctx.send("⚠️ Moderators only.")
            return

        stats = live_intel.get_moderation_stats()
        detection = stats["detection"]
        execution = stats["execution"]

        embed = discord.Embed(
            title="🛡️ Moderation Statistics",
            color=discord.Color.blue()
        )

        # Detection stats
        embed.add_field(
            name="Detection",
            value=(
                f"**Analyzed:** {detection.total_analyzed:,}\n"
                f"**Threats:** {detection.threats_detected:,}\n"
                f"**Avg Confidence:** {detection.avg_confidence:.0%}"
            ),
            inline=True
        )

        # Execution stats
        embed.add_field(
            name="Actions",
            value=(
                f"**Total:** {execution['total_actions']}\n"
                f"**Executed:** {execution['executed']}\n"
                f"**Pending:** {execution['pending_approval']}"
            ),
            inline=True
        )

        # By threat level
        if detection.by_threat_level:
            threats_text = "\n".join(
                f"**{level.upper()}:** {count}"
                for level, count in detection.by_threat_level.items()
            )
            embed.add_field(name="By Threat Level", value=threats_text, inline=True)

        # By action type
        if execution.get('by_action_type'):
            actions_text = "\n".join(
                f"**{action}:** {count}"
                for action, count in execution['by_action_type'].items()
            )
            embed.add_field(name="By Action Type", value=actions_text, inline=True)

        await ctx.send(embed=embed)

    @bot.command(name="configure_automod")
    async def configure_automod_cmd(ctx, setting: str = "", value: str = ""):
        """Configure auto-moderation settings (admin only)."""
        if not isinstance(ctx.author, discord.Member) or not ctx.author.guild_permissions.administrator:
            await ctx.send("⚠️ Administrators only.")
            return

        if not setting:
            # Show current config
            config = live_intel.auto_mod.config
            embed = discord.Embed(
                title="⚙️ Auto-Moderation Configuration",
                color=discord.Color.blue()
            )

            embed.add_field(name="Enabled", value="✅" if config.enabled else "❌", inline=True)
            embed.add_field(name="Dry Run", value="✅" if config.dry_run else "❌", inline=True)
            embed.add_field(name="Delete Threshold", value=f"{config.auto_delete_threshold:.0%}", inline=True)
            embed.add_field(name="Warn Threshold", value=f"{config.auto_warn_threshold:.0%}", inline=True)
            embed.add_field(name="Timeout Threshold", value=f"{config.auto_timeout_threshold:.0%}", inline=True)
            embed.add_field(name="Never Auto-Kick", value="✅" if config.never_auto_kick else "❌", inline=True)
            embed.add_field(name="Never Auto-Ban", value="✅" if config.never_auto_ban else "❌", inline=True)

            embed.set_footer(text="Use: !configure_automod <setting> <value> to change")
            await ctx.send(embed=embed)
            return

        # Parse value
        if value.lower() in ("true", "yes", "on", "1"):
            parsed_value = True
        elif value.lower() in ("false", "no", "off", "0"):
            parsed_value = False
        else:
            try:
                parsed_value = float(value)
            except (ValueError, TypeError):
                parsed_value = value

        # Apply setting
        try:
            live_intel.configure_auto_mod(**{setting: parsed_value})
            await ctx.send(f"✅ Updated `{setting}` to `{parsed_value}`")
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")

    @bot.command(name="set_admin_channel")
    async def set_admin_channel_cmd(ctx, channel: discord.TextChannel = None):
        """Set the admin notification channel (admin only)."""
        if not isinstance(ctx.author, discord.Member) or not ctx.author.guild_permissions.administrator:
            await ctx.send("⚠️ Administrators only.")
            return

        target_channel = channel or ctx.channel
        live_intel.set_admin_channel(str(target_channel.id))
        await ctx.send(f"✅ Admin notifications will be sent to {target_channel.mention}")

    @bot.command(name="export_analytics")
    async def export_analytics_cmd(ctx):
        """Export server analytics to JSON (admin only)."""
        if not ctx.guild:
            await ctx.send("This command only works in servers.")
            return

        if not isinstance(ctx.author, discord.Member) or not ctx.author.guild_permissions.administrator:
            await ctx.send("⚠️ Administrators only.")
            return

        await ctx.send("📊 Exporting analytics...")

        import tempfile
        from pathlib import Path

        temp_file = Path(tempfile.gettempdir()) / f"analytics_{ctx.guild.id}.json"
        await live_intel.export_analytics(str(ctx.guild.id), str(temp_file))

        try:
            await ctx.send(
                "✅ Analytics exported!",
                file=discord.File(str(temp_file), filename=f"{ctx.guild.name}_analytics.json")
            )
        except Exception:
            await ctx.send(f"✅ Analytics saved to: `{temp_file}`")

    @bot.command(name="system_status")
    async def system_status_cmd(ctx):
        """Show live intelligence system status (admin only)."""
        if not isinstance(ctx.author, discord.Member) or not ctx.author.guild_permissions.administrator:
            await ctx.send("⚠️ Administrators only.")
            return

        status = live_intel.get_system_status()

        embed = discord.Embed(
            title="🤖 Live Intelligence System Status",
            description="Status of all monitoring subsystems",
            color=discord.Color.green()
        )

        # Awareness
        embed.add_field(
            name="👁️ Awareness Engine",
            value=(
                f"**Status:** ✅ Online\n"
                f"**Servers:** {status['awareness']['guilds_tracked']}\n"
                f"**Events:** {status['awareness']['total_events']:,}"
            ),
            inline=True
        )

        # Moderation
        mod = status['moderation']
        embed.add_field(
            name="🛡️ Moderation Intel",
            value=(
                f"**Status:** ✅ Online\n"
                f"**LLM:** {'✅' if mod['llm_available'] else '❌'}\n"
                f"**Strict:** {'✅' if mod['strict_mode'] else '❌'}"
            ),
            inline=True
        )

        # Auto-mod
        auto = status['auto_mod']
        embed.add_field(
            name="⚙️ Auto-Moderation",
            value=(
                f"**Status:** {'✅ Enabled' if auto['enabled'] else '⏸️ Disabled'}\n"
                f"**Mode:** {'🔍 Dry Run' if auto['dry_run'] else '▶️ Active'}\n"
                f"**Actions:** {auto['stats']['total_actions']}"
            ),
            inline=True
        )

        # Insights
        ins = status['insights']
        embed.add_field(
            name="💡 Proactive Insights",
            value=(
                f"**Status:** ✅ Online\n"
                f"**LLM:** {'✅' if ins['llm_available'] else '❌'}\n"
                f"**Suggestions:** {ins['stats']['total']}"
            ),
            inline=True
        )

        embed.set_footer(text="Azure Live Intelligence v2.0")
        await ctx.send(embed=embed)
