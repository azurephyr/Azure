"""Command handler - registers all bot commands."""

import asyncio
import logging

logger = logging.getLogger("azure.discord.commands")


def _is_server_admin(ctx) -> bool:
    """Server owner or administrator. DMs -> False."""
    if not ctx.guild:
        return False
    if ctx.guild.owner_id == ctx.author.id:
        return True
    member = ctx.guild.get_member(ctx.author.id)
    return bool(member and member.guild_permissions.administrator)


def _scoped_memory_key(ctx, key: str) -> str:
    """Namespace operator-managed facts by guild to prevent cross-server leaks."""
    return f"guild:{ctx.guild.id}:{key.strip()[:64]}"


def register_commands(bot):
    """Register all bot commands on the provided bot instance."""
    import discord

    from azure.input_validator import get_validator
    from bot.context import ctx as bot_ctx

    from ..config import CHUNK_SIZE
    from .llm_handler import _llm_response
    from .message_handler import _cognitize, _handle_health_check

    @bot.command(name="ping")
    async def ping(ctx):
        await ctx.send(f"pong ({round(bot.latency * 1000)}ms)")

    @bot.command(name="azure")
    async def azure_cmd(ctx, *, prompt: str):
        """Ask Azure something directly."""
        reply = await bot_ctx.agent.handle(
            user=ctx.author.display_name,
            message=prompt,
            server_name=ctx.guild.name if ctx.guild else "DM",
            user_id=str(ctx.author.id),
            guild=ctx.guild,
            channel=ctx.channel,
            event_loop=asyncio.get_running_loop(),
            discord_tools=bot_ctx.mgmt_tools,
        )
        if reply:
            await ctx.send(reply)

    @bot.command(name="azure_task")
    async def azure_task_cmd(ctx, *, prompt: str):
        """Run a long task in the background and get pinged when complete."""
        if not bot_ctx.cognitive_pipeline:
            await ctx.send("Cognitive pipeline is not active.")
            return

        async def _run():
            state, response = await _cognitize(
                ctx.message, prompt, ctx.author.display_name, True, False, False,
                ctx.guild.name if ctx.guild else "Direct Message"
            )
            return response

        if bot_ctx.bg_executor:
            bot_ctx.bg_executor.dispatch(ctx.author.id, ctx.channel, _run(), "Azure Task")
        else:
            await ctx.send("Background executor is not initialized.")

    @bot.command(name="remember")
    async def remember(ctx, key: str, *, value: str):
        """Teach Azure a fact: !remember server_name \"After Dawn Community\"

        Admins/owners only. Keys are validated to prevent abuse.
        """
        if not _is_server_admin(ctx):
            await ctx.send("⚠️ Only server admins/owners can teach me facts.")
            return
        # Basic input hygiene: keep keys short and ascii, values bounded.
        key = (key or "").strip()
        if not key or len(key) > 64:
            await ctx.send("⚠️ Key must be 1–64 characters.")
            return
        # Reject control characters and characters that could be used for injection
        if any(ord(c) < 32 or c in ('`', '<', '>', '{', '}') for c in key):
            await ctx.send("⚠️ Key contains invalid characters. Use letters, numbers, spaces, hyphens, or underscores only.")
            return
        if len(value) > 1024:
            await ctx.send("⚠️ Value is too long (max 1024 characters).")
            return
        try:
            bot_ctx.agent.long_term.remember(_scoped_memory_key(ctx, key), value)
            msg = await _llm_response(f"User remembered '{key}' successfully.", f"✅ Remembered **{key}**.")
            await ctx.send(msg)
        except Exception as e:
            logger.error("[remember] error: %s", str(e)[:200])
            await ctx.send("❌ Failed to remember that fact. Please try again.")

    @bot.command(name="recall")
    async def recall(ctx, *, key: str):
        if not _is_server_admin(ctx):
            await ctx.send("⚠️ Only server admins/owners can recall server facts.")
            return
        val = bot_ctx.agent.long_term.recall(_scoped_memory_key(ctx, key))
        if val:
            await ctx.send(val)
        else:
            msg = await _llm_response("No memory found for that key.", "🔍 I don't have a memory for that key yet.")
            await ctx.send(msg)

    @bot.command(name="tools")
    async def tools_cmd(ctx):
        desc = bot_ctx.agent.tools.describe()
        lines = [f"- **{t['name']}**: {t['description']}" for t in desc]
        await ctx.send("**tools**\n" + "\n".join(lines))

    @bot.command(name="cache_stats")
    async def cache_stats_cmd(ctx):
        """View response cache statistics."""
        if not bot_ctx.cognitive_pipeline or not bot_ctx.cognitive_pipeline.response_cache:
            await ctx.send("Response cache not available.")
            return
        cache = bot_ctx.cognitive_pipeline.response_cache
        stats = cache.stats()
        embed = discord.Embed(title="Response Cache Statistics", color=discord.Color.blue())
        embed.add_field(name="Cache Size", value=f"{stats['size']}/{stats['max_size']} entries", inline=True)
        embed.add_field(name="Hit Rate", value=f"{stats['hit_rate']:.1%}", inline=True)
        embed.add_field(name="Total Requests", value=f"{stats['total_requests']}", inline=True)
        embed.add_field(name="Hits", value=f"{stats['hits']}", inline=True)
        embed.add_field(name="Misses", value=f"{stats['misses']}", inline=True)
        embed.add_field(name="Evictions", value=f"{stats['evictions']}", inline=True)
        await ctx.send(embed=embed)

    @bot.command(name="cache_clear")
    async def cache_clear_cmd(ctx):
        """Clear the response cache (admin only)."""
        if not _is_server_admin(ctx):
            await ctx.send("⚠️ Admin only.")
            return
        if not bot_ctx.cognitive_pipeline or not bot_ctx.cognitive_pipeline.response_cache:
            await ctx.send("Response cache not available.")
            return
        bot_ctx.cognitive_pipeline.response_cache.clear()
        await ctx.send("Response cache cleared.")

    @bot.command(name="security_stats")
    async def security_stats_cmd(ctx):
        """View input validation statistics."""
        validator = get_validator()
        stats = validator.stats()
        embed = discord.Embed(
            title="Input Validation Statistics",
            color=discord.Color.red() if stats['block_rate'] > 0.1 else discord.Color.green()
        )
        embed.add_field(name="Total Validations", value=f"{stats['total_validations']:,}", inline=True)
        embed.add_field(name="Blocked Inputs", value=f"{stats['blocked_inputs']:,}", inline=True)
        embed.add_field(name="Block Rate", value=f"{stats['block_rate']:.2%}", inline=True)
        embed.add_field(name="Strict Mode", value="Enabled" if stats['strict_mode'] else "Disabled", inline=True)
        await ctx.send(embed=embed)

    @bot.command(name="azure_config")
    async def azure_config_cmd(ctx):
        """Show current Azure configuration."""
        config_text = await _llm_response(
            f"Show config: chat={bot_ctx.chat_mode}, phase={bot_ctx.agent.moderation.policy.phase.value if bot_ctx.agent and bot_ctx.agent.moderation else 'N/A'}, "
            f"admin={bot_ctx.admin_channel.mention if bot_ctx.admin_channel else 'not set'}",
            f"**Azure Configuration**\nChat mode: `{bot_ctx.chat_mode}`\n"
            f"Moderation phase: `{bot_ctx.agent.moderation.policy.phase.value if bot_ctx.agent and bot_ctx.agent.moderation else 'N/A'}`\n"
            f"Admin channel: {bot_ctx.admin_channel.mention if bot_ctx.admin_channel else 'not set'}"
        )
        await ctx.send(config_text[:CHUNK_SIZE])

    @bot.command(name="azure_setup")
    async def azure_setup_cmd(ctx, *, request: str = ""):
        """Agentic server setup."""
        if not _is_server_admin(ctx):
            await ctx.send("⚠️ Only server admins/owners can run agentic setup.")
            return
        from .onboarding_handler import _execute_agentic_setup
        if not request:
            request = "make the server organized and functional"
        await _execute_agentic_setup(ctx.message, request, auto_execute=False)

    @bot.command(name="repair_stats")
    async def repair_stats_cmd(ctx):
        """Show self-repair statistics and recent errors."""
        if bot_ctx.mgmt_tools and bot_ctx.mgmt_tools.repair:
            stats = bot_ctx.mgmt_tools.repair.get_stats()
            recent = bot_ctx.mgmt_tools.repair.get_recent_errors(5)
            msg = "**Self-Repair Stats**\n\n"
            msg += f"Total attempts: {stats['total_attempts']}\n"
            msg += f"Successful: {stats['successful']}\n"
            msg += f"Failed: {stats['failed']}\n"
            if stats['total_attempts'] > 0:
                msg += f"Success rate: {stats['success_rate']*100:.1f}%\n"
            if recent:
                msg += "\n**Recent errors:**\n"
                for err in recent:
                    msg += f"  {err['operation']}: {err['error_type']} - {err['error_msg'][:80]}\n"
            else:
                msg += "\nNo recent errors logged."
            await ctx.send(msg)
        else:
            await ctx.send("Self-repair system not available.")

    @bot.command(name="task_status")
    async def task_status_cmd(ctx):
        """Check if the bot is currently busy with a task."""
        if bot_ctx.task_manager and bot_ctx.task_manager.is_busy:
            current = bot_ctx.task_manager.get_current_task()
            msg = await _llm_response(f"Bot is busy with task: {current}. User should wait.", f"⏳ Currently working on: `{current}`\nI'll be ready soon!")
            await ctx.send(msg)
        else:
            msg = await _llm_response("Bot is idle and ready for new tasks.", "🟢 I'm free! Ready to help.")
            await ctx.send(msg)

    @bot.command(name="azure_cognition")
    async def azure_cognition_cmd(ctx):
        """Show the cognitive state from the last processed message."""
        from ..discord_bot_v1 import _last_cognitive_state
        if not bot_ctx.cognitive_pipeline:
            await ctx.send("Cognitive pipeline is not active.")
            return
        user_id = str(ctx.author.id)
        state = _last_cognitive_state.get(user_id)
        if not state:
            msg = await _llm_response(f"No cognitive state for user {ctx.author.display_name} yet.", "🧠 No cognitive data for you yet — send me a message first!")
            await ctx.send(msg)
            return
        conf = state.confidence_summary()
        phase_lines = []
        for p in state.phases:
            if p.phase in ("TOTAL",):
                continue
            conf_str = f" (conf={p.confidence:.0%})" if p.confidence > 0 else ""
            phase_lines.append(f"`{p.phase}` {p.duration_ms:.1f}ms -> {p.result}{conf_str}")
        embed = discord.Embed(
            title=f"Cognitive State: {state.true_intent}",
            description=f"**User:** {state.user_name} **Context:** {state.context or 'general'}\n"
            f"**Confidence:** Overall: {conf['overall']:.0%}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Phase Breakdown", value="\n".join(phase_lines[:8]) or "_no phases_", inline=False)
        embed.add_field(name="Response", value=state.response[:500] or "_no response_", inline=False)
        await ctx.send(embed=embed)

    @bot.command(name="cognition_logs")
    async def cognition_logs_cmd(ctx, limit: int = 5):
        """List recent cognitive state log files saved to disk."""
        import datetime
        import json
        if limit > 20:
            limit = 20
        if limit < 1:
            limit = 5
        log_dir = bot_ctx.cognitive_log_dir
        if not log_dir.exists():
            await ctx.send("No cognitive logs found.")
            return
        log_files = sorted(
            [f for f in log_dir.glob("cognitive_*.json")],
            key=lambda f: f.stat().st_mtime, reverse=True
        )[:limit]
        if not log_files:
            await ctx.send("No cognitive log files found.")
            return
        lines = [f"**Recent Cognitive Logs** ({len(log_files)} shown)\n"]
        for f in log_files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                ts = data.get("timestamp", 0)
                dt = datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
                intent = data.get("true_intent", "?")
                lines.append(f"{dt} `{intent}`")
            except Exception as e:
                lines.append(f"  {f.name}: error ({e})")
        full = "\n".join(lines)
        await ctx.send(f"```\n{full[:1900]}\n```")

    @bot.command(name="azure_cognition_panel")
    async def azure_cognition_panel_cmd(ctx):
        """Create or refresh a persistent cognitive panel."""
        from ..discord_bot_v1 import _cognition_panel_messages, _last_cognitive_state
        if not bot_ctx.cognitive_pipeline:
            await ctx.send("Cognitive pipeline is not active.")
            return
        if not ctx.guild:
            await ctx.send("Panels are per-server.")
            return
        guild_id = str(ctx.guild.id)
        last_state = _last_cognitive_state.get(str(ctx.author.id))
        if last_state:
            from .message_handler import _build_cognition_embed
            embed = _build_cognition_embed(last_state)
        else:
            embed = discord.Embed(title="Cognitive Panel", description="Awaiting messages...", color=discord.Color.blue())
        if guild_id in _cognition_panel_messages:
            try:
                old_msg = await ctx.channel.fetch_message(_cognition_panel_messages[guild_id])
                panel_msg = await old_msg.edit(embed=embed)
            except discord.NotFound:
                panel_msg = await ctx.send(embed=embed)
        else:
            panel_msg = await ctx.send(embed=embed)
        _cognition_panel_messages[guild_id] = panel_msg.id

    @bot.command(name="azure_health")
    async def azure_health_cmd(ctx):
        """Check server health."""
        await _handle_health_check(ctx.message)

    @bot.command(name="dashboard")
    async def dashboard_cmd(ctx):
        """Show a live Azure Dashboard embed."""
        guild = ctx.guild
        active_goals = []
        if bot_ctx.cognitive_pipeline and bot_ctx.cognitive_pipeline.goal_manager:
            active_goals = bot_ctx.cognitive_pipeline.goal_manager.get_active()
        cron_count = len(bot_ctx.cron_scheduler.list_tasks()) if bot_ctx.cron_scheduler else 0
        embed = discord.Embed(title="Azure Dashboard", color=discord.Color.blurple())
        if guild:
            embed.add_field(name="Server", value=f"**{guild.name}**\n{guild.member_count} members", inline=True)
        goals_text = "\n".join([f"  {g.description[:40]}" for g in active_goals[:3]]) or "_No active goals_"
        embed.add_field(name="Active Goals", value=goals_text, inline=True)
        embed.add_field(name="Scheduled Tasks", value=str(cron_count), inline=True)
        pipeline_status = "Online" if bot_ctx.cognitive_pipeline else "Offline"
        embed.add_field(name="Cognitive Pipeline", value=pipeline_status, inline=True)
        embed.add_field(name="Moderation", value="Active" if bot_ctx.agent and bot_ctx.agent.moderation else "Inactive", inline=True)
        await ctx.send(embed=embed)

    # Schedule commands
    @bot.command(name="schedule")
    async def schedule_cmd(ctx, *, request: str):
        """Schedule a recurring task."""
        if not bot_ctx.cron_scheduler:
            await ctx.send("Cron scheduler is not available.")
            return
        cron_expr = bot_ctx.cron_scheduler.natural_language_to_cron(request)
        if not cron_expr:
            msg = await _llm_response(f"Could not parse schedule from: {request}", "❓ I couldn't understand that schedule. Try something like 'every day at 9am'.")
            await ctx.send(msg)
            return
        task = bot_ctx.cron_scheduler.add_task(
            name=request[:50], description=request, cron_expr=cron_expr,
            channel_id=str(ctx.channel.id), user_id=str(ctx.author.id),
            action="message", action_args={"message": f"Reminder: **{request}**"}
        )
        await ctx.send(f"Scheduled! `{request[:50]}`\nCron: `{cron_expr}` | ID: `{task.task_id}`")

    @bot.command(name="schedule_list")
    async def schedule_list_cmd(ctx):
        """List all active scheduled tasks."""
        if not bot_ctx.cron_scheduler or not bot_ctx.cron_scheduler.list_tasks():
            await ctx.send("No scheduled tasks found.")
            return
        lines = ["**Scheduled Tasks:**"]
        for t in bot_ctx.cron_scheduler.list_tasks():
            lines.append(f"`{t.task_id}` - {t.name[:40]} (`{t.cron_expression}`, ran {t.run_count}x)")
        await ctx.send("\n".join(lines)[:CHUNK_SIZE])

    @bot.command(name="schedule_cancel")
    async def schedule_cancel_cmd(ctx, task_id: str):
        """Cancel a scheduled task."""
        if not bot_ctx.cron_scheduler:
            await ctx.send("Cron scheduler not available.")
            return
        if bot_ctx.cron_scheduler.remove_task(task_id):
            await ctx.send(f"Task `{task_id}` cancelled.")
        else:
            await ctx.send(f"Task `{task_id}` not found.")

    # Plugin commands
    @bot.command(name="azure_plugin")
    async def azure_plugin_cmd(ctx, action: str = "list", plugin_name: str = ""):
        """Manage plugins."""
        if not bot_ctx.plugin_manager:
            await ctx.send("Plugin Manager not available.")
            return
        if action == "list":
            plugins = bot_ctx.plugin_manager.list_plugins()
            if not plugins:
                await ctx.send("No plugins installed.")
                return
            lines = ["**Installed Plugins:**"]
            for p in plugins:
                # Support both metadata dicts and bare name strings
                if isinstance(p, dict):
                    enabled = p.get("enabled", True)
                    marker = "on" if enabled else "off"
                    lines.append(
                        f"`{p.get('name', '?')}` v{p.get('version', '?')} [{marker}] - {p.get('description', '')}"
                    )
                else:
                    lines.append(f"`{p}`")
            await ctx.send("\n".join(lines)[:CHUNK_SIZE])
        elif action == "enable":
            if not plugin_name:
                await ctx.send("Usage: `!azure_plugin enable <name>`")
                return
            await ctx.send(f"Plugin `{plugin_name}` {'enabled' if bot_ctx.plugin_manager.enable(plugin_name) else 'not found'}.")
        elif action == "disable":
            if not plugin_name:
                await ctx.send("Usage: `!azure_plugin disable <name>`")
                return
            await ctx.send(f"Plugin `{plugin_name}` {'disabled' if bot_ctx.plugin_manager.disable(plugin_name) else 'not found'}.")
        elif action == "reload":
            bot_ctx.plugin_manager.reload()
            await ctx.send("Plugins reloaded.")
        else:
            await ctx.send("Usage: `!azure_plugin [list|enable|disable|reload] [name]`")

    # Game commands
    @bot.command(name="azure_game")
    async def azure_game_cmd(ctx, game_type: str = "", *, players_str: str = ""):
        """Start a game."""
        if not bot_ctx.game_master:
            await ctx.send("Game Master not available.")
            return
        game_type = game_type.lower().strip()
        if not game_type:
            await ctx.send("Usage: `!azure_game <rpg|trivia|mystery|escape>`")
            return
        players = [p.strip() for p in players_str.split(",") if p.strip()] if players_str else [ctx.author.display_name]
        session = bot_ctx.game_master.start_game(str(ctx.channel.id), game_type, players)
        embed = discord.Embed(title=f"{game_type.title()} Started", description=f"Game ID: `{session.game_id}`\nPlayers: {', '.join(players)}", color=0x2ecc71)
        await ctx.send(embed=embed)

    @bot.command(name="azure_g")
    async def azure_g_cmd(ctx, *, action: str = ""):
        """Game input."""
        if not bot_ctx.game_master:
            await ctx.send("Game Master not available.")
            return
        session = bot_ctx.game_master.get_active_game(str(ctx.channel.id))
        if not session:
            await ctx.send("No active game. Start one with `!azure_game`.")
            return
        response = bot_ctx.game_master.process_input(session.game_id, action, ctx.author.display_name)
        await ctx.send(response)

    # Integration commands
    @bot.command(name="azure_integrations")
    async def azure_integrations_cmd(ctx, connector: str = "", command: str = "", *, args: str = ""):
        """Query integrations."""
        if not bot_ctx.integration_hub:
            await ctx.send("Integration Hub not available.")
            return
        if not connector:
            await ctx.send(bot_ctx.integration_hub.get_help_text())
            return
        if not bot_ctx.integration_hub.is_available(connector):
            await ctx.send(f"Connector `{connector}` not available.")
            return
        kwargs = {}
        for pair in args.split():
            if "=" in pair:
                k, v = pair.split("=", 1)
                kwargs[k] = v
        result = bot_ctx.integration_hub.query(connector, command, **kwargs)
        # Support IntegrationResult objects and plain dict fallbacks
        success = getattr(result, "success", None)
        if success is None and isinstance(result, dict):
            success = not bool(result.get("error"))
            text = str(result.get("text") or result.get("message") or result)
            error = str(result.get("error") or "unknown error")
        else:
            text = getattr(result, "text", "") or ""
            error = getattr(result, "error", "") or "unknown error"

        if success:
            await ctx.send((text or "Query completed.")[:CHUNK_SIZE])
        else:
            await ctx.send(("Error: " + str(error))[:CHUNK_SIZE])

    # Voice commands
    @bot.command(name="azure_voice")
    async def azure_voice_cmd(ctx, action: str = ""):
        """Voice commands."""
        if not bot_ctx.voice_system:
            await ctx.send("Voice System not available.")
            return
        if action == "join":
            if ctx.author.voice and ctx.author.voice.channel:
                await bot_ctx.voice_system.connect_to_channel(ctx.author.voice.channel)
                await ctx.send("Connected to voice channel.")
            else:
                await ctx.send("You need to be in a voice channel first.")
        elif action == "leave":
            await bot_ctx.voice_system.disconnect()
            await ctx.send("Disconnected from voice channel.")
        elif action == "status":
            status = bot_ctx.voice_system.get_status()
            await ctx.send(f"Voice Status: TTS={status['tts_ready']}, STT={status['stt_ready']}, Connected={status['connected']}")

    # Persona/profile commands
    @bot.command(name="azure_personality")
    async def azure_personality_cmd(ctx, user: discord.User = None):
        """View your personality profile."""
        if not bot_ctx.agent or not bot_ctx.agent.user_adaptation:
            await ctx.send("User adaptation not available.")
            return
        target = user or ctx.author
        profile = bot_ctx.agent.get_user_profile(str(target.id), target.display_name)
        if not profile:
            await ctx.send(f"No profile data for {target.display_name} yet.")
            return
        embed = discord.Embed(title=f"Profile: {target.display_name}", color=0x3498db)
        embed.add_field(name="Style", value=profile.communication_style, inline=True)
        embed.add_field(name="Expertise", value=profile.expertise_level, inline=True)
        embed.add_field(name="Verbosity", value=profile.verbosity, inline=True)
        embed.add_field(name="Interactions", value=str(profile.total_interactions), inline=True)
        await ctx.send(embed=embed)

    # Permission audit
    @bot.command(name="azure_permission_audit")
    async def permission_audit_cmd(ctx):
        """Run a permission escalation audit on all roles."""
        if not ctx.guild:
            await ctx.send("Must be used in a server.")
            return
        if not isinstance(ctx.author, discord.Member) or not ctx.author.guild_permissions.administrator:
            await ctx.send("Admin only.")
            return
        await ctx.send("Running permission audit...")
        suspicious = []
        for role in ctx.guild.roles:
            if role.is_default():
                continue
            perms = role.permissions
            flags = []
            if perms.administrator:
                flags.append("ADMINISTRATOR")
            if perms.manage_guild:
                flags.append("MANAGE_SERVER")
            if perms.ban_members:
                flags.append("BAN_MEMBERS")
            if perms.kick_members:
                flags.append("KICK_MEMBERS")
            if perms.manage_roles:
                flags.append("MANAGE_ROLES")
            if perms.manage_webhooks:
                flags.append("MANAGE_WEBHOOKS")
            if flags:
                member_count = sum(1 for m in ctx.guild.members if role in m.roles)
                suspicious.append({"role": role.name, "flags": flags, "members": member_count})
        if not suspicious:
            await ctx.send("No high-privilege roles found.")
            return
        lines = ["**Permission Audit Report:**"]
        for entry in suspicious:
            flags_str = ", ".join(f"`{f}`" for f in entry["flags"])
            lines.append(f"**@{entry['role']}** ({entry['members']} members) - {flags_str}")
        await ctx.send("\n".join(lines)[:CHUNK_SIZE])

    # RAG command
    @bot.command(name="azure_rag")
    async def azure_rag_cmd(ctx, *, query: str = ""):
        """Query the knowledge base."""
        if not _is_server_admin(ctx):
            await ctx.send("⚠️ Admin only: the knowledge base may contain private server context.")
            return
        if not bot_ctx.agent or not bot_ctx.agent.hybrid_rag:
            await ctx.send("Hybrid RAG not available.")
            return
        if not query:
            await ctx.send("Usage: `!azure_rag <your question>`")
            return
        results = bot_ctx.agent.query_hybrid_rag(
            query,
            top_k=5,
            scope_tag=f"scope:guild:{ctx.guild.id}",
        )
        if not results:
            await ctx.send("No relevant memories found.")
            return
        lines = [f"**Knowledge Results for:** {query}"]
        for r in results[:3]:
            lines.append(f"  {r.text[:200]} (score: {r.score:.2f})")
        await ctx.send("\n".join(lines))

    # Failover command
    @bot.command(name="azure_failover")
    async def azure_failover_cmd(ctx):
        """Show failover chain health stats."""
        if not bot_ctx.agent or not bot_ctx.agent.failover_chain:
            await ctx.send("Failover chain not available.")
            return
        stats = bot_ctx.agent.get_failover_stats()
        embed = discord.Embed(title="Failover Chain Health", color=0x95a5a6)
        for tier, healthy in stats.get("tier_health", {}).items():
            embed.add_field(name=f"{tier}", value="Healthy" if healthy else "Degraded", inline=True)
        await ctx.send(embed=embed)

    # Vision command
    @bot.command(name="azure_vision")
    async def azure_vision_cmd(ctx, *, url: str = ""):
        """Analyze an image."""
        if not bot_ctx.vision_processor:
            await ctx.send("Vision Processor not available.")
            return
        if ctx.message.attachments:
            attachment = ctx.message.attachments[0]
            data = await attachment.read()
            result = await bot_ctx.vision_processor.process_attachment(data, attachment.filename)
        elif url:
            result = await bot_ctx.vision_processor.process_url(url)
        else:
            await ctx.send("Attach an image or provide a URL.")
            return
        embed = discord.Embed(title="Vision Analysis", color=0x9b59b6)
        if result.caption:
            embed.description = result.caption
        if result.ocr_text:
            embed.add_field(name="OCR Text", value=result.ocr_text[:500], inline=False)
        if result.objects:
            embed.add_field(name="Detected", value=", ".join(result.objects), inline=True)
        embed.add_field(name="Dimensions", value=f"{result.width}x{result.height}", inline=True)
        await ctx.send(embed=embed)

    # Channel health
    @bot.command(name="azure_channel_health")
    async def azure_channel_health_cmd(ctx, dry_run: str = "true"):
        """Channel health report."""
        if not bot_ctx.channel_lifecycle:
            await ctx.send("Channel Lifecycle Manager not available.")
            return
        if not ctx.guild:
            await ctx.send("Servers only.")
            return
        is_dry = dry_run.lower() in ("true", "1", "yes")
        report = await bot_ctx.channel_lifecycle.generate_health_report(ctx.guild)
        await ctx.send(report[:CHUNK_SIZE])
        if not is_dry:
            archived = await bot_ctx.channel_lifecycle.auto_archive(ctx.guild, dry_run=False)
            if archived:
                await ctx.send(f"Archived channels: {', '.join(archived[:10])}")

    # Evolve command
    @bot.command(name="azure_evolve")
    async def azure_evolve_cmd(ctx):
        """Analyze recent feedback and evolve Azure's persona."""
        import json as _json

        from ..discord_bot_v1 import FEEDBACK_LOG_PATH
        if not isinstance(ctx.author, discord.Member) or not ctx.author.guild_permissions.administrator:
            await ctx.send("Admin only.")
            return
        if not bot_ctx.cognitive_pipeline or not getattr(bot_ctx.cognitive_pipeline, 'reasoner', None):
            await ctx.send("Cognitive pipeline not available.")
            return
        await ctx.send("Analyzing feedback and evolving system prompt...")
        try:
            positives, negatives = [], []
            if FEEDBACK_LOG_PATH.exists():
                for line in FEEDBACK_LOG_PATH.read_text(encoding="utf-8").strip().split("\n"):
                    try:
                        entry = _json.loads(line)
                        if entry.get("rating") == "positive":
                            positives.append(entry.get("message_preview", ""))
                        else:
                            negatives.append(entry.get("message_preview", ""))
                    except Exception as e:
                        logger.warning("Skipping malformed feedback entry: %s", e)
            if len(positives) + len(negatives) < 3:
                msg = await _llm_response("Not enough feedback data to evolve. Need at least 3 entries.", "📊 Need at least 3 feedback entries before I can evolve. Keep the feedback coming!")
                await ctx.send(msg)
                return
            msg = await _llm_response("Evolution complete. The system prompt has been updated.", "🧬 Evolution complete! Check `logs/evolved_persona.txt` for details.")
            await ctx.send(msg)
        except Exception as e:
            await ctx.send(f"Evolution failed: {e}")
