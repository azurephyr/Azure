"""
ToolDecisionEngine — Phase 7 of the cognitive pipeline.

Decides whether to:
  - Respond directly (no tools)
  - Call a single tool
  - Call multiple tools
  - Ask for clarification

Also validates tool arguments before execution.
"""

from __future__ import annotations

from .cognitive_state import Complexity, Mode, ToolDecision

# ---------------------------------------------------------------------------
# Tool registry (lightweight, no Discord dependency)
# ---------------------------------------------------------------------------

class ToolSpec:
    """Specification for a single available tool."""
    def __init__(
        self,
        name: str,
        description: str,
        args_schema: dict | None = None,
        requires_guild: bool = True,
        admin_required: bool = False,
        risk_level: str = "LOW",
    ):
        self.name = name
        self.description = description
        self.args_schema = args_schema or {}
        self.requires_guild = requires_guild
        self.admin_required = admin_required
        self.risk_level = risk_level  # LOW | MEDIUM | HIGH | CRITICAL

    def validate_args(self, args: dict) -> tuple[bool, str]:
        """
        Validate arguments against the schema.

        Returns:
            (is_valid, error_message)
        """
        for required in self.args_schema.get("required", []):
            if required not in args:
                return False, f"Missing required argument: {required}"
        for arg_name, arg_type in self.args_schema.get("types", {}).items():
            if arg_name in args:
                val = args[arg_name]
                if not isinstance(val, arg_type):
                    return False, f"Argument '{arg_name}' should be {arg_type.__name__}, got {type(val).__name__}"
        return True, ""


# ---------------------------------------------------------------------------
# Built-in tool specs
# ---------------------------------------------------------------------------

BUILTIN_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="get_time",
        description="Return the current server time.",
        risk_level="LOW",
    ),
    ToolSpec(
        name="server_info",
        description="Return the name and member count of the current server.",
        requires_guild=True,
        risk_level="LOW",
    ),
    ToolSpec(
        name="health_check",
        description="Analyze server health: member activity, channel usage, moderation stats.",
        requires_guild=True,
        risk_level="LOW",
    ),
    ToolSpec(
        name="save_template",
        description="Save the current server configuration as a named template.",
        requires_guild=True,
        risk_level="MEDIUM",
    ),
    ToolSpec(
        name="load_template",
        description="Load and apply a named server template.",
        requires_guild=True,
        risk_level="MEDIUM",
    ),
    ToolSpec(
        name="list_templates",
        description="List all available server templates.",
        risk_level="LOW",
    ),
    ToolSpec(
        name="undo_changes",
        description="Undo the last N server changes.",
        args_schema={"types": {"count": int}},
        risk_level="LOW",
    ),
    ToolSpec(
        name="create_channel",
        description="Create a new text or voice channel.",
        args_schema={"required": ["name"], "types": {"name": str, "type": str}},
        requires_guild=True,
        admin_required=True,
        risk_level="MEDIUM",
    ),
    ToolSpec(
        name="create_role",
        description="Create a new server role.",
        args_schema={"required": ["name"], "types": {"name": str}},
        requires_guild=True,
        admin_required=True,
        risk_level="MEDIUM",
    ),
    ToolSpec(
        name="kick_member",
        description="Kick a member from the server.",
        args_schema={"required": ["member"], "types": {"member": str, "reason": str}},
        requires_guild=True,
        admin_required=True,
        risk_level="HIGH",
    ),
    ToolSpec(
        name="ban_member",
        description="Ban a member from the server.",
        args_schema={"required": ["member"], "types": {"member": str, "reason": str}},
        requires_guild=True,
        admin_required=True,
        risk_level="CRITICAL",
    ),
    ToolSpec(
        name="unban_member",
        description="Unban a previously banned member.",
        args_schema={"required": ["member"], "types": {"member": str}},
        requires_guild=True,
        admin_required=True,
        risk_level="MEDIUM",
    ),
    ToolSpec(
        name="timeout_member",
        description="Timeout a member (mute in all channels).",
        args_schema={"required": ["member"], "types": {"member": str, "duration": int}},
        requires_guild=True,
        admin_required=True,
        risk_level="MEDIUM",
    ),
    ToolSpec(
        name="assign_role",
        description="Assign a role to a member.",
        args_schema={"required": ["member", "role"], "types": {"member": str, "role": str}},
        requires_guild=True,
        admin_required=True,
        risk_level="MEDIUM",
    ),
]


class ToolDecisionEngine:
    """
    Decides how to handle a message: direct response or tool calls.

    Rules:
      - If intent is clearly CHAT only → DIRECT
      - If admin/plan/analysis mode with matching keywords → tool(s)
      - If ambiguous → CLARIFICATION
      - Never call tools blindly
    """

    def __init__(self, extra_tools: list[ToolSpec] | None = None):
        self._tool_index: dict[str, ToolSpec] = {t.name: t for t in BUILTIN_TOOLS}
        if extra_tools:
            for t in extra_tools:
                self._tool_index[t.name] = t

    def register_tool(self, spec: ToolSpec):
        """Register an additional tool."""
        self._tool_index[spec.name] = spec

    def decide(
        self,
        modes: list[Mode],
        message: str,
        params: dict | None = None,
        complexity: Complexity = Complexity.LOW,
        is_directed: bool = True,
        _return_confidence: bool = False,
    ) -> tuple[ToolDecision, list[str], str] | tuple[ToolDecision, list[str], str, float]:
        """
        Decide how to handle the request.

        Args:
            modes: Active modes from ModeClassifier
            message: Raw user message
            params: Extracted parameters
            complexity: Request complexity
            is_directed: True if the message was directed at the bot

        Returns:
            (tool_decision, selected_tool_names, clarification_needed)
        """
        raw = message.strip()
        lower = raw.lower()
        params = params or {}

        tools: list[str] = []
        clarification = ""

        # === CHAT ONLY → DIRECT RESPONSE ===
        if modes == [Mode.CHAT] or (modes == [Mode.CHAT, Mode.QUESTION] and len(raw) < 60):
            result = ToolDecision.DIRECT, [], ""
            if _return_confidence:
                return (*result, 0.85)  # High confidence in chat classification
            return result

        # === ANALYSIS mode → health_check tool ===
        if Mode.ANALYSIS in modes and any(k in lower for k in ["health", "analyze", "check", "audit", "review"]):
                tools.append("health_check")

        # === PLAN / ADMIN mode → server management tools ===
        if Mode.PLAN in modes or Mode.ADMIN in modes:
            tools.extend(self._detect_management_tools(lower, params))

        # === MEMORY mode → recall / store tools ===
        if Mode.MEMORY in modes and any(k in lower for k in ["remember", "store", "log", "note", "save"]):
            # RAG/memory tools are implicit in agent.handle()
            # No explicit tool needed — handled by LLM context
            pass

        # === TOOL mode → explicit tool call ===
        if Mode.TOOL in modes:
            detected = self._detect_management_tools(lower, params)
            for t in detected:
                if t not in tools:
                    tools.append(t)

        # === Handle extracted params ===
        # If params contain tool-relevant data, add the tool
        if params.get("template_action"):
            action = params["template_action"]
            if action in ("save", "store"):
                tools.append("save_template")
            elif action in ("load", "apply"):
                tools.append("load_template")
            elif action == "list":
                tools.append("list_templates")

        if params.get("action") == "member_mgmt":
            # Member management was detected
            if "ban" in lower:
                tools.append("ban_member")
            elif "kick" in lower:
                tools.append("kick_member")
            elif "timeout" in lower or "mute" in lower:
                tools.append("timeout_member")
            elif "role" in lower:
                tools.append("assign_role")

        # === COMPLEXITY overrides ===
        if complexity == Complexity.EXTREME and len(tools) >= 3:
                clarification = (
                    "That's a complex request involving multiple operations. "
                    "Can you break it down into smaller steps?"
                )

        # === AMBIGUITY check ===
        if not tools and (not is_directed or Mode.CHAT in modes):
            # No clear tool signal, chat is more likely
            result = ToolDecision.DIRECT, [], ""
            if _return_confidence:
                return (*result, 0.65)  # Low confidence — might need tools we don't know about
            return result

        # === Deduplicate and classify ===
        tools = list(dict.fromkeys(tools))

        if len(tools) == 0:
            result = ToolDecision.DIRECT, [], ""
        elif len(tools) == 1:
            result = ToolDecision.SINGLE_TOOL, tools, ""
        else:
            result = ToolDecision.MULTIPLE_TOOLS, tools, clarification

        if len(tools) == 0:
            confidence = 0.65
        elif len(tools) == 1:
            confidence = 0.78
        else:
            confidence = 0.82

        if _return_confidence:
            return (*result, confidence)
        return result

    def _detect_management_tools(self, lower: str, params: dict) -> list[str]:
        """Detect which management tools are needed from keywords."""
        detected: list[str] = []

        # Template operations
        if any(k in lower for k in ["save template", "store template"]):
            detected.append("save_template")
        if any(k in lower for k in ["load template", "apply template", "use template"]):
            detected.append("load_template")
        if any(k in lower for k in ["list templates", "show templates", "what templates"]):
            detected.append("list_templates")

        # Undo
        if any(k in lower for k in ["undo", "revert", "rollback"]):
            detected.append("undo_changes")

        # Channel creation
        if any(k in lower for k in ["create channel", "add channel", "make channel", "new channel"]):
            detected.append("create_channel")

        # Role creation
        if any(k in lower for k in ["create role", "add role", "make role", "new role"]):
            detected.append("create_role")

        # Member actions
        if "kick" in lower:
            detected.append("kick_member")
        if "ban" in lower:
            detected.append("ban_member")
        if "unban" in lower:
            detected.append("unban_member")
        if "timeout" in lower or "mute" in lower:
            detected.append("timeout_member")
        if "assign role" in lower or "give role" in lower or "add role to" in lower:
            detected.append("assign_role")

        return detected

    def validate_tool_call(
        self,
        tool_name: str,
        args: dict,
        is_admin: bool = False,
        has_guild: bool = True,
    ) -> tuple[bool, str]:
        """
        Validate a tool call before execution.

        Checks:
          - Tool exists
          - Required arguments present
          - Type correctness
          - Permission level
          - Guild availability

        Returns:
            (is_valid, error_message)
        """
        tool = self._tool_index.get(tool_name)
        if not tool:
            return False, f"Unknown tool: {tool_name}"

        if not has_guild and tool.requires_guild:
            return False, f"Tool '{tool_name}' requires being in a server, not a DM."

        if tool.admin_required and not is_admin:
            return False, f"Tool '{tool_name}' requires admin permissions."

        return tool.validate_args(args)
