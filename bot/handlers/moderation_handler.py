"""Moderation handler - moderation commands and task loops."""

import logging

import discord

logger = logging.getLogger("azure.discord.moderation")


def _is_server_admin(ctx) -> bool:
    """Check if the user is server owner or administrator. DMs -> False."""
    if not ctx.guild:
        return False
    if ctx.guild.owner_id == ctx.author.id:
        return True
    member = ctx.guild.get_member(ctx.author.id)
    return bool(member and member.guild_permissions.administrator)


def register_moderation_commands(bot):
    """Register all moderation-related bot commands."""
    from bot.context import ctx as bot_ctx

    from ..config import CHUNK_SIZE, DEFAULT_LOOKBACK_HOURS, MOD_LOOKBACK_HOURS
    from .llm_handler import _llm_response

    @bot.command(name="mod_phase")
    async def mod_phase_cmd(ctx, phase: str = None):
        """Switch moderation phase."""
        if not bot_ctx.agent or not bot_ctx.agent.moderation:
            await ctx.send("moderation engine not available.")
            return
        # Read-only: show current phase. Mutation requires admin.
        if phase is None:
            current = bot_ctx.agent.moderation.policy.phase
            desc = bot_ctx.agent.moderation.policy.get_phase_description()
            await ctx.send(f"current phase: **{current.value}**\n{desc}")
            return
        if not _is_server_admin(ctx):
            await ctx.send("⚠️ Only server admins/owners can change moderation phase.")
            return
        try:
            bot_ctx.agent.moderation.set_phase(phase)
            current = bot_ctx.agent.moderation.policy.phase
            desc = bot_ctx.agent.moderation.policy.get_phase_description()
            await ctx.send(f"phase set to **{current.value}**\n{desc}")
        except ValueError as e:
            await ctx.send(f"cannot escalate: {e}")
        except Exception as e:
            await ctx.send(f"error: {e}")

    @bot.command(name="mod_readiness")
    async def mod_readiness_cmd(ctx, hours: int = None):
        """Check if Azure is ready for phase escalation."""
        if not bot_ctx.agent or not bot_ctx.agent.moderation:
            await ctx.send("moderation engine not available.")
            return
        hours = hours or DEFAULT_LOOKBACK_HOURS
        report = bot_ctx.agent.moderation.get_readiness_report(hours=hours)
        lines = [
            f"**Azure Readiness Report (last {hours}h)**",
            f"total events: {report['total_events']}",
            f"feedback given: {report['feedback_given']}",
            f"precision: {report['precision']}",
            f"recall: {report['recall']}",
            "",
            "**checks:**",
        ]
        for check, passed in report["checks"].items():
            status = "✅" if passed else "❌"
            lines.append(f"{status} {check}")
        lines.append("")
        lines.append(f"**recommendation:** {report['recommendation']}")
        await ctx.send("\n".join(lines))

    @bot.command(name="mod_stats")
    async def mod_stats_cmd(ctx):
        """Show moderation statistics."""
        if not bot_ctx.agent or not bot_ctx.agent.moderation:
            await ctx.send("moderation engine not available.")
            return
        stats = bot_ctx.agent.get_moderation_stats()
        lines = ["**Azure Moderation Stats**"]
        lines.append(f"phase: {stats.get('phase', 'unknown')}")
        lines.append(f"mode: {stats.get('mode', 'unknown')}")
        lines.append(f"dry_run: {stats.get('dry_run', False)}")
        actions = stats.get("actions_taken", {})
        if actions:
            lines.append("actions taken:")
            for action, count in actions.items():
                lines.append(f"  - {action}: {count}")
        else:
            lines.append("actions taken: none yet")
        await ctx.send("\n".join(lines))

    @bot.command(name="mod_scan")
    async def mod_scan_cmd(ctx):
        """Manually trigger a full channel scan."""
        if not bot_ctx.agent or not bot_ctx.agent.moderation:
            await ctx.send("moderation engine not available.")
            return
        if not ctx.guild:
            await ctx.send("must be used in a server.")
            return
        await ctx.send("scanning channels...")
        try:
            summary = await bot_ctx.agent.moderation.scan_and_report(ctx.guild, ctx.channel)
            await ctx.send(f"scan complete: {summary}")
        except Exception as e:
            await ctx.send(f"scan failed: {e}")

    @bot.command(name="mod_report")
    async def mod_report_cmd(ctx, hours: int = None):
        """Show moderation report for the last N hours."""
        if not bot_ctx.agent or not bot_ctx.agent.moderation:
            await ctx.send("moderation engine not available.")
            return
        hours = hours or MOD_LOOKBACK_HOURS
        summary = bot_ctx.agent.moderation.reporter.get_summary(hours=hours)
        lines = [f"**Moderation Report (last {hours}h)**"]
        lines.append(f"total actions: {summary.get('total', 0)}")
        for key in ("by_category", "by_severity", "by_action"):
            data = summary.get(key, {})
            if data:
                lines.append(f"{key}:")
                for k, v in data.items():
                    lines.append(f"  - {k}: {v}")
        await ctx.send("\n".join(lines))

    @bot.command(name="mod_channel")
    async def mod_channel_cmd(ctx, channel: discord.TextChannel = None):
        """Set the admin report channel."""
        if not _is_server_admin(ctx):
            await ctx.send("⚠️ Only server admins/owners can change the admin report channel.")
            return
        # ADMIN_CHANNEL is module-global in discord_bot_v1.py. Read & write
        # via the module attribute so other subsystems (periodic_scan,
        # autonomous_scan_task, moderation engine reporter) actually see
        # the new value. Without this, the assignment was a local-to-this-
        # function rebind and was silently lost.
        import discord_bot_v1
        if channel is None:
            if discord_bot_v1.ADMIN_CHANNEL:
                await ctx.send(f"admin channel: {discord_bot_v1.ADMIN_CHANNEL.mention}")
            else:
                await ctx.send("no admin channel set. Use !mod_channel #channel")
            return
        discord_bot_v1.ADMIN_CHANNEL = channel
        bot_ctx.admin_channel = channel
        await ctx.send(f"admin report channel set to {channel.mention}")

    @bot.command(name="mod_test")
    async def mod_test_cmd(ctx, *, text: str):
        """Test moderation classification on a message."""
        if not bot_ctx.agent or not bot_ctx.agent.moderation:
            await ctx.send("moderation engine not available.")
            return
        from azure.moderation.classifier import MessageClassifier
        classifier = MessageClassifier()
        result = classifier.classify(text)
        lines = ["**Classification Test**"]
        lines.append(f"category: **{result.category}**")
        lines.append(f"severity: **{result.severity.name}**")
        lines.append(f"confidence: {result.confidence:.0%}")
        lines.append(f"reason: {result.reason}")
        await ctx.send("\n".join(lines))

    @bot.command(name="mod_feedback")
    async def mod_feedback_cmd(ctx, message_id: str, verdict: str):
        """Provide feedback on a moderation classification."""
        if not bot_ctx.agent or not bot_ctx.agent.moderation:
            await ctx.send("moderation engine not available.")
            return
        verdict = verdict.lower().strip()
        if verdict not in ("correct", "false_positive", "missed"):
            await ctx.send("verdict must be: correct, false_positive, or missed")
            return
        try:
            bot_ctx.agent.moderation.add_feedback(message_id, verdict, str(ctx.author))
            await ctx.send(f"feedback recorded: {verdict} for message {message_id}")
        except Exception as e:
            await ctx.send(f"feedback failed: {e}")

    @bot.command(name="azure_emergency_stop")
    async def azure_emergency_stop_cmd(ctx):
        """EMERGENCY KILL SWITCH."""
        if not _is_server_admin(ctx):
            await ctx.send("⚠️ Only server admins/owners can trigger an emergency stop.")
            return
        if not bot_ctx.agent or not bot_ctx.agent.moderation:
            await ctx.send("moderation engine not available.")
            return
        old_phase = bot_ctx.agent.moderation.policy.phase.value
        bot_ctx.agent.emergency_stop()
        await ctx.send(
            f"EMERGENCY STOP ACTIVATED\n"
            f"Phase forced from **{old_phase}** to **dry_run**\n"
            f"All moderation actions are now DISABLED."
        )

    @bot.command(name="azure_confirm")
    async def azure_confirm_cmd(ctx, message_id: str = None):
        """Confirm a pending moderation action."""
        if not _is_server_admin(ctx):
            await ctx.send("⚠️ Only server admins/owners can confirm pending actions.")
            return
        if not bot_ctx.agent or not bot_ctx.agent.moderation:
            await ctx.send("moderation engine not available.")
            return
        if message_id is None:
            pending = bot_ctx.agent.moderation.list_pending_confirmations()
            if not pending:
                await ctx.send("no pending confirmations.")
                return
            pending_text = "\n".join(
                f"- `{p['message_id']}` | {p['action_type']} on {p['user_name']}"
                for p in pending[:5]
            )
            await ctx.send(f"**Pending Confirmations:**\n{pending_text}")
            return
        success, result_msg = await bot_ctx.agent.moderation.confirm_action(message_id)
        resp = await _llm_response(
            f"Confirmation result for {message_id}: success={success}, msg='{result_msg}'",
            f"{'Confirmed.' if success else 'Failed:'} {result_msg}"
        )
        await ctx.send(resp[:CHUNK_SIZE])

    @bot.command(name="azure_cancel")
    async def azure_cancel_cmd(ctx, message_id: str):
        """Cancel a pending moderation action."""
        if not _is_server_admin(ctx):
            await ctx.send("⚠️ Only server admins/owners can cancel pending actions.")
            return
        if not bot_ctx.agent or not bot_ctx.agent.moderation:
            await ctx.send("moderation engine not available.")
            return
        result = bot_ctx.agent.moderation.cancel_action(message_id)
        if result:
            await ctx.send(f"Action `{message_id}` cancelled.")
        else:
            await ctx.send(f"Action `{message_id}` not found.")

    @bot.command(name="azure_scan")
    async def azure_scan_cmd(ctx):
        """Manually trigger an autonomous cross-channel scan."""
        from bot.context import ctx as bot_ctx
        if not bot_ctx.agent or not bot_ctx.agent.moderation:
            await ctx.send("moderation engine not available.")
            return
        if not ctx.guild:
            await ctx.send("must be used in a server.")
            return
        async def _run():
            report = await bot_ctx.agent.moderation.autonomous_scan(ctx.guild)
            if report.get("threat_detected"):
                lines = [
                    "Situation Detected",
                    f"Raid probability: {report.get('raid_probability', 0):.0%}",
                    f"Messages flagged: {report.get('messages_flagged', 0)}",
                    f"Action: {report.get('action', 'none')}",
                ]
                return "\n".join(lines)
            else:
                return f"No significant threats detected. Raid probability: {report.get('raid_probability', 0):.0%}"
        if bot_ctx.bg_executor:
            bot_ctx.bg_executor.dispatch(ctx.author.id, ctx.channel, _run(), "Autonomous Scan")
        else:
            await ctx.send("Background executor is not initialized.")

    @bot.command(name="azure_situation")
    async def azure_situation_cmd(ctx):
        """Show current temporal situation analysis for this server."""
        if not bot_ctx.agent or not bot_ctx.agent.moderation:
            await ctx.send("Moderation engine not loaded.")
            return
        if not ctx.guild:
            await ctx.send("Situation analysis only works in servers.")
            return
        guild_id = str(ctx.guild.id)
        temporal = bot_ctx.agent.moderation.temporal_analyzer.analyze_situation(guild_id, window_seconds=300)
        embed = discord.Embed(
            title="Server Situation Analysis",
            description=f"**Explanation:** {temporal.explanation}",
            color=0xf1c40f
        )
        embed.add_field(name="Raid Probability", value=f"{temporal.raid_probability:.0%}", inline=True)
        embed.add_field(name="Burst Score", value=f"{temporal.burst_score:.0f}", inline=True)
        embed.add_field(name="Coordination", value=f"{temporal.coordination_score:.0f}", inline=True)
        embed.add_field(name="Active Users", value=str(len(temporal.involved_users)), inline=True)
        await ctx.send(embed=embed)

    @bot.command(name="azure_behavior")
    async def azure_behavior_cmd(ctx, user: discord.Member = None):
        """Show behavioral profile for a user."""
        if not bot_ctx.agent or not bot_ctx.agent.moderation:
            await ctx.send("moderation engine not available.")
            return
        if not ctx.guild:
            await ctx.send("must be used in a server.")
            return
        target = user or ctx.author
        guild_id = str(ctx.guild.id)
        user_id = str(target.id)
        profile = bot_ctx.agent.moderation.behavioral_analyzer.get_profile(guild_id, user_id)
        if not profile:
            msg = await _llm_response(f"No behavioral data for {target.display_name}.", f"🔍 No behavioral data for **{target.display_name}** yet.")
            await ctx.send(msg)
            return
        anomaly = bot_ctx.agent.moderation.behavioral_analyzer.get_anomaly_score(guild_id, user_id)
        lines = [f"**Behavioral Profile: {target.display_name}**"]
        lines.append(f"Total messages: {profile.total_messages}")
        lines.append(f"Link ratio: {profile.link_ratio:.0%}")
        lines.append(f"Mention ratio: {profile.mention_ratio:.0%}")
        lines.append(f"Caps ratio: {profile.caps_ratio:.0%}")
        lines.append(f"Anomaly score: {anomaly:.0%}")
        await ctx.send("\n".join(lines))

    @bot.command(name="azure_risk")
    async def azure_risk_cmd(ctx, user: discord.Member = None):
        """Show risk scores for a user or the current server."""
        if not bot_ctx.agent or not bot_ctx.agent.moderation:
            await ctx.send("moderation engine not available.")
            return
        if not ctx.guild:
            await ctx.send("must be used in a server.")
            return
        guild_id = str(ctx.guild.id)
        if user:
            user_id = str(user.id)
            risk = bot_ctx.agent.moderation.risk_engine.get_user_risk(guild_id, user_id)
            await ctx.send(f"**User Risk: {user.display_name}**\nRisk score: {risk:.0%}")
        else:
            user_risks = bot_ctx.agent.moderation.risk_engine.user_risk.get(guild_id, {})
            lines = ["**Server Risk Overview**"]
            if user_risks:
                top_users = sorted(user_risks.items(), key=lambda x: x[1], reverse=True)[:5]
                lines.append("Top user risks:")
                for uid, risk in top_users:
                    member = ctx.guild.get_member(int(uid))
                    name = member.display_name if member else uid[:8]
                    lines.append(f"  - {name}: {risk:.0%}")
            else:
                lines.append("No user risk data yet.")
            await ctx.send("\n".join(lines))
