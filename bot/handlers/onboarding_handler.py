"""Onboarding and server setup handler."""

import logging

logger = logging.getLogger("azure.discord.onboarding")


def register_discord_tools(agent, guild_name_getter, bot_instance):
    """Register Discord-bound tools on the agent."""
    from azure.cognition.goal_state import GoalPriority, GoalStatus

    agent.tools.register(
        "server_info",
        "Return the name of the current Discord server.",
        lambda: guild_name_getter() or "unknown server",
    )

    def _send_ping(user_id: str, channel_id: str, message: str):
        import asyncio
        async def do_ping():
            channel = bot_instance.get_channel(int(channel_id))
            if channel:
                await channel.send(f"<@{user_id}> {message}")
        if bot_instance.loop.is_running():
            asyncio.run_coroutine_threadsafe(do_ping(), bot_instance.loop)
        return f"Ping scheduled for user {user_id}"

    agent.tools.register(
        "send_discord_ping",
        "Sends a direct ping/notification to a Discord user in a specific channel.",
        _send_ping,
        schema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "The Discord User ID to ping."},
                "channel_id": {"type": "string", "description": "The Discord Channel ID to send the ping to."},
                "message": {"type": "string", "description": "The message to send."}
            },
            "required": ["user_id", "channel_id", "message"]
        }
    )

    def _manage_goals(action: str, description: str = "", priority: str = "medium", goal_id: str = ""):
        from bot.context import ctx
        if not ctx.cognitive_pipeline or not ctx.cognitive_pipeline.goal_manager:
            return "Goal manager is not initialized."
        pri_map = {"low": GoalPriority.LOW, "medium": GoalPriority.MEDIUM, "high": GoalPriority.HIGH}
        pri = pri_map.get(priority.lower(), GoalPriority.MEDIUM)
        if action == "create":
            if not description:
                return "Error: description is required to create a goal."
            goal = ctx.cognitive_pipeline.goal_manager.create(description, pri)
            return f"Created goal: {goal.description} (ID: {goal.goal_id})"
        elif action == "complete":
            if not goal_id:
                return "Error: goal_id is required to complete a goal."
            goal = ctx.cognitive_pipeline.goal_manager.get(goal_id)
            if goal:
                goal.status = GoalStatus.COMPLETED
                ctx.cognitive_pipeline.goal_manager._save()
                return f"Marked goal as completed: {goal.description}"
            return f"Goal not found: {goal_id}"
        return f"Unknown action: {action}"

    agent.tools.register(
        "manage_goals",
        "Create or complete long-term server goals. Actions: 'create' or 'complete'.",
        _manage_goals,
        schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "complete"], "description": "The action to perform."},
                "description": {"type": "string", "description": "The goal description (required for 'create')."},
                "priority": {"type": "string", "enum": ["low", "medium", "high"], "description": "The priority of the goal."},
                "goal_id": {"type": "string", "description": "The goal ID (required for 'complete')."}
            },
            "required": ["action"]
        }
    )


async def _execute_agentic_setup(message, request: str, auto_execute: bool = False, progress_msg=None):
    """Execute agentic server setup from a natural language request."""


    from bot.context import ctx

    from ..config import CHUNK_SIZE, SETUP_TIMEOUT, TRUNC_SMALL
    from .llm_handler import _llm_response

    if not ctx.mgmt_tools or not ctx.agent:
        msg = await _llm_response("Setup requested but management tools unavailable.", "Management tools not available.")
        await message.channel.send(msg)
        return
    if not message.guild:
        msg = await _llm_response("Setup requested outside a server.", "Agentic setup only works in servers, not DMs.")
        await message.channel.send(msg)
        return

    bot_member = message.guild.get_member(message.guild.me.id) if hasattr(message.guild, 'me') and message.guild.me else None
    if bot_member is None:
        bot_member = message.guild.get_member(ctx.bot.user.id)
    if not bot_member or not bot_member.guild_permissions.manage_guild:
        msg = await _llm_response(
            "Bot lacks Manage Server permission for setup.",
            "I need **Manage Server** permission."
        )
        await message.channel.send(msg)
        return

    analyzing = progress_msg
    if analyzing is None:
        analysis_msg = await _llm_response(
            f"Analyzing server setup request: '{request[:TRUNC_SMALL]}'",
            f"Analyzing server for request: {request[:TRUNC_SMALL]}..."
        )
        analyzing = await message.channel.send(analysis_msg)

    llm = None
    if ctx.agent and ctx.agent.llm:
        llm = ctx.agent.llm
    else:
        diag = await _llm_response(
            "LLM is not loaded. Explain why and how to fix (model path, env vars).",
            "No LLM loaded. Set AZURE_MODEL_PATH in .env and restart."
        )
        await analyzing.edit(content=diag[:CHUNK_SIZE])
        plan = {"analysis": "Basic structure", "steps": []}
        await ctx.mgmt_tools.execute_plan(message.guild, plan, message.channel,
                                       requester_name=message.author.display_name,
                                       requester_id=message.author.id)
        return

    try:
        progress_update = await _llm_response(
            f"Generating plan for: '{request[:TRUNC_SMALL]}'",
            "Generating plan..."
        )
        await analyzing.edit(content=progress_update[:CHUNK_SIZE])
        plan = await ctx.mgmt_tools.generate_plan(message.guild, request, llm)
        if not plan.get("steps"):
            fail_msg = await _llm_response("Plan generation produced no steps.", "Could not generate a plan.")
            await analyzing.edit(content=fail_msg[:CHUNK_SIZE])
            return
    except Exception as e:
        err_msg = await _llm_response(f"Plan generation failed: {e}", f"Plan generation failed: {e}")
        await analyzing.edit(content=err_msg[:CHUNK_SIZE])
        return

    await analyzing.delete()

    total = len(plan.get("steps", []))
    if auto_execute:
        exec_msg = await _llm_response(
            f"Auto-executing {total}-step plan as owner.",
            f"Auto-executing {total} steps..."
        )
        await message.channel.send(exec_msg[:CHUNK_SIZE])
        await ctx.mgmt_tools.execute_plan(message.guild, plan, message.channel,
                                       requester_name=message.author.display_name,
                                       requester_id=message.author.id)
    else:
        confirm_prompt = await _llm_response(
            f"Ask the user to confirm executing a {total}-step plan. Say they can reply yes/no.",
            f"**Plan ({total} steps)**\nReply **yes** to execute, **no** to cancel."
        )
        await message.channel.send(confirm_prompt[:CHUNK_SIZE])

        from .message_handler import _clear_pending_confirmation, _set_pending_confirmation

        def check_reply(m):
            # Only the original requester may confirm/cancel. This prevents
            # any other user in the channel from hijacking the setup.
            return m.author.id == message.author.id and m.channel.id == message.channel.id

        _set_pending_confirmation(str(message.author.id), str(message.channel.id))
        try:
            reply = await ctx.bot.wait_for(
                "message",
                timeout=float(SETUP_TIMEOUT),
                check=check_reply,
            )
        except TimeoutError:
            timeout_msg = await _llm_response("Setup confirmation timed out.", "No response. Setup cancelled.")
            await message.channel.send(timeout_msg)
            return
        finally:
            _clear_pending_confirmation(str(message.author.id), str(message.channel.id))

        text_lower = reply.content.lower().strip()
        # Single, simple keyword check — no LLM-driven interpretation needed
        # for yes/no, and avoids multi-burn of tokens per setup attempt.
        if text_lower not in ("yes", "y", "go", "do it", "ok", "sure", "yep", "yeah", "confirm"):
            cancel_msg = await _llm_response("Setup cancelled by user.", "Setup cancelled.")
            await message.channel.send(cancel_msg)
            return

        await ctx.mgmt_tools.execute_plan(message.guild, plan, message.channel,
                                       requester_name=message.author.display_name,
                                       requester_id=message.author.id)
