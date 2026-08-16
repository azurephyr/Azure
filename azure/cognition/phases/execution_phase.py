"""Tool execution phase — validate, dispatch, retry, and self-heal tool calls."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from functools import partial

from ..cognitive_state import CognitiveState

logger = logging.getLogger(__name__)


class ExecutionPhaseMixin:
    """Mixin for tool execution, validation, and dispatch."""

    async def _execute(
        self,
        state: CognitiveState,
        params: dict,
        is_admin: bool,
        has_guild: bool,
    ) -> tuple[str, bool]:
        """Phase 4/9: Execute tools or generate response."""
        if state.needs_confirmation and not state.confirmation_message:
            state.confirmation_message = (
                f"\u26a0\ufe0f **This is a {state.risk.value} action.** "
                "Please type `yes` to confirm or `no` to cancel."
            )
        if state.needs_confirmation and not state.execution_result:
            return state.confirmation_message, True

        if state.selected_tools and self.agent is not None:
            tool_results = []
            held_actions = []
            tools_list = self.agent.tools.describe()
            self.tier_dispatcher.tool_registry = {
                t["name"]: partial(self.agent.tools.call, t["name"])
                for t in tools_list
            }

            role_ctx = getattr(state, 'role_context', None)
            if role_ctx is not None:
                from ..role_context import RoleGate
                gate_denials = []
                for tool_name in state.selected_tools:
                    allowed, reason = RoleGate.check(tool_name, role_ctx)
                    if not allowed:
                        gate_denials.append(reason)
                if gate_denials:
                    denial_msg = "\n\n".join(gate_denials)
                    return (
                        f"{denial_msg}\n\n"
                        f"**Your permission tier:** `{role_ctx.tier.value}`\n"
                        f"**Your roles:** {', '.join(role_ctx.role_names[:5]) or 'None'}\n\n"
                        f"Ask a server admin to authorize these actions.",
                        False,
                    )

            for tool_name in state.selected_tools:
                reasoner_tool_args = getattr(state, '_reasoner_tool_args', {})
                base_args = {k: v for k, v in params.items()
                             if k in ("member", "member_id", "reason", "role",
                                      "name", "count", "duration")}
                if tool_name in ("web_search", "execute_python"):
                    tool_args = reasoner_tool_args if reasoner_tool_args else base_args
                else:
                    tool_args = {**base_args, **reasoner_tool_args}
                valid, err = self._validate_tool(tool_name, tool_args, is_admin, has_guild)
                if not valid:
                    return f"\u274c Tool validation failed: {err}", False

                tier = self.tier_dispatcher.classify(tool_name)
                if tier.value == "WRITE_DESTRUCTIVE":
                    disp = self.tier_dispatcher._hold_for_confirmation(
                        tool_name, tool_args, state.user_name, None
                    )
                    held_actions.append(disp)
                    tool_results.append(
                        f"\u23f8\ufe0f {tool_name}: HELD for confirmation (ID: {disp.confirmation_id})"
                    )
                else:
                    attempts = 0
                    max_attempts = 3

                    while attempts < max_attempts:
                        _current_args = dict(tool_args)
                        result = await asyncio.get_running_loop().run_in_executor(None, lambda tn=tool_name, ta=_current_args: self.agent.tools.call(tn, **ta))
                        if result.get("ok"):
                            if attempts == 0:
                                tool_results.append(f"\u2705 {tool_name}: {result.get('result', 'done')}")
                            else:
                                tool_results.append(f"\u2705 {tool_name}: {result.get('result', 'done')} (Self-Healed after {attempts} retries)")
                            break
                        else:
                            error_msg = result.get('error', 'unknown error')
                            attempts += 1
                            if attempts >= max_attempts:
                                tool_results.append(f"\u274c {tool_name} failed after {max_attempts} attempts: {error_msg}")
                                break

                            if self.reasoner and self.reasoner.llm:
                                logger.info(f"[{tool_name}] Failed: {error_msg}. Attempting self-heal (attempt {attempts})...")
                                fix_prompt = (
                                    f"You tried to run the tool '{tool_name}' with these arguments:\n"
                                    f"{json.dumps(tool_args, indent=2)}\n\n"
                                    f"But it failed with this error:\n"
                                    f"{error_msg}\n\n"
                                    f"Please fix the arguments based on the error. Return ONLY a JSON object containing the new arguments for '{tool_name}'. Do not include markdown formatting or explanations."
                                )
                                try:
                                    fix_raw = self.reasoner.llm.chat([{"role": "user", "content": fix_prompt}], max_tokens=150, temperature=0.2)
                                    code_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', fix_raw, re.DOTALL)
                                    if code_block:
                                        fix_raw = code_block.group(1)
                                    start = fix_raw.find("{")
                                    end = fix_raw.rfind("}")
                                    if start != -1 and end != -1:
                                        fix_raw = fix_raw[start:end+1]
                                    new_args = json.loads(fix_raw)
                                    if isinstance(new_args, dict):
                                        tool_args = new_args
                                        logger.info(f"[{tool_name}] Self-healed params to: {tool_args}")
                                    else:
                                        logger.warning(f"[{tool_name}] Self-heal returned invalid JSON format: {fix_raw}")
                                except Exception as e:
                                    logger.error(f"[{tool_name}] Self-heal LLM call failed: {e}")
                            else:
                                tool_results.append(f"\u274c {tool_name} failed: {error_msg}")
                                break

            if held_actions:
                hold_msg = "\n\n".join([
                    f"\u26a0\ufe0f **Action held for confirmation:**\n"
                    f"Tool: `{h.tool_name}`\n"
                    f"Reply `confirm {h.confirmation_id}` to proceed, or `cancel {h.confirmation_id}` to abort."
                    for h in held_actions
                ])
                state.confirmation_message = hold_msg
                state.confirmation_required = True

            return "\n".join(tool_results), True

        if state.plan and state.plan.execution_order:
            if state.plan.requires_confirmation:
                plan_text = self.planning.format_plan(state.plan)
                return (
                    f"\U0001f4cb **Execution Plan Drafted**\n\n{plan_text}\n\n[NEEDS_CONFIRMATION_VIEW]",
                    True,
                )
            else:
                plan_results = ["\U0001f680 **Executing Plan...**"]
                all_success = True

                for step in state.plan.execution_order:
                    if not step.tool or step.tool == "none" or step.tool.lower() == "none":
                        plan_results.append(f"\u2705 Step {step.order}: {step.action} (No tool required)")
                        continue

                    base_args = {k: v for k, v in params.items()
                                 if k in ("member", "member_id", "reason", "role",
                                          "name", "count", "duration")}
                    tool_args = {**base_args, **(step.args or {})}

                    valid, err = self._validate_tool(step.tool, tool_args, is_admin, has_guild)
                    if not valid:
                        plan_results.append(f"\u274c Step {step.order} failed: Invalid tool ({err})")
                        if not step.can_fail:
                            plan_results.append("\u26a0\ufe0f **Execution aborted:** Critical step failed.")
                            all_success = False
                            break
                        continue

                    _plan_args = dict(tool_args)
                    result = await asyncio.get_running_loop().run_in_executor(None, lambda st=step, pa=_plan_args: self.agent.tools.call(st.tool, **pa))
                    if result.get("ok"):
                        plan_results.append(f"\u2705 Step {step.order}: {result.get('result', 'done')}")
                    else:
                        plan_results.append(f"\u274c Step {step.order} failed: {result.get('error')}")
                        if not step.can_fail:
                            plan_results.append("\u26a0\ufe0f **Execution aborted:** Critical step failed.")
                            all_success = False
                            break

                return "\n".join(plan_results), all_success

        if state.response:
            return state.response, True

        if self.agent is not None and state.needs_llm:
            try:
                server_name = state.context.split("|")[0].strip() or "Discord"
                reply = await self.agent.handle(
                    user=state.user_name,
                    message=state.raw_message,
                    server_name=server_name,
                )
                return reply, True
            except Exception as e:
                logger.error(f"[cognitive_pipeline] LLM execution error: {e}")
                return f"Sorry, I had trouble thinking that through. ({e})", False

        return self._generate_fallback_response(state), True

    def _validate_tool(self, tool_name: str, args: dict, is_admin: bool, has_guild: bool) -> tuple[bool, str]:
        """Validate a tool call."""
        if self.agent is None:
            return False, "Agent not initialized"
        tool_map = {t["name"]: t for t in self.agent.tools.describe()}
        spec = tool_map.get(tool_name)
        if not spec:
            return False, f"Unknown tool: {tool_name}"
        admin_tools = ("kick_member", "ban_member", "timeout_member", "assign_role",
                       "create_channel", "create_role", "save_template", "load_template",
                       "set_permissions")
        if tool_name in admin_tools:
            if not is_admin:
                return False, f"Tool '{tool_name}' requires admin permissions"
            if not has_guild:
                return False, f"Tool '{tool_name}' requires being in a server"
        return True, ""
