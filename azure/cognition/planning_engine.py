"""
PlanningEngine — Phase 8 of the cognitive pipeline.

Builds step-by-step execution plans for HIGH / EXTREME complexity tasks.
Uses the LLM to dynamically generate plans rather than relying on static templates.

Plan structure:
  - objective: what we're trying to achieve
  - constraints: limitations on the plan
  - dependencies: what must happen first
  - risks: things that could go wrong
  - execution_order: ordered list of steps
  - fallback_paths: what to do if a step fails
"""

from __future__ import annotations

import json
import logging
import re

from .cognitive_state import (
    CognitiveState,
    Complexity,
    ExecutionPlan,
    PlanStep,
)

logger = logging.getLogger(__name__)

class PlanningEngine:
    """
    Builds structured execution plans using the LLM.

    Produces an ExecutionPlan with ordered steps, risks, and fallback paths.
    """

    def __init__(self, llm=None):
        self.llm = llm

    def plan(
        self,
        state: CognitiveState,
        params: dict | None = None,
    ) -> ExecutionPlan:
        """
        Build an execution plan based on the cognitive state using the LLM.
        """
        raw = state.raw_message.strip()
        params = params or {}
        plan = ExecutionPlan()

        if not self.llm:
            # Fallback if no LLM
            plan.objective = "Execute task (Fallback - No LLM available)"
            plan.execution_order.append(PlanStep(
                order=1,
                action="Execute command",
                description="Unable to generate dynamic plan without LLM",
                risk="HIGH",
                can_fail=True,
                fallback="Manual intervention required"
            ))
            plan.requires_confirmation = True
            return plan

        prompt = (
            f"Generate a step-by-step execution plan for this request: '{raw}'\n"
            f"Context: {state.context_summary}\n\n"
            f"AVAILABLE TOOLS:\n"
            f"- create_category(name, position)\n"
            f"- create_channel(name, type, category, topic, slowmode, bitrate, user_limit)\n"
            f"- create_role(name, permissions=[...], color, hoist, mentionable) — Valid perms: administrator, kick_members, ban_members, mute_members, manage_messages, manage_channels, manage_roles, read_messages, send_messages, embed_links, attach_files, add_reactions, connect, speak, read_message_history, use_voice_activity, move_members, deafen_members, mention_everyone\n"
            f"- set_permissions(channel, role, allow=[...], deny=[...])\n"
            f"- set_server_settings, set_server_meta, set_onboarding, web_search, execute_python\n\n"
            f"RULES — FOLLOW STRICTLY:\n"
            f"1. Generate 60-120 steps. Quality over quantity.\n"
            f"2. Create 5-7 categories with 4-5 channels each. Create 12-16 roles.\n"
            f"3. EVERY channel must have 2-3 set_permissions steps targeting different roles. No channel gets zero permissions.\n"
            f"4. Permissions should be 50-65% of all steps.\n"
            f"5. Focus on ROLE diversity and rich permission hierarchies. Create roles with distinct, meaningful permission sets — not just read_messages/send_messages for everyone.\n"
            f"6. SET_PERMISSIONS example — a staff channel: Member deny read, Moderator allow, Admin allow. A dev channel: Member deny read, Junior Dev allow, Senior Dev allow, Head Dev allow. Mix allow and deny per channel.\n"
            f"7. Every create_role must include specific permissions (not empty). Every create_channel must include: name, type, category, topic.\n"
            f"8. Use varied Discord perm strings: read_messages, send_messages, embed_links, attach_files, add_reactions, connect, speak, mute_members, deafen_members, move_members, use_voice_activity, priority_speaker, manage_messages, manage_channels, manage_roles, kick_members, ban_members, mention_everyone\n"
            f"9. Order: set_server_meta then set_server_settings, then roles (with perms), then categories with channels and perms inline.\n"
            f"10. MUST include set_server_meta (name + description) and set_server_settings (verification_level + content_filter).\n"
            f"11. NEVER create @everyone role.\n"
            f"12. Risk: HIGH for destructive, MEDIUM for perms, LOW for creation.\n\n"
            f"Respond with ONLY JSON:\n"
            f"{{\"objective\":\"...\",\"constraints\":[],\"dependencies\":[],\"risks\":[],\"fallback_paths\":[],\"steps\":[{{\"action\":\"...\",\"description\":\"...\",\"tool\":\"...\",\"args\":{{}},\"risk\":\"LOW\",\"can_fail\":false,\"fallback\":\"\"}}]}}"
        )

        try:
            raw_response = self.llm.chat([{"role": "system", "content": "You output only valid JSON."}, {"role": "user", "content": prompt}], max_tokens=32768, temperature=0.2)

            # Extract JSON block (strip markdown fences)
            code_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw_response, re.DOTALL)
            if code_block:
                raw_response = code_block.group(1)
            else:
                start = raw_response.find("{")
                end = raw_response.rfind("}")
                if start != -1 and end != -1:
                    raw_response = raw_response[start:end+1]

            # Repair common LLM JSON errors before parsing
            raw_response = re.sub(r',\s*}', '}', raw_response)
            raw_response = re.sub(r',\s*]', ']', raw_response)

            # Try multiple repair strategies if json.loads fails
            try:
                data = json.loads(raw_response)
            except json.JSONDecodeError:
                # Strategy 1: unquote single-quoted keys
                fixed = re.sub(r"(?<=[{,])\s*'(\w+)'\s*:", r'"\1":', raw_response)
                try:
                    data = json.loads(fixed)
                except json.JSONDecodeError:
                    # Strategy 2: add missing commas between object entries
                    fixed = re.sub(r'}\s*{', '},{', fixed)
                    fixed = re.sub(r'}\s*]', '}]', fixed)
                    fixed = re.sub(r'"\s+"', '", "', fixed)
                    fixed = re.sub(r'(?<=[\[,])\s*"', '"', fixed)
                    try:
                        data = json.loads(fixed)
                    except json.JSONDecodeError as final_err:
                        logger.error(f"[planning_engine] JSON repair failed: {final_err}")
                        logger.error(f"[planning_engine] Raw response (first 2000 chars): {raw_response[:2000]}")
                        raise

            plan.objective = data.get("objective", "Execute requested operation")
            plan.constraints = data.get("constraints", [])
            plan.dependencies = data.get("dependencies", [])
            plan.risks = data.get("risks", [])
            plan.fallback_paths = data.get("fallback_paths", [])

            steps_data = data.get("steps", [])
            for i, step_dict in enumerate(steps_data, start=1):
                plan.execution_order.append(PlanStep(
                    order=i,
                    action=step_dict.get("action", f"Step {i}"),
                    description=step_dict.get("description", ""),
                    tool=step_dict.get("tool"),
                    args=step_dict.get("args", {}),
                    risk=step_dict.get("risk", "LOW"),
                    can_fail=step_dict.get("can_fail", False),
                    fallback=step_dict.get("fallback"),
                ))

        except Exception as e:
            logger.error(f"[planning_engine] LLM plan generation failed: {e}")

        # Verify complex requirements
        if state.complexity in (Complexity.HIGH, Complexity.EXTREME) and len(plan.execution_order) > 1:
            plan.execution_order.insert(0, PlanStep(
                order=0,
                action="Confirm with user",
                description="Confirm action plan with user before executing",
                risk="LOW",
                can_fail=False
            ))

        plan.requires_confirmation = any(
            s.risk in ("HIGH", "CRITICAL") for s in plan.execution_order
        )

        return plan

    def format_plan(self, plan: ExecutionPlan) -> str:
        """Format a plan as a readable string for display."""
        lines = []
        lines.append(f"**Objective:** {plan.objective}")

        if plan.constraints:
            lines.append(f"**Constraints:** {', '.join(plan.constraints)}")

        lines.append("**Plan:**")
        for step in plan.execution_order:
            risk_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}.get(step.risk, "⚪")
            fail_note = f" *(may fail: {step.fallback})*" if step.can_fail and step.fallback else ""
            lines.append(f"  {risk_icon} Step {step.order}: {step.description}{fail_note}")

        if plan.risks:
            lines.append("**Risks:**")
            for r in plan.risks:
                lines.append(f"  ⚠️ {r}")

        if plan.fallback_paths:
            lines.append("**If something fails:**")
            for f in plan.fallback_paths:
                lines.append(f"  → {f}")

        return "\n".join(lines)
