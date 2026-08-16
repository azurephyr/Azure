"""Background task loops for the Azure Discord bot."""

from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord.ext import tasks

from bot.config import (
    CHUNK_SIZE,
    DEFAULT_LOOKBACK_HOURS,
    TRUNC_DESC,
    TRUNC_SMALL,
)

logger = logging.getLogger("azure.discord")


# ---------------------------------------------------------------------------
# Cron scheduler loop
# ---------------------------------------------------------------------------

@tasks.loop(minutes=1.0)
async def cron_check_loop():
    """Check for due cron tasks every minute."""
    from bot.context import ctx

    if not ctx.cron_scheduler:
        return
    due = ctx.cron_scheduler.get_due_tasks()
    for task in due:
        try:
            channel = ctx.bot.get_channel(int(task.channel_id))
            if not channel:
                continue
            if task.action == "message":
                msg = task.action_args.get("message", f"⏰ Scheduled task: **{task.name}**")
                await channel.send(f"<@{task.user_id}> {msg}")
            elif task.action == "pipeline" and ctx.cognitive_pipeline:
                prompt = task.action_args.get("prompt", task.name)
                state = await ctx.cognitive_pipeline.process(
                    message=prompt,
                    user_name="[Cron]",
                    is_directed=True,
                )
                if state and state.response:
                    await channel.send(f"⏰ **{task.name}**\n{state.response[:CHUNK_SIZE]}")
            ctx.cron_scheduler.mark_ran(task.task_id)
        except Exception as e:
            logger.error(f"[cron] Failed to run task {task.task_id}: {e}")


# ---------------------------------------------------------------------------
# Autonomous agent heartbeat (30-minute proactive scan)
# ---------------------------------------------------------------------------

@tasks.loop(minutes=30.0)
async def autonomous_agent_loop():
    """Wake up periodically, analyze server state, and propose goals + proactive suggestions."""
    from bot.context import ctx

    if not ctx.cognitive_mode or not ctx.cognitive_pipeline:
        return
    if not ctx.bot.guilds:
        return

    for guild in ctx.bot.guilds:
        channel = ctx.admin_channel if ctx.admin_channel and ctx.admin_channel.guild.id == guild.id else guild.system_channel
        if not channel:
            continue

        try:
            # === v3: Proactive Engine Suggestions ===
            if ctx.proactive_engine is not None:
                loop = asyncio.get_running_loop()
                suggestions = await loop.run_in_executor(
                    None, ctx.proactive_engine.generate_suggestions, str(guild.id), DEFAULT_LOOKBACK_HOURS
                )
                if suggestions:
                    top = suggestions[0]
                    if top.confidence > 0.7:
                        embed = discord.Embed(
                            title="🤖 Proactive Suggestion",
                            description=top.description,
                            color=0x3498db
                        )
                        embed.add_field(name="Expected Outcome", value=top.expected_outcome, inline=False)
                        embed.add_field(name="Confidence", value=f"{top.confidence:.0%}", inline=True)
                        embed.set_footer(text="Azure Proactive Intelligence • React 👍 to accept")
                        msg = await channel.send(embed=embed)
                        ctx.proactive_engine._suggestion_history.append({
                            "id": top.suggestion_id, "message_id": msg.id,
                            "accepted": None, "time": time.time()
                        })

            # === Existing Goal-Based Proposals ===
            if not getattr(ctx.cognitive_pipeline, 'proactive_engine', None):
                continue

            active_goals = ctx.cognitive_pipeline.goal_manager.get_active()
            if not active_goals:
                continue

            goal = active_goals[0]
            pct = int(goal.progress * 100)
            desc = goal.description
            proposal = (
                f"While reviewing the server asynchronously, I checked on our goal to **{desc}** ({pct}% complete).\n"
            )

            active_blockers = [b for b in goal.blockers if not b.resolved]
            if active_blockers:
                proposal += f"⚠️ We have an active blocker: *{active_blockers[0].description}*.\nHow would you like me to resolve this?"
            elif pct < 100:
                proposal += "Would you like me to draft an execution plan for the next step?"
            else:
                continue

            embed = discord.Embed(
                title="🧠 Autonomous Heartbeat Proposal",
                description=proposal,
                color=0x2ecc71
            )
            embed.set_footer(text="Azure Proactive Agent • Triggered by background timer")
            await channel.send(embed=embed)

        except Exception as e:
            logger.info(f"[autonomous_agent] error in guild {guild.name}: {e}")


@autonomous_agent_loop.before_loop
async def before_autonomous_agent_loop():
    """Wait for the Discord bot to be ready before starting the autonomous agent loop."""
    from bot.context import ctx
    await ctx.bot.wait_until_ready()


# ---------------------------------------------------------------------------
# Goal executor (2-minute LLM-driven goal advancement)
# ---------------------------------------------------------------------------

@tasks.loop(minutes=2.0)
async def goal_executor_loop():
    """Background goal executor: advances active goals using LLM autonomously."""
    from bot.context import ctx

    if not ctx.cognitive_mode or not ctx.cognitive_pipeline or not ctx.agent or not ctx.agent.llm:
        return
    goal_mgr = getattr(ctx.cognitive_pipeline, 'goal_manager', None)
    if not goal_mgr:
        return
    active = goal_mgr.get_active()
    if not active:
        return
    llm = ctx.agent.llm
    for goal in active:
        desc = goal.description[:TRUNC_DESC] if goal.description else "<unknown>"
        try:
            if goal.progress >= 1.0:
                continue
            active_blockers = [b for b in goal.blockers if not b.resolved]
            if active_blockers:
                continue
            pct = int(goal.progress * 100)
            prompt = (
                f"You are Azure's goal executor. Current goal: '{desc}' ({pct}% complete).\n"
                f"Subgoals: {[sg.description for sg in goal.subgoals if sg.status.value != 'completed']}\n"
                f"Decide the single next action to advance this goal. "
                f"Output ONLY the action description in one short sentence."
            )
            resp = await asyncio.get_event_loop().run_in_executor(
                None, lambda p=prompt: llm.chat([{"role": "user", "content": p}], max_tokens=80, temperature=0.5)
            )
            if resp:
                action = resp.strip().strip('"\'')
                goal_mgr.add_subgoal(goal.goal_id, action)
                logger.info(f"[goal_executor] advanced '{desc}': {action[:TRUNC_SMALL]}")

        except Exception as e:
            logger.info(f"[goal_executor] error on '{desc}': {e}")


@goal_executor_loop.before_loop
async def before_goal_executor_loop():
    """Wait for the Discord bot to be ready before starting the goal executor loop."""
    from bot.context import ctx
    await ctx.bot.wait_until_ready()


# ---------------------------------------------------------------------------
# Periodic proactive moderation scan (5-minute interval)
# ---------------------------------------------------------------------------

@tasks.loop(minutes=5)
async def periodic_scan():
    """Periodic proactive scan of all guilds (proactive mode only)."""
    from bot.context import ctx

    if not ctx.agent or not ctx.agent.moderation:
        return
    if getattr(getattr(ctx.agent.moderation, 'policy', None), 'mode', None) != "proactive":
        return

    for guild in ctx.bot.guilds:
        try:
            reports = await ctx.agent.moderation.periodic_scan(guild)
            if reports and ctx.admin_channel:
                for report in reports:
                    embed_dict = report.to_embed_dict()
                    embed = discord.Embed.from_dict(embed_dict)
                    await ctx.admin_channel.send(embed=embed)
            if reports:
                logger.info(f"[MOD] proactive scan on {guild.name}: {len(reports)} campaigns detected")

        except Exception as e:
            logger.error(f"[moderation] periodic scan error: {e}")


@periodic_scan.before_loop
async def before_periodic_scan():
    """Wait for the Discord bot to be ready before starting the periodic scan loop."""
    from bot.context import ctx
    await ctx.bot.wait_until_ready()


# ---------------------------------------------------------------------------
# Autonomous cross-channel scan (30-second interval — Phase Alpha)
# ---------------------------------------------------------------------------

@tasks.loop(seconds=30)
async def autonomous_scan_task():
    """
    Phase Alpha: Autonomous cross-channel scan for raids, spam waves, coordination.
    Runs every 30 seconds. Analyzes temporal patterns and takes action if needed.
    """
    from bot.context import ctx

    if not ctx.features.autonomous:
        return
    if not ctx.agent or not ctx.agent.moderation:
        return

    for guild in ctx.bot.guilds:
        try:
            report = await ctx.agent.moderation.autonomous_scan(guild)
            if report.get("threat_detected") and ctx.admin_channel:
                users = report.get("users_involved", [])
                channels = report.get("channels_involved", [])
                raid_prob = report.get("raid_probability", 0.0)
                action = report.get("action", "none")
                actions_exec = report.get("actions_executed", 0)
                explanation = report.get("explanation", "")

                lines = [
                    "🚨 **Azure Situation Alert**",
                    f"**Server:** {guild.name}",
                    f"**Messages flagged:** {report.get('messages_flagged', 0)}",
                    f"**Users involved:** {len(users)}",
                    f"**Channels affected:** {len(channels)}",
                    f"**Raid probability:** {raid_prob:.0%}",
                    f"**Action taken:** {action} ({actions_exec} executed)",
                    f"**Explanation:** {explanation}",
                ]
                if report.get("dry_run"):
                    lines.append("⚠️ **DRY RUN** — no actual actions were taken.")
                if report.get("human_review"):
                    lines.append("🔍 **Human review recommended.**")

                await ctx.admin_channel.send("\n".join(lines))
                logger.info(f"[MOD] autonomous scan on {guild.name}: {explanation}")

        except Exception as e:
            logger.error(f"[moderation] autonomous scan error: {e}")


@autonomous_scan_task.before_loop
async def before_autonomous_scan_task():
    """Wait for the Discord bot to be ready before starting the autonomous scan loop."""
    from bot.context import ctx
    await ctx.bot.wait_until_ready()


# ---------------------------------------------------------------------------
# Ghost moderation maintenance (5-minute interval)
# ---------------------------------------------------------------------------

@tasks.loop(minutes=5.0)
async def ghost_maintenance_loop():
    """Clean up expired shadow mutes and log statistics."""
    try:
        from azure.ghost_moderation import cleanup_expired_shadow_mutes
        from bot.context import ctx
        if not ctx.bot:
            return
        count = await cleanup_expired_shadow_mutes(ctx.bot)
        if count > 0:
            logger.info("[ghost] Cleaned up %d expired shadow mute(s)", count)
    except Exception:
        logger.exception("[ghost] maintenance error")


@ghost_maintenance_loop.before_loop
async def before_ghost_maintenance_loop():
    """Wait for the Discord bot to be ready."""
    from bot.context import ctx
    await ctx.bot.wait_until_ready()


# ---------------------------------------------------------------------------
# Dead chat revival scanning (3-minute interval)
# ---------------------------------------------------------------------------

@tasks.loop(minutes=3.0)
async def revival_scan_loop():
    """Scan guilds for channels that are ready for revival."""
    try:
        from azure.dead_chat_revival import get_all_revivable_channels, get_db
        from bot.context import ctx
        if not ctx.bot:
            return

        db = get_db()
        for guild in ctx.bot.guilds:
            ready_channels = get_all_revivable_channels(str(guild.id), db=db)
            for ch_cfg in ready_channels:
                channel = guild.get_channel(int(ch_cfg["channel_id"]))
                if channel and isinstance(channel, discord.TextChannel):
                    from azure.dead_chat_revival import send_revival
                    await send_revival(channel)
    except Exception:
        logger.exception("[revival] scan error")


@revival_scan_loop.before_loop
async def before_revival_scan_loop():
    """Wait for the Discord bot to be ready."""
    from bot.context import ctx
    await ctx.bot.wait_until_ready()
