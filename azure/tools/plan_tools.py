"""Plan generation and execution tools."""
import asyncio
import copy
import json
import logging
import re
import threading

import discord

from .types import StepResult

logger = logging.getLogger("tools.plan_tools")

# Track active plan executions to prevent multi-user overload
_active_executions = 0
_exec_lock = threading.Lock()
MAX_CONCURRENT_EXECUTIONS = 2


class PlanToolsMixin:
    """Mixin providing plan generation and execution for DiscordManagementTools."""

    MAX_RETRIES = 2
    RETRY_DELAY_BASE = 1

    # Normalize LLM-generated action names to actual tool names
    _ACTION_ALIASES = {
        "set_channel_permissions": "set_permissions",
        "clear_channel_permissions": "clear_permissions",
        "sync_channel_permissions": "sync_permissions",
        "set_channel_name": "edit_channel",
        "set_channel_topic": "edit_channel",
        "rename_channel": "edit_channel",
        "set_channel_slowmode": "edit_channel",
        "set_slowmode": "edit_channel",
        "bulk_delete_messages": "purge_messages",
        "delete_messages": "purge_messages",
        "clear_messages": "purge_messages",
        "clean_messages": "purge_messages",
        "clean_channel": "purge_messages",
        "kick_member": "kick",
        "ban_member": "ban",
        "unban_member": "unban",
        "timeout_member": "timeout",
        "set_nickname_member": "set_nickname",
        "move_member_to_voice": "move_voice",
        "deafen_member": "deafen",
        "mute_member": "mute",
        "create_text_channel": "create_channel",
        "create_voice_channel": "create_channel",
        "create_category_channel": "create_category",
        "delete_text_channel": "delete_channel",
        "delete_voice_channel": "delete_channel",
        "delete_category_channel": "delete_category",
        "create": "create_channel",
    }

    async def generate_plan(self, guild: discord.Guild, request: str, llm) -> dict:
        state = await self.get_server_state(guild)
        prompt = self._build_planning_prompt(state, request)
        messages = [
            {"role": "system", "content": f"You are a Discord server setup expert. User request: {request}\nGenerate a step-by-step plan."},
            {"role": "user", "content": prompt},
        ]
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, lambda: llm.chat(messages, max_tokens=1024, temperature=0.3))
        return self._parse_plan(raw)

    def _build_planning_prompt(self, state: dict, request: str) -> str:
        roles_str = ", ".join([r["name"] for r in state["roles"]]) or "(none)"
        channels_str = ", ".join([c["name"] for c in state["channels"]]) or "(none)"
        cats_str = ", ".join([c["name"] for c in state["categories"]]) or "(none)"

        return (
            f"User request: {request}\n"
            f"Generate a step-by-step plan.\n\n"
            f"SERVER STATE:\n"
            f"  Name: {state['server_name']}\n"
            f"  Members: {state['member_count']}\n"
            f"  Roles: {roles_str}\n"
            f"  Channels: {channels_str}\n"
            f"  Categories: {cats_str}\n"
            f"  Verification: {state.get('verification_level', 'unknown')}\n\n"
            f"Return ONLY valid JSON with 'analysis' and 'steps'."
        )

    def _parse_plan(self, raw: str) -> dict:
        raw = raw.strip()
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
            return {"analysis": "Failed to parse plan.", "steps": [], "raw": raw}

    async def execute_plan(self, guild: discord.Guild, plan: dict, ctx,
                            confirm_destructive: bool = True, requester_name: str = "",
                            requester_id: int = None, require_authorization: bool = True) -> list[StepResult]:
        """
        Execute a Discord management plan.

        SECURITY NOTE: This function can perform destructive operations.
        Authorization and confirmation are REQUIRED for production safety.
        """
        global _active_executions
        steps = plan.get("steps", [])
        if not steps:
            return []

        # Reject if too many concurrent executions
        with _exec_lock:
            if _active_executions >= MAX_CONCURRENT_EXECUTIONS:
                logger.warning(
                    "[plan_tools] Rejected plan from %s — %d executions already active",
                    requester_name, _active_executions,
                )
                return [StepResult(
                    success=False, action="execute_plan", name="queue_full",
                    error="Too many concurrent requests. Please wait a moment and try again.",
                )]
            _active_executions += 1
        try:
            # CRITICAL: Authorization gate
            if require_authorization:
                if not requester_id:
                    logger.warning("[plan_tools] Blocked plan execution - no requester_id provided")
                    return [StepResult(
                        success=False, action="execute_plan", name="auth_required",
                        error="Cannot execute plan without requester identity.",
                    )]

                # Check if requester has necessary Discord permissions
                requester = guild.get_member(requester_id)
                if not requester:
                    logger.warning(f"[plan_tools] Blocked plan execution - requester {requester_id} not in guild")
                    return [StepResult(
                        success=False, action="execute_plan", name="auth_failed",
                        error="Requester not found in guild.",
                    )]

                # Verify requester is owner or administrator
                is_owner = guild.owner_id == requester_id
                is_admin = requester.guild_permissions.administrator

                if not (is_owner or is_admin):
                    logger.warning(f"[plan_tools] Blocked plan execution - {requester_name} ({requester_id}) lacks admin rights")
                    return [StepResult(
                        success=False, action="execute_plan", name="permission_denied",
                        error=f"{requester.mention} lacks administrator permissions.",
                    )]

                logger.info(f"[plan_tools] Authorization passed: {requester_name} ({requester_id}) is {'owner' if is_owner else 'admin'}")

            preflight = await self.preflight_check(guild, {"steps": steps})
            if not preflight["can_execute"]:
                missing = ", ".join(preflight.get("missing", []))
                logger.warning("[plan_tools] Blocked plan execution - bot permissions missing: %s", missing)
                return [StepResult(
                    success=False, action="execute_plan", name="preflight_failed",
                    error=f"Bot lacks required permissions: {missing}",
                )]

            # CRITICAL: Destructive action confirmation gate
            if confirm_destructive:
                destructive_actions = ["delete_role", "delete_channel", "delete_category", "delete_webhook",
                                       "delete_scheduled_event", "kick", "ban", "unban", "timeout", "delete_emoji", "delete_sticker",
                                       "delete_thread", "delete_server_template", "delete_automod_rule",
                                       "revoke_invite", "prune_members", "end_stage_instance"]
                has_destructive = any(step.get("action") in destructive_actions for step in steps)

                if has_destructive:
                    destructive_steps = [f"- {step.get('action')}: {self._extract_step_name(step)}"
                                         for step in steps if step.get("action") in destructive_actions]
                    warning_msg = (
                        f"⚠️ **DESTRUCTIVE ACTIONS DETECTED**\n\n"
                        f"This plan contains {len(destructive_steps)} destructive action(s):\n" +
                        "\n".join(destructive_steps[:5]) +
                        (f"\n...and {len(destructive_steps) - 5} more" if len(destructive_steps) > 5 else "") +
                        "\n\nReact ✅ to confirm or ❌ to cancel within 60 seconds."
                    )
                    warning = await ctx.send(warning_msg)
                    await warning.add_reaction("✅")
                    await warning.add_reaction("❌")

                    chan = ctx.channel if hasattr(ctx, 'channel') else ctx
                    author_id = requester_id

                    def check_reaction(r, u):
                        return (u.id == author_id and r.message.id == warning.id
                                and str(r.emoji) in ("✅", "❌"))

                    def check_text(m):
                        content = m.content.strip().lower()
                        return (m.author.id == author_id and m.channel.id == chan.id
                                and any(w in content for w in ("confirm", "cancel", "abort", "proceed", "yes", "no")))

                    _tasks = [asyncio.create_task(self.bot.wait_for("reaction_add", check=check_reaction)),
                              asyncio.create_task(self.bot.wait_for("message", check=check_text))]
                    try:
                        done, pending = await asyncio.wait(_tasks, timeout=60, return_when=asyncio.FIRST_COMPLETED)
                    except TimeoutError:
                        done, pending = set(), set(_tasks)
                    for t in pending:
                        t.cancel()
                    if done:
                        result = done.pop().result()
                        if isinstance(result, tuple):
                            reaction, user = result
                            confirmed = str(reaction.emoji) == "✅"
                        else:
                            confirmed = any(w in result.content.strip().lower() for w in ("confirm", "yes", "proceed"))
                    else:
                        confirmed = False

                    if confirmed:
                        await ctx.send("✅ Confirmation received. Executing plan...")
                        logger.info(f"[plan_tools] Destructive plan confirmed by {requester_name} ({requester_id})")
                    else:
                        logger.info(f"[plan_tools] Plan cancelled by {requester_name} ({requester_id})")
                        return [StepResult(
                            success=False, action="execute_plan", name="cancelled",
                            error="Plan cancelled — destructive actions not confirmed.",
                        )]

            steps = copy.deepcopy(steps)

            for step in steps:
                params = step.pop("params", {})
                for k, v in params.items():
                    if k not in step:
                        step[k] = v

            for step in steps:
                if step.get("action") == "create_role":
                    name = step.get("name", "")
                    if "with color" in name.lower():
                        logger.info(f"[tools] CLEANING role name: '{name}'")
                        m = re.match(r'^(.*?)\s+with\s+color\s+', name, re.IGNORECASE)
                        if m:
                            step["name"] = m.group(1).strip()
                            color_text = name[m.end():].strip()
                            if color_text and not step.get("color"):
                                step["color"] = color_text
                        logger.info(f"[tools] CLEANED role name: '{step.get('name')}', color: '{step.get('color')}'")

            total = len(steps)
            progress_msg = await self._send_progress_embed(ctx, plan, 0, total, [], " Starting...")

            results = []
            for i, step in enumerate(steps, 1):
                action = step.get("action", "unknown")
                name = self._extract_step_name(step)

                await self._update_progress_embed(progress_msg, plan, i - 1, total, results, f" {action} '{name}'...")

                result = await self._execute_single_step(guild, step, confirm_destructive)

                if self.tracker:
                    self.tracker.log_change(
                        guild_id=guild.id, guild_name=guild.name,
                        action=action, target={"name": name, "id": result.target_id},
                        before=result.before_state, after=result.after_state,
                        performed_by=requester_name, request_text=plan.get("analysis", ""),
                        success=result.success, error=result.error,
                    )
                results.append(result)

                await self._update_progress_embed(progress_msg, plan, i, total, results, None)

            await self._finalize_progress_embed(progress_msg, plan, results)

            followups = []
            if self.health:
                followups = self.health.suggest_followups(guild, plan.get("analysis", "completed tasks"))
            if followups:
                followup_text = "\n".join(f"  {f}" for f in followups)
                await ctx.send(f"**Suggestions:**\n{followup_text}")

            return results
        finally:
            with _exec_lock:
                _active_executions = max(0, _active_executions - 1)

    async def execute_plan_parallel(self, guild: discord.Guild, plan: dict, ctx,
                                      confirm_destructive: bool = True,
                                      requester_name: str = "", requester_id: int = None,
                                      require_authorization: bool = True) -> list[StepResult]:
        """
        Execute a Discord management plan in parallel phases.

        SECURITY NOTE: This function can perform destructive operations.
        Authorization is REQUIRED for production safety.

        Args:
            guild: Discord guild to operate on
            plan: Plan dictionary with 'steps' list
            ctx: Discord context (channel or interaction)
            requester_name: Name of user requesting execution (for audit trail)
            requester_id: Discord user ID of requester (for authorization checks)
            require_authorization: If True, verify requester has necessary Discord permissions
        """
        steps = plan.get("steps", [])
        if not steps:
            await ctx.send("No steps in the plan. Nothing to do.")
            return []

        # CRITICAL: Authorization gate (same as execute_plan)
        if require_authorization:
            if not requester_id:
                await ctx.send("❌ **Authorization Required**: Cannot execute plan without requester identity.")
                logger.warning("[plan_tools] Blocked parallel plan execution - no requester_id provided")
                return []

            requester = guild.get_member(requester_id)
            if not requester:
                await ctx.send("❌ **Authorization Failed**: Requester not found in guild.")
                logger.warning(f"[plan_tools] Blocked parallel plan execution - requester {requester_id} not in guild")
                return []

            is_owner = guild.owner_id == requester_id
            is_admin = requester.guild_permissions.administrator

            if not (is_owner or is_admin):
                await ctx.send(f"❌ **Permission Denied**: {requester.mention} lacks administrator permissions.")
                logger.warning(f"[plan_tools] Blocked parallel plan execution - {requester_name} ({requester_id}) lacks admin rights")
                return []

            logger.info(f"[plan_tools] Parallel authorization passed: {requester_name} ({requester_id}) is {'owner' if is_owner else 'admin'}")

        preflight = await self.preflight_check(guild, {"steps": steps})
        if not preflight["can_execute"]:
            missing = ", ".join(preflight.get("missing", []))
            logger.warning("[plan_tools] Blocked parallel plan execution - bot permissions missing: %s", missing)
            await ctx.send(f"❌ **Bot Permissions Missing**: {missing}")
            return [StepResult(
                success=False, action="execute_plan_parallel", name="preflight_failed",
                error=f"Bot lacks required permissions: {missing}",
            )]

        # CRITICAL: Destructive action confirmation gate (same as execute_plan)
        if confirm_destructive:
            destructive_actions = ["delete_role", "delete_channel", "delete_category", "delete_webhook",
                                   "delete_scheduled_event", "kick", "ban", "unban", "delete_thread",
                                   "delete_server_template", "delete_automod_rule", "revoke_invite",
                                   "prune_members", "end_stage_instance"]
            has_destructive = any(step.get("action") in destructive_actions for step in steps)

            if has_destructive:
                destructive_steps = [f"- {step.get('action')}: {step.get('name', step.get('channel_name', 'unknown'))}"
                                     for step in steps if step.get("action") in destructive_actions]
                warning = (
                    f"⚠️ **DESTRUCTIVE ACTIONS DETECTED**\n\n"
                    f"This plan contains {len(destructive_steps)} destructive action(s):\n" +
                    "\n".join(destructive_steps[:5]) +
                    (f"\n...and {len(destructive_steps) - 5} more" if len(destructive_steps) > 5 else "") +
                    "\n\nReact ✅ to confirm or ❌ to cancel within 30 seconds."
                )
                warning_msg = await ctx.send(warning)
                await warning_msg.add_reaction("✅")
                await warning_msg.add_reaction("❌")

                chan = ctx.channel if hasattr(ctx, 'channel') else ctx
                author_id = requester_id

                def check_reaction(r, u):
                    return (u.id == author_id and r.message.id == warning_msg.id
                            and str(r.emoji) in ("✅", "❌"))

                def check_text(m):
                    return (m.author.id == author_id and m.channel.id == chan.id
                            and m.content.strip().lower() in ("yes", "y", "confirm", "approve", "no", "cancel"))

                try:
                    _tasks = [asyncio.create_task(self.bot.wait_for("reaction_add", check=check_reaction)),
                              asyncio.create_task(self.bot.wait_for("message", check=check_text))]
                    try:
                        done, pending = await asyncio.wait(_tasks, timeout=30, return_when=asyncio.FIRST_COMPLETED)
                    except TimeoutError:
                        done, pending = set(), set(_tasks)
                    for t in pending:
                        t.cancel()
                    if done:
                        result = done.pop().result()
                        if isinstance(result, tuple):
                            reaction, user = result
                            confirmed = str(reaction.emoji) == "✅"
                        else:
                            confirmed = result.content.strip().lower() in ("yes", "y", "confirm", "approve")
                    else:
                        confirmed = False
                except Exception:
                    confirmed = False

                if confirmed:
                    await ctx.send("✅ Confirmation received. Executing plan...")
                else:
                    await ctx.send("🚫 Plan cancelled.")
                    logger.info(f"[plan_tools] Parallel plan cancelled by {requester_name}")
                    return []

        steps = copy.deepcopy(plan.get("steps", []))

        for step in steps:
            params = step.pop("params", {})
            for k, v in params.items():
                if k not in step:
                    step[k] = v

        for step in steps:
            if step.get("action") == "create_role":
                name = step.get("name", "")
                if "with color" in name.lower():
                    m = re.match(r'^(.*?)\s+with\s+color\s+', name, re.IGNORECASE)
                    if m:
                        step["name"] = m.group(1).strip()
                        color_text = name[m.end():].strip()
                        if color_text and not step.get("color"):
                            step["color"] = color_text

        phases = [[], [], [], [], []]
        for step in steps:
            action = step.get("action", "")
            if action in ("create_role", "delete_role"):
                phases[0].append(step)
            elif action in ("create_category", "delete_category"):
                phases[1].append(step)
            elif action in ("create_channel", "edit_channel", "move_channel", "delete_channel"):
                phases[2].append(step)
            elif action in ("set_permissions", "clear_permissions", "sync_permissions"):
                phases[3].append(step)
            else:
                phases[4].append(step)

        total = len(steps)
        progress_msg = await self._send_progress_embed(ctx, plan, 0, total, [], " Starting parallel execution...")
        results = []

        for phase_idx, phase_steps in enumerate(phases):
            if not phase_steps:
                continue
            phase_names = ["roles", "categories", "channels", "permissions", "finalizing"]
            await self._update_progress_embed(progress_msg, plan, len(results), total, results,
                                               f" Executing {phase_names[phase_idx]}...")

            tasks = [self._execute_single_step(guild, step, confirm_destructive, origin_channel=ctx) for step in phase_steps]
            phase_results = await asyncio.gather(*tasks, return_exceptions=True)

            for step, result in zip(phase_steps, phase_results, strict=False):
                if isinstance(result, Exception):
                    result = StepResult(success=False, action=step.get("action", "unknown"),
                                         name=step.get("name", "unknown"), error=str(result))
                action = step.get("action", "unknown")
                name = step.get("name", step.get("channel", step.get("role", "unknown")))
                if self.tracker:
                    self.tracker.log_change(
                        guild_id=guild.id, guild_name=guild.name,
                        action=action, target={"name": name, "id": result.target_id},
                        before=result.before_state, after=result.after_state,
                        performed_by=requester_name, request_text=plan.get("analysis", ""),
                        success=result.success, error=result.error,
                    )
                results.append(result)

            await self._update_progress_embed(progress_msg, plan, len(results), total, results, None)

        await self._finalize_progress_embed(progress_msg, plan, results)

        followups = []
        if self.health:
            followups = self.health.suggest_followups(guild, plan.get("analysis", "completed tasks"))
        if followups:
            followup_text = "\n".join(f"  {f}" for f in followups)
            await ctx.send(f"**Suggestions:**\n{followup_text}")

        return results

    def _extract_step_name(self, step: dict) -> str:
        for key in ("name", "channel", "role", "category", "member", "nickname",
                     "channel_name", "webhook_name", "event_name", "role_name"):
            val = step.get(key)
            if val and val != "unknown":
                return str(val)
        return step.get("action", "unknown")

    async def _execute_single_step(self, guild: discord.Guild, step: dict, confirm_destructive: bool, origin_channel=None) -> StepResult:
        action = step.get("action", "unknown")
        name = self._extract_step_name(step)

        for attempt in range(self.MAX_RETRIES):
            try:
                result = await self._do_step(guild, step, confirm_destructive, origin_channel=origin_channel)
                if result.success or attempt == self.MAX_RETRIES - 1:
                    return result
                if self.repair and not result.success:
                    self.repair._log_error(action, guild.name, "StepFailure", result.error, "")
                await asyncio.sleep(self.RETRY_DELAY_BASE * (2 ** attempt))
            except Exception as e:
                if self.repair:
                    async def retry_step(**kwargs):
                        return await self._do_step(guild, step, confirm_destructive, origin_channel=origin_channel)
                    repair_result = await self.repair.safe_execute(
                        operation=retry_step,
                        operation_name=action,
                        guild=guild,
                        ctx=None,
                    )
                    if repair_result is not None:
                        return repair_result
                if attempt == self.MAX_RETRIES - 1:
                    return StepResult(success=False, action=action, name=name, error=str(e))
                await asyncio.sleep(self.RETRY_DELAY_BASE * (2 ** attempt))

        return StepResult(success=False, action=action, name=name, error="Max retries exceeded")

    async def _do_step(self, guild: discord.Guild, step: dict, confirm_destructive: bool, origin_channel=None) -> StepResult:
        action = step.get("action", "unknown")
        name = self._extract_step_name(step)

        action = self._ACTION_ALIASES.get(action, action)
        step["action"] = action

        # SAFETY: Never let the bot destroy the channel it's talking in
        if origin_channel and action in ("delete_channel", "move_channel", "edit_channel",
                                          "set_permissions", "clear_permissions", "sync_permissions"):
            # Collect every name this step might refer to
            step_names = set()
            step_names.add(name.lower().strip())
            for key in ("channel", "channel_name", "target"):
                val = step.get(key, "")
                if val:
                    step_names.add(val.lower().strip())
            # Compare against origin channel
            origin_name = getattr(origin_channel, "name", "").lower().strip()
            getattr(origin_channel, "id", 0)
            if origin_name in step_names:
                logger.warning("[plan_tools] SAFETY BLOCK: step targets origin channel '%s'", origin_name)
                return StepResult(
                    success=False, action=action, name=name,
                    error="Cannot modify the channel I'm currently talking in. Please use a different channel first.",
                )

        if action == "list_channels":
            channels = [c.name for c in guild.channels if c.type != 4]
            return StepResult(success=True, action=action, name=f"{len(channels)} channels",
                              after_state={"channels": channels})
        if action == "list_roles":
            roles = [r.name for r in guild.roles if not r.is_default()]
            return StepResult(success=True, action=action, name=f"{len(roles)} roles",
                              after_state={"roles": roles})
        if action == "create_role":
            if "with color" in name.lower():
                m = re.match(r'^(.*?)\s+with\s+color\s+', name, re.IGNORECASE)
                if m:
                    color_text = name[m.end():].strip()
                    name = m.group(1).strip()
                    if color_text and not step.get("color"):
                        step["color"] = color_text
            logger.info(f"[tools] _do_step create_role: name='{name}', color='{step.get('color')}'")
            return await self.create_role(guild, name=name, color=step.get("color"),
                                           permissions=step.get("permissions", []),
                                           hoist=step.get("hoist", False),
                                           mentionable=step.get("mentionable", False),
                                           position=step.get("position"))
        elif action == "edit_role":
            _valid = {"name", "color", "permissions", "hoist", "mentionable", "position"}
            kwargs = {k: v for k, v in step.items() if k in _valid}
            return await self.edit_role(guild, role_name=name, **kwargs)
        elif action == "delete_role":
            return await self.delete_role(guild, role_name=name)
        elif action == "assign_role":
            return await self.assign_role(guild, member_name_or_id=step.get("member", ""), role_name=step.get("role", name))
        elif action == "remove_role":
            return await self.remove_role(guild, member_name_or_id=step.get("member", ""), role_name=step.get("role", name))

        elif action == "create_category":
            return await self.create_category(guild, name=name, position=step.get("position"))
        elif action == "edit_category":
            _valid = {"name", "position"}
            kwargs = {k: v for k, v in step.items() if k in _valid}
            return await self.edit_category(guild, category_name=name, **kwargs)
        elif action == "delete_category":
            return await self.delete_category(guild, category_name=name)

        elif action == "create_channel":
            return await self.create_channel(guild, name=name, channel_type=step.get("type", "text"),
                                              category=step.get("category"), topic=step.get("topic"),
                                              slowmode=step.get("slowmode"), nsfw=step.get("nsfw", False),
                                              bitrate=step.get("bitrate"), user_limit=step.get("user_limit"))
        elif action == "edit_channel":
            _valid = {"topic", "category", "slowmode_delay", "nsfw", "name", "type"}
            kwargs = {k: v for k, v in step.items() if k in _valid}
            return await self.edit_channel(guild, channel_name=name, **kwargs)
        elif action == "delete_channel":
            ch = discord.utils.get(guild.channels, name=name)
            if not ch:
                return StepResult(success=False, action=action, name=name, error="Channel not found")
            return await self.delete_channel(ch, safe=confirm_destructive)
        elif action == "move_channel":
            cat_name = step.get("category", "")
            if not cat_name:
                return StepResult(success=False, action=action, name=name, error="Missing required 'category' field")
            return await self.move_channel(guild, channel_name=step.get("channel", name), category_name=cat_name)
        elif action == "sync_permissions":
            return await self.sync_channel_permissions(guild, channel_name=name)

        elif action == "set_permissions":
            ch = discord.utils.get(guild.channels, name=step.get("channel", ""))
            if not ch:
                return StepResult(success=False, action=action, name=step.get("channel", ""), error="Channel not found")
            # Convert boolean flags (send_messages=true) to allow/deny lists
            allow = list(step.get("allow", []))
            deny = list(step.get("deny", []))
            for key, val in step.items():
                if key in ("action", "channel", "role", "member", "target_type", "allow", "deny"):
                    continue
                sval = str(val).lower()
                if sval in ("true", "allow", "yes", "1"):
                    allow.append(key)
                elif sval in ("false", "deny", "no", "0"):
                    deny.append(key)
            return await self.set_channel_permissions(
                ch, target_name=step.get("role", step.get("member", "")),
                allow=allow, deny=deny,
                target_type=step.get("target_type", "role"),
            )
        elif action == "clear_permissions":
            ch = discord.utils.get(guild.channels, name=step.get("channel", ""))
            if not ch:
                return StepResult(success=False, action=action, name=step.get("channel", ""), error="Channel not found")
            return await self.clear_channel_permissions(
                ch, target_name=step.get("role", step.get("member", "")),
                target_type=step.get("target_type", "role"),
            )
        elif action == "purge_messages":
            ch = discord.utils.get(guild.text_channels, name=step.get("channel", ""))
            if not ch:
                return StepResult(success=False, action=action, name=step.get("channel", ""), error="Channel not found")
            return await self.purge_messages(ch, limit=step.get("limit", 100))

        elif action == "kick":
            return await self.kick_member(guild, member_name_or_id=step.get("member", name), reason=step.get("reason", "Azure"))
        elif action == "ban":
            return await self.ban_member(guild, member_name_or_id=step.get("member", name), reason=step.get("reason", "Azure"),
                                          delete_message_days=step.get("delete_message_days", 0))
        elif action == "unban":
            try:
                uid = int(step.get("user_id", 0))
            except (ValueError, TypeError):
                uid = 0
            if not uid:
                return StepResult(success=False, action=action, name=name, error="unban requires a valid user_id (integer)")
            return await self.unban_member(guild, user_id=uid, reason=step.get("reason", "Azure"))
        elif action == "timeout":
            return await self.timeout_member(guild, member_name_or_id=step.get("member", name),
                                              duration_minutes=step.get("duration", 60),
                                              reason=step.get("reason", "Azure"))
        elif action == "set_nickname":
            return await self.set_nickname(guild, member_name_or_id=step.get("member", name), nickname=step.get("nickname", name))
        elif action == "move_voice":
            return await self.move_member_to_voice(guild, member_name_or_id=step.get("member", name), channel_name=step.get("channel", ""))
        elif action == "deafen":
            return await self.deafen_member(guild, member_name_or_id=step.get("member", name), deafen=step.get("deafen", True))
        elif action == "mute":
            return await self.mute_member(guild, member_name_or_id=step.get("member", name), mute=step.get("mute", True))

        elif action == "create_webhook":
            return await self.create_webhook(guild, channel_name=step.get("channel", ""), webhook_name=step.get("webhook_name", name))
        elif action == "delete_webhook":
            return await self.delete_webhook(guild, webhook_name=name)

        elif action == "set_server_name":
            return await self.set_server_name(guild, name=step.get("name", name))
        elif action == "set_verification_level":
            return await self.set_verification_level(guild, level=step.get("level", "low"))
        elif action == "set_content_filter":
            return await self.set_content_filter(guild, filter_level=step.get("filter", "no_role"))
        elif action == "set_notifications":
            return await self.set_notifications(guild, level=step.get("level", "mentions_only"))
        elif action == "set_afk_channel":
            return await self.set_afk_channel(guild, channel_name=step.get("channel", name), timeout=step.get("timeout", 300))
        elif action == "set_system_channel":
            return await self.set_system_channel(guild, channel_name=step.get("channel", name))
        elif action == "set_rules_channel":
            return await self.set_rules_channel(guild, channel_name=step.get("channel", name))

        elif action == "create_scheduled_event":
            return await self.create_scheduled_event(
                guild, name=name, description=step.get("description", ""),
                start_time=step.get("start_time", ""), end_time=step.get("end_time"),
                location=step.get("location"), channel_name=step.get("channel_name"),
            )
        elif action == "delete_scheduled_event":
            return await self.delete_scheduled_event(guild, event_name=name)

        elif action == "create_invite":
            ch = discord.utils.get(guild.channels, name=step.get("channel", name))
            if not ch:
                return StepResult(success=False, action=action, name=name, error="Channel not found")
            return await self.create_invite(ch, max_age=step.get("max_age", 86400),
                                             max_uses=step.get("max_uses", 0),
                                             temporary=step.get("temporary", False))

        elif action == "pin_message":
            ch = discord.utils.get(guild.text_channels, name=step.get("channel", ""))
            if not ch:
                return StepResult(success=False, action=action, name=name, error="Channel not found")
            return await self.pin_message(ch, message_id=int(step.get("message_id", 0)))
        elif action == "unpin_message":
            ch = discord.utils.get(guild.text_channels, name=step.get("channel", ""))
            if not ch:
                return StepResult(success=False, action=action, name=name, error="Channel not found")
            return await self.unpin_message(ch, message_id=int(step.get("message_id", 0)))

        elif action == "create_thread":
            ch = discord.utils.get(guild.text_channels, name=step.get("channel", ""))
            if not ch:
                return StepResult(success=False, action=action, name=name, error="Channel not found")
            return await self.create_thread(ch, name=name, message_id=step.get("message_id"),
                                             thread_type=step.get("thread_type", "public"))
        elif action == "archive_thread":
            thread = discord.utils.get(guild.threads, name=name)
            if not thread:
                return StepResult(success=False, action=action, name=name, error="Thread not found")
            return await self.archive_thread(thread)

        elif action == "create_forum_channel":
            return await self.create_forum_channel(
                guild, name=name, topic=step.get("topic"),
                category=step.get("category"),
                default_sort_order=step.get("default_sort_order", 0),
                default_layout=step.get("default_layout", 1)
            )
        elif action == "create_forum_post":
            forum = discord.utils.get(guild.forums, name=step.get("forum", step.get("channel", "")))
            if not forum:
                return StepResult(success=False, action=action, name=name, error="Forum channel not found")
            return await self.create_forum_post(
                forum, title=name, content=step.get("content", ""),
                tags=step.get("tags", [])
            )
        elif action == "manage_forum_tags":
            forum = discord.utils.get(guild.forums, name=step.get("forum", step.get("channel", "")))
            if not forum:
                return StepResult(success=False, action=action, name=name, error="Forum channel not found")
            return await self.manage_forum_tags(
                forum, tag_name=name, emoji=step.get("emoji"),
                action_type=step.get("tag_action", "create")
            )

        elif action == "create_stage_channel":
            return await self.create_stage_channel(
                guild, name=name, topic=step.get("topic"),
                category=step.get("category"),
                bitrate=step.get("bitrate", 64000)
            )
        elif action == "start_stage_instance":
            stage = discord.utils.get(guild.stage_channels, name=step.get("stage", step.get("channel", "")))
            if not stage:
                return StepResult(success=False, action=action, name=name, error="Stage channel not found")
            return await self.start_stage_instance(
                stage, topic=name, privacy_level=step.get("privacy_level", 2)
            )
        elif action == "manage_stage_speaker":
            member = await self._resolve_member(guild, step.get("member", name))
            stage = discord.utils.get(guild.stage_channels, name=step.get("stage", step.get("channel", "")))
            if not member:
                return StepResult(success=False, action=action, name=name, error="Member not found")
            if not stage:
                return StepResult(success=False, action=action, name=name, error="Stage channel not found")
            return await self.manage_stage_speaker(
                member, stage, make_speaker=step.get("make_speaker", True)
            )

        elif action == "create_sticker":
            return await self.create_sticker(
                guild, name=name, description=step.get("description", ""),
                emoji=step.get("emoji", ""),
                file_path=step.get("file_path"),
                file_data=step.get("file_data")
            )
        elif action == "delete_sticker":
            return await self.delete_sticker(guild, sticker_name=name)
        elif action == "create_emoji":
            return await self.create_emoji(
                guild, name=name, image_data=step.get("image_data"),
                image_path=step.get("image_path"),
                roles=step.get("roles", [])
            )
        elif action == "delete_emoji":
            return await self.delete_emoji(guild, emoji_name=name)

        elif action == "create_automod_rule":
            return await self.create_automod_rule(
                guild, name=name, rule_type=step.get("rule_type", "keyword"),
                keywords=step.get("keywords", []),
                mention_limit=step.get("mention_limit"),
                actions=step.get("actions", ["block"])
            )
        elif action == "enable_spam_filter":
            return await self.enable_spam_filter(
                guild, mention_limit=step.get("mention_limit", 5)
            )
        elif action == "enable_keyword_filter":
            return await self.enable_keyword_filter(
                guild, blocked_words=step.get("keywords", step.get("blocked_words", []))
            )

        elif action == "get_audit_logs":
            return await self.get_audit_logs(
                guild, limit=step.get("limit", 50),
                action_type=step.get("action_type")
            )
        elif action == "find_who_did_action":
            return await self.find_who_did_action(
                guild, action_type=step.get("action_type", "channel_delete"),
                target_name=step.get("target")
            )

        elif action == "set_voice_bitrate":
            vc = discord.utils.get(guild.voice_channels, name=step.get("channel", name))
            if not vc:
                return StepResult(success=False, action=action, name=name, error="Voice channel not found")
            return await self.set_voice_bitrate(vc, bitrate_kbps=step.get("bitrate", 64))
        elif action == "set_voice_user_limit":
            vc = discord.utils.get(guild.voice_channels, name=step.get("channel", name))
            if not vc:
                return StepResult(success=False, action=action, name=name, error="Voice channel not found")
            return await self.set_voice_user_limit(vc, user_limit=step.get("user_limit", 0))
        elif action == "set_voice_region":
            vc = discord.utils.get(guild.voice_channels, name=step.get("channel", name))
            if not vc:
                return StepResult(success=False, action=action, name=name, error="Voice channel not found")
            return await self.set_voice_region(vc, region=step.get("region"))

        elif action == "set_welcome_screen":
            return await self.set_welcome_screen(
                guild, description=step.get("description", "Welcome!"),
                welcome_channels=step.get("channels", [])
            )
        elif action == "create_server_template":
            return await self.create_server_template(
                guild, name=name, description=step.get("description")
            )
        elif action == "sync_server_template":
            return await self.sync_server_template(
                guild, template_code=step.get("template_code", name)
            )

        elif action == "follow_channel":
            ch = discord.utils.get(guild.text_channels, name=step.get("channel", ""))
            if not ch:
                return StepResult(success=False, action=action, name=name, error="Channel not found")
            return await self.follow_channel(ch, target_channel_id=int(step.get("target_channel_id", 0)))
        elif action == "crosspost_message":
            ch = discord.utils.get(guild.text_channels, name=step.get("channel", ""))
            if not ch:
                return StepResult(success=False, action=action, name=name, error="Channel not found")
            return await self.crosspost_message(ch, message_id=int(step.get("message_id", 0)))
        elif action == "set_forum_require_tag":
            forum = discord.utils.get(guild.forums, name=step.get("channel", name))
            if not forum:
                return StepResult(success=False, action=action, name=name, error="Forum channel not found")
            return await self.set_forum_require_tag(forum, require_tag=step.get("require_tag", True))
        elif action == "set_forum_default_reaction":
            forum = discord.utils.get(guild.forums, name=step.get("channel", name))
            if not forum:
                return StepResult(success=False, action=action, name=name, error="Forum channel not found")
            return await self.set_forum_default_reaction(forum, emoji=step.get("emoji"))
        elif action == "set_forum_default_slowmode":
            forum = discord.utils.get(guild.forums, name=step.get("channel", name))
            if not forum:
                return StepResult(success=False, action=action, name=name, error="Forum channel not found")
            return await self.set_forum_default_slowmode(forum, slowmode_seconds=step.get("slowmode", 0))
        elif action == "disconnect_voice":
            member = await self._resolve_member(guild, step.get("member", name))
            if not member:
                return StepResult(success=False, action=action, name=name, error="Member not found")
            return await self.disconnect_voice(member)
        elif action == "get_channel_invites":
            ch = discord.utils.get(guild.channels, name=step.get("channel", name))
            if not ch:
                return StepResult(success=False, action=action, name=name, error="Channel not found")
            return await self.get_channel_invites(ch)
        elif action == "get_guild_invites":
            return await self.get_guild_invites(guild)
        elif action == "revoke_invite":
            return await self.revoke_invite(guild, code=step.get("code", name))
        elif action == "get_pinned_messages":
            ch = discord.utils.get(guild.text_channels, name=step.get("channel", ""))
            if not ch:
                return StepResult(success=False, action=action, name=name, error="Channel not found")
            return await self.get_pinned_messages(ch)

        elif action == "delete_thread":
            thread = discord.utils.get(guild.threads, name=name) or discord.utils.get(guild.channels, name=name)
            if not thread:
                return StepResult(success=False, action=action, name=name, error="Thread not found")
            return await self.delete_thread(thread)
        elif action == "rename_thread":
            thread = discord.utils.get(guild.threads, name=name)
            if not thread:
                return StepResult(success=False, action=action, name=name, error="Thread not found")
            return await self.rename_thread(thread, name=step.get("new_name", name))
        elif action == "set_thread_auto_archive":
            thread = discord.utils.get(guild.threads, name=step.get("thread", name))
            if not thread:
                return StepResult(success=False, action=action, name=name, error="Thread not found")
            return await self.set_thread_auto_archive(thread, duration_minutes=step.get("duration", 1440))
        elif action == "set_thread_slowmode":
            thread = discord.utils.get(guild.threads, name=step.get("thread", name))
            if not thread:
                return StepResult(success=False, action=action, name=name, error="Thread not found")
            return await self.set_thread_slowmode(thread, slowmode_seconds=step.get("slowmode", 0))
        elif action == "join_thread":
            thread = discord.utils.get(guild.threads, name=name)
            if not thread:
                return StepResult(success=False, action=action, name=name, error="Thread not found")
            return await self.join_thread(thread)
        elif action == "leave_thread":
            thread = discord.utils.get(guild.threads, name=name)
            if not thread:
                return StepResult(success=False, action=action, name=name, error="Thread not found")
            return await self.leave_thread(thread)
        elif action == "add_thread_member":
            thread = discord.utils.get(guild.threads, name=step.get("thread", name))
            member = await self._resolve_member(guild, step.get("member", ""))
            if not thread:
                return StepResult(success=False, action=action, name=name, error="Thread not found")
            if not member:
                return StepResult(success=False, action=action, name=name, error="Member not found")
            return await self.add_thread_member(thread, member)
        elif action == "remove_thread_member":
            thread = discord.utils.get(guild.threads, name=step.get("thread", name))
            member = await self._resolve_member(guild, step.get("member", ""))
            if not thread:
                return StepResult(success=False, action=action, name=name, error="Thread not found")
            if not member:
                return StepResult(success=False, action=action, name=name, error="Member not found")
            return await self.remove_thread_member(thread, member)
        elif action == "list_archived_threads":
            return await self.list_archived_threads(guild, public=step.get("public", True), limit=step.get("limit", 50))
        elif action == "clone_channel":
            return await self.clone_channel(guild, channel_name=step.get("channel", name), name=step.get("name"))

        elif action == "set_server_icon":
            return await self.set_server_icon(guild, image_path=step.get("image_path"), image_data=step.get("image_data"))
        elif action == "set_server_banner":
            return await self.set_server_banner(guild, image_path=step.get("image_path"), image_data=step.get("image_data"))
        elif action == "set_server_splash":
            return await self.set_server_splash(guild, image_path=step.get("image_path"), image_data=step.get("image_data"))
        elif action == "set_server_description":
            return await self.set_server_description(guild, description=step.get("description", ""))
        elif action == "set_public_updates_channel":
            return await self.set_public_updates_channel(guild, channel_name=step.get("channel", name))
        elif action == "set_mfa_level":
            return await self.set_mfa_level(guild, required=step.get("required", True))
        elif action == "set_preferred_locale":
            return await self.set_preferred_locale(guild, locale=step.get("locale", "en-US"))
        elif action == "set_vanity_url":
            return await self.set_vanity_url(guild, code=step.get("code", name))
        elif action == "get_vanity_url":
            return await self.get_vanity_url(guild)
        elif action == "get_ban_list":
            return await self.get_ban_list(guild, limit=step.get("limit", 100))
        elif action == "estimate_prune_members":
            return await self.estimate_prune_members(guild, days=step.get("days", 30), roles=step.get("roles"))
        elif action == "prune_members":
            return await self.prune_members(guild, days=step.get("days", 30), roles=step.get("roles"))

        elif action == "get_automod_rules":
            return await self.get_automod_rules(guild)
        elif action == "edit_automod_rule":
            return await self.edit_automod_rule(guild, rule_name=name, name=step.get("new_name"),
                                                 enabled=step.get("enabled"), actions=step.get("actions"))
        elif action == "delete_automod_rule":
            return await self.delete_automod_rule(guild, rule_name=name)

        elif action == "edit_scheduled_event":
            return await self.edit_scheduled_event(guild, event_name=name, name=step.get("new_name"),
                                                    description=step.get("description"),
                                                    start_time=step.get("start_time"), end_time=step.get("end_time"),
                                                    location=step.get("location"), channel_name=step.get("channel_name"))

        elif action == "edit_emoji":
            return await self.edit_emoji(guild, emoji_name=name, name=step.get("new_name"), roles=step.get("roles"))
        elif action == "edit_sticker":
            return await self.edit_sticker(guild, sticker_name=name, name=step.get("new_name"),
                                            description=step.get("description"), emoji=step.get("emoji"))

        elif action == "edit_webhook":
            return await self.edit_webhook(guild, webhook_name=name, name=step.get("new_name"),
                                            channel_name=step.get("channel"))
        elif action == "get_channel_webhooks":
            return await self.get_channel_webhooks(guild, channel_name=step.get("channel", name))
        elif action == "get_guild_webhooks":
            return await self.get_guild_webhooks(guild)

        elif action == "noop":
            return StepResult(success=True, action="noop", name="noop", detail="No action needed")

        elif action == "delete_server_template":
            return await self.delete_server_template(guild, template_code=step.get("template_code", name))
        elif action == "edit_server_template":
            return await self.edit_server_template(guild, template_code=step.get("template_code", name),
                                                    name=step.get("new_name"), description=step.get("description"))
        elif action == "get_guild_templates":
            return await self.get_guild_templates(guild)

        elif action == "end_stage_instance":
            stage = discord.utils.get(guild.stage_channels, name=step.get("stage", step.get("channel", name)))
            if not stage:
                return StepResult(success=False, action=action, name=name, error="Stage channel not found")
            return await self.end_stage_instance(stage)
        elif action == "edit_stage_instance_topic":
            stage = discord.utils.get(guild.stage_channels, name=step.get("stage", step.get("channel", name)))
            if not stage:
                return StepResult(success=False, action=action, name=name, error="Stage channel not found")
            return await self.edit_stage_instance_topic(stage, topic=step.get("topic", name))

        elif action == "get_onboarding":
            return await self.get_onboarding(guild)
        elif action == "edit_onboarding":
            return await self.edit_onboarding(guild, enabled=step.get("enabled"),
                                               default_channels=step.get("default_channels"),
                                               prompts=step.get("prompts"))
        elif action == "enable_community_mode":
            return await self.enable_community_mode(guild, rules_channel=step.get("rules_channel", ""),
                                                     public_updates_channel=step.get("public_updates_channel", ""),
                                                     system_channel=step.get("system_channel"),
                                                     description=step.get("description"))
        elif action == "set_widget":
            return await self.set_widget(guild, enabled=step.get("enabled", True),
                                          channel_name=step.get("channel"))
        elif action == "get_widget":
            return await self.get_widget(guild)

        else:
            logger.warning("[plan_tools] UNKNOWN action '%s' (aliases tried: %s)", step.get("action", "?"), action)
            return StepResult(success=False, action=action, name=name, error=f"Unknown action: {action}")

    async def preflight_check(self, guild: discord.Guild, plan: dict) -> dict:
        if not self.bot:
            return {"can_execute": False, "missing": ["Bot not configured"], "warnings": []}
        bot_member = guild.get_member(self.bot.user.id) or getattr(guild, "me", None)
        if not bot_member:
            return {"can_execute": False, "missing": ["Bot not in guild?"], "warnings": []}

        perms = bot_member.guild_permissions
        is_admin = bool(getattr(perms, "administrator", False))

        def can(permission):
            return is_admin or bool(getattr(perms, permission, False))

        missing = []
        warnings = []
        steps = plan.get("steps", [])

        for step in steps:
            action = step.get("action", "")
            if action in ("create_role", "delete_role", "edit_role") and not can("manage_roles"):
                missing.append("manage_roles (for role operations)")
            if action in ("create_channel", "delete_channel", "edit_channel", "move_channel", "clone_channel") and not can("manage_channels"):
                missing.append("manage_channels (for channel operations)")
            if action in ("create_category", "delete_category") and not can("manage_channels"):
                missing.append("manage_channels (for category operations)")
            if action in ("set_permissions", "clear_permissions") and not can("manage_channels"):
                missing.append("manage_channels (for permission operations)")
            if action in ("kick", "ban", "unban", "timeout") and not can("kick_members") and not can("ban_members"):
                missing.append("kick_members or ban_members (for member moderation)")
            if action == "purge_messages" and not can("manage_messages"):
                missing.append("manage_messages (for purge_messages)")
            if action in ("set_server_name", "set_verification_level", "set_content_filter", "set_notifications",
                          "set_server_icon", "set_server_banner", "set_server_splash", "set_server_description",
                          "set_public_updates_channel", "set_mfa_level", "set_preferred_locale", "set_vanity_url",
                          "enable_community_mode", "set_widget", "edit_onboarding") and not can("manage_guild"):
                missing.append("manage_guild (for server settings)")
            if action in ("create_webhook", "delete_webhook", "edit_webhook", "get_channel_webhooks", "get_guild_webhooks") and not can("manage_webhooks"):
                missing.append("manage_webhooks (for webhook operations)")
            if action in ("create_scheduled_event", "delete_scheduled_event", "edit_scheduled_event") and not can("manage_events"):
                missing.append("manage_events (for scheduled events)")
            if action in ("create_emoji", "delete_emoji", "edit_emoji") and not can("manage_emojis_and_stickers"):
                missing.append("manage_emojis_and_stickers")
            if action in ("create_sticker", "delete_sticker", "edit_sticker") and not can("manage_emojis_and_stickers"):
                missing.append("manage_emojis_and_stickers")
            if action in ("prune_members", "get_ban_list", "get_audit_logs") and not can("ban_members"):
                missing.append("ban_members (for moderation queries)")
            if action in ("disconnect_voice", "move_voice", "deafen", "mute") and not can("move_members"):
                missing.append("move_members (for voice moderation)")
            if action in ("create_automod_rule", "edit_automod_rule", "delete_automod_rule", "get_automod_rules") and not can("manage_guild"):
                missing.append("manage_guild (for auto-mod rules)")
            if action in ("create_server_template", "delete_server_template", "edit_server_template", "sync_server_template", "get_guild_templates") and not can("manage_guild"):
                missing.append("manage_guild (for server templates)")

        destructive = ["delete_role", "delete_channel", "delete_category", "delete_webhook", "delete_scheduled_event",
                       "kick", "ban", "unban", "delete_emoji", "delete_sticker", "delete_thread",
                       "delete_server_template", "delete_automod_rule", "revoke_invite", "prune_members", "end_stage_instance"]
        has_destructive = any(s.get("action", "") in destructive for s in steps)
        if has_destructive:
            warnings.append("This plan contains destructive actions (deletions, bans). A confirmation will be required.")

        if len(steps) > 10:
            warnings.append(f"Large plan ({len(steps)} steps). This may take a while and could hit rate limits.")

        return {
            "can_execute": len(missing) == 0,
            "missing": list(set(missing)),
            "warnings": warnings,
            "has_destructive": has_destructive,
        }

    async def undo_last(self, guild: discord.Guild, ctx, n: int = 1) -> list[StepResult]:
        if not self.tracker:
            return []
        results = []
        for _ in range(n):
            undo = self.tracker.get_undo(guild.id)
            if not undo:
                break
            result = await self._execute_single_undo(guild, undo)
            results.append(result)
            await asyncio.sleep(0.5)

        if not results:
            await ctx.send(" Nothing to undo.")
        else:
            summary = "\n".join(f"{'✅' if r.success else '❌'} {r.action}: {r.name}" for r in results)
            await ctx.send(f"**Undo Results:**\n{summary}")
        return results

    async def _execute_single_undo(self, guild: discord.Guild, undo: dict) -> StepResult:
        action = undo.get("action")
        target = undo.get("target", {})
        name = target.get("name", "unknown")

        if action == "delete_role":
            return await self.delete_role(guild, role_name=name)
        elif action == "delete_channel":
            ch = discord.utils.get(guild.channels, name=name)
            if ch:
                return await self.delete_channel(ch, safe=False)
            return StepResult(success=False, action="undo_delete_channel", name=name, error="Channel not found")
        elif action == "delete_category":
            return await self.delete_category(guild, category_name=name)
        elif action == "delete_webhook":
            return await self.delete_webhook(guild, webhook_name=name)
        elif action == "delete_scheduled_event":
            return await self.delete_scheduled_event(guild, event_name=name)
        elif action == "create_role":
            before = undo.get("before", {})
            return await self.create_role(
                guild, name=name, color=before.get("color"),
                permissions=before.get("permissions", []),
            )
        elif action == "create_channel":
            before = undo.get("before", {})
            return await self.create_channel(guild, name=name, channel_type=before.get("type", "text"))
        elif action == "restore_permissions":
            before = undo.get("before", {})
            ch = discord.utils.get(guild.channels, name=target.get("channel", name))
            if ch and before:
                return await self.set_channel_permissions(
                    ch, target_name=target.get("target", ""),
                    allow=[k for k, v in before.items() if v is True],
                    deny=[k for k, v in before.items() if v is False],
                )
            return StepResult(success=False, action="undo_permissions", name=name, error="Could not restore")
        elif action == "restore_nickname":
            before = undo.get("before", {})
            return await self.set_nickname(guild, member_name_or_id=target.get("member", name),
                                              nickname=before.get("nickname", ""))
        else:
            return StepResult(success=False, action="undo", name=name, error=f"Cannot undo {action}")
