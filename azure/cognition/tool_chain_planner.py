"""
ToolChainPlanner — Priority 4B: Multi-Tool Coordination

Plans tool sequences, determines dependencies, optimizes execution order,
and defines rollback plans for complex multi-step server tasks.

Architecture:
  Router → IntentDecomposer → ReasonerAgent → ToolChainPlanner → Executor

The planner sits AFTER the ReasonerAgent but BEFORE the Executor. It takes
the selected_tools from the Reasoner and turns them into an ordered chain.

For simple single-tool tasks, the planner returns immediately.
For multi-tool tasks, it:
  1. Looks up known template sequences
  2. Detects inter-tool dependencies
  3. Optimizes execution order (parallel where possible)
  4. Defines rollback steps for each tool

Examples:
  - "create full staff system" → create_role (mod) → create_role (admin) → set_permissions → create_channel (staff-only)
  - "restructure server" → delete_channel (old) → create_channel (new) → set_permissions → move_messages
  - "setup moderation workflow" → create_channel (logs) → create_role (mod) → set_permissions → configure_automod

"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("azure.cognition.tool_chain_planner")


# ---------------------------------------------------------------------------
# Known tool sequences (templates)
# ---------------------------------------------------------------------------

# Each template maps a high-level goal to a sequence of tool calls
TOOL_TEMPLATES = {
    "staff_system": {
        "description": "Create a full staff/moderation system",
        "tools": ["create_role", "create_role", "set_permissions", "create_channel"],
        "execution_order": [0, 1, 2, 3],  # sequential: role → role → permissions → channel
        "dependencies": [(1, 0), (2, 1), (3, 2)],  # each step depends on previous
        "rollback_plan": ["delete_role", "delete_role", "reset_permissions", "delete_channel"],
    },
    "server_restructure": {
        "description": "Restructure server channels and categories",
        "tools": ["create_channel", "create_channel", "set_permissions", "delete_channel"],
        "execution_order": [0, 1, 2, 3],
        "dependencies": [(2, 1), (3, 0)],  # permissions depend on channel, delete depends on old
        "rollback_plan": ["delete_channel", "delete_channel", "reset_permissions", "restore_channel"],
    },
    "moderation_workflow": {
        "description": "Set up moderation logging and roles",
        "tools": ["create_channel", "create_role", "set_permissions", "create_channel"],
        "execution_order": [0, 1, 2, 3],
        "dependencies": [(1, 0), (2, 1), (3, 2)],
        "rollback_plan": ["delete_channel", "delete_role", "reset_permissions", "delete_channel"],
    },
    "welcome_system": {
        "description": "Set up welcome/onboarding system",
        "tools": ["create_channel", "create_role", "set_permissions"],
        "execution_order": [0, 1, 2],
        "dependencies": [(1, 0), (2, 1)],
        "rollback_plan": ["delete_channel", "delete_role", "reset_permissions"],
    },
}


# Tool dependencies: tool A must run before tool B
TOOL_DEPENDENCIES = {
    "create_role": ["set_permissions", "assign_role"],  # role must exist before permissions/assignment
    "create_channel": ["set_permissions", "send_message"],  # channel must exist first
    "ban": ["unban"],  # can't unban before ban (semantic)
    "create_category": ["create_channel"],  # category must exist
}


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class ToolChainPlan:
    """A planned sequence of tool executions."""
    tools: list[str] = field(default_factory=list)  # tool names in planned order
    execution_order: list[int] = field(default_factory=list)  # indices into tools (supports parallel groups)
    dependencies: list[tuple[int, int]] = field(default_factory=list)  # (step_a, step_b) means step_b depends on step_a
    rollback_plan: list[str] = field(default_factory=list)  # rollback tool for each step
    parallel_groups: list[list[int]] = field(default_factory=list)  # steps that can run in parallel
    confidence: float = 0.0
    template_used: str | None = None  # which template matched, if any

    def is_sequential(self) -> bool:
        """True if all steps must run in order."""
        return len(self.parallel_groups) <= 1

    def total_steps(self) -> int:
        return len(self.tools)

    def to_dict(self) -> dict:
        return {
            "tools": self.tools,
            "execution_order": self.execution_order,
            "dependencies": self.dependencies,
            "rollback_plan": self.rollback_plan,
            "parallel_groups": self.parallel_groups,
            "confidence": self.confidence,
            "template_used": self.template_used,
        }


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

class ToolChainPlanner:
    """
    Plans multi-tool execution sequences with dependency resolution and rollback.

    Design:
      - Templates: fast lookup for known complex tasks
      - Dependency resolver: topological sort for arbitrary tool lists
      - LLM fallback: only for novel multi-tool combinations not in templates
    """

    def __init__(self, llm=None):
        self.llm = llm
        self._template_hits = 0
        self._resolver_hits = 0
        self._llm_hits = 0

    def plan(self, selected_tools: list[str], intent: str = "",
             complexity: str = "MEDIUM") -> ToolChainPlan:
        """
        Plan a tool execution sequence.

        Args:
            selected_tools: List of tool names from the ReasonerAgent
            intent: The true intent (helps template matching)
            complexity: Message complexity (LOW/MEDIUM/HIGH/EXTREME)

        Returns:
            ToolChainPlan with execution order and rollback
        """
        # Single tool → no planning needed
        if len(selected_tools) <= 1:
            return ToolChainPlan(
                tools=selected_tools,
                execution_order=[0] if selected_tools else [],
                rollback_plan=["undo"] if selected_tools else [],
                confidence=1.0,
            )

        # Step 1: Try template matching
        template_plan = self._match_template(selected_tools, intent)
        if template_plan:
            self._template_hits += 1
            return template_plan

        # Step 2: Dependency resolver for arbitrary tool lists
        resolved = self._resolve_dependencies(selected_tools)
        if resolved.confidence >= 0.7:
            self._resolver_hits += 1
            return resolved

        # Step 3: LLM fallback for novel combinations
        if self.llm and complexity in ("HIGH", "EXTREME"):
            self._llm_hits += 1
            return self._llm_plan(selected_tools, intent)

        # Step 4: Sequential fallback (safe but slow)
        return ToolChainPlan(
            tools=selected_tools,
            execution_order=list(range(len(selected_tools))),
            dependencies=[(i, i+1) for i in range(len(selected_tools)-1)],
            rollback_plan=["undo"] * len(selected_tools),
            confidence=0.5,
        )

    # -----------------------------------------------------------------------
    # Template matching
    # -----------------------------------------------------------------------

    def _match_template(self, tools: list[str], intent: str) -> ToolChainPlan | None:
        """Match selected tools against known templates."""
        tool_set = set(tools)
        intent_lower = intent.lower()

        for template_name, template in TOOL_TEMPLATES.items():
            template_tools = set(template["tools"])
            # Check if tools match (allowing extras)
            if tool_set.issubset(template_tools) or template_tools.issubset(tool_set):
                # Check intent relevance
                keywords = template_name.replace("_", " ")
                if any(k in intent_lower for k in keywords.split()):
                    return self._build_from_template(template, template_name)

        # Also check intent-only matches (tools may not match exactly)
        for template_name, template in TOOL_TEMPLATES.items():
            keywords = template_name.replace("_", " ")
            if any(k in intent_lower for k in keywords.split()):
                return self._build_from_template(template, template_name)

        return None

    def _build_from_template(self, template: dict, name: str) -> ToolChainPlan:
        """Build a ToolChainPlan from a template definition."""
        tools = template["tools"]
        order = template["execution_order"]
        deps = template["dependencies"]
        rollback = template["rollback_plan"]

        # Build parallel groups from dependencies
        parallel_groups = self._build_parallel_groups(order, deps)

        return ToolChainPlan(
            tools=tools,
            execution_order=order,
            dependencies=deps,
            rollback_plan=rollback,
            parallel_groups=parallel_groups,
            confidence=0.85,
            template_used=name,
        )

    def _build_parallel_groups(self, order: list[int],
                                deps: list[tuple[int, int]]) -> list[list[int]]:
        """Group steps that can run in parallel."""
        # Simple approach: steps with no incoming dependencies can be parallel
        if not deps:
            return [order]

        # Find all steps that have dependencies
        has_deps = set()
        for _, dependent in deps:
            has_deps.add(dependent)

        # Group independent steps together
        independent = [step for step in order if step not in has_deps]
        dependent = [step for step in order if step in has_deps]

        if independent and dependent:
            return [independent, dependent]
        return [order]

    # -----------------------------------------------------------------------
    # Dependency resolver
    # -----------------------------------------------------------------------

    def _resolve_dependencies(self, tools: list[str]) -> ToolChainPlan:
        """Topologically sort tools based on known dependencies."""
        # Build dependency graph
        graph = {i: set() for i in range(len(tools))}
        in_degree = {i: 0 for i in range(len(tools))}

        for i, tool_a in enumerate(tools):
            for j, tool_b in enumerate(tools):
                if i == j:
                    continue
                # Check if tool_b depends on tool_a
                if tool_a in TOOL_DEPENDENCIES and tool_b in TOOL_DEPENDENCIES[tool_a]:
                    graph[i].add(j)
                    in_degree[j] += 1

        # Topological sort (Kahn's algorithm)
        order = []
        queue = [i for i, deg in in_degree.items() if deg == 0]

        while queue:
            # Sort for determinism
            queue.sort()
            node = queue.pop(0)
            order.append(node)
            for neighbor in sorted(graph[node]):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # If not all tools were ordered, there's a cycle → fallback to sequential
        if len(order) != len(tools):
            return ToolChainPlan(
                tools=tools,
                execution_order=list(range(len(tools))),
                dependencies=[(i, i+1) for i in range(len(tools)-1)],
                rollback_plan=["undo"] * len(tools),
                confidence=0.4,
            )

        # Build dependency list from graph
        deps = []
        for i in range(len(tools)):
            for j in graph[i]:
                deps.append((i, j))

        # Build parallel groups
        parallel_groups = self._build_parallel_groups(order, deps)

        # Generate rollback plan
        rollback = []
        for tool in tools:
            rollback.append(self._get_rollback_tool(tool))

        return ToolChainPlan(
            tools=tools,
            execution_order=order,
            dependencies=deps,
            rollback_plan=rollback,
            parallel_groups=parallel_groups,
            confidence=0.75,
        )

    def _get_rollback_tool(self, tool: str) -> str:
        """Get the rollback tool for a given tool."""
        rollback_map = {
            "create_channel": "delete_channel",
            "create_role": "delete_role",
            "create_category": "delete_category",
            "set_permissions": "reset_permissions",
            "ban": "unban",
            "kick": "invite",  # can't truly undo kick, but can re-invite
            "assign_role": "remove_role",
            "send_message": "delete_message",
        }
        return rollback_map.get(tool, "undo")

    # -----------------------------------------------------------------------
    # LLM fallback
    # -----------------------------------------------------------------------

    def _llm_plan(self, tools: list[str], intent: str) -> ToolChainPlan:
        """Use Qwen to plan a novel tool sequence."""
        safe_intent = intent.replace("{", "{{").replace("}", "}}")
        prompt = f"""Plan the optimal execution order for these Discord tools:

Tools: {tools}
Intent: {safe_intent}

Rules:
1. Some tools must run before others (e.g., create_role before set_permissions)
2. Independent tools can run in parallel
3. Each step needs a rollback action

Return ONLY JSON:
{{
  "execution_order": [0, 1, 2],
  "dependencies": [[0, 1], [1, 2]],
  "rollback_plan": ["delete_role", "reset_permissions", "delete_channel"]
}}"""

        try:
            raw = self.llm.chat(
                [
                    {"role": "system", "content": "You are a tool chain planner. Output ONLY JSON."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=150,
                temperature=0.2,
            )

            # Extract JSON
            code_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
            if code_block:
                raw = code_block.group(1)

            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                data = json.loads(raw[start:end+1])

                order = data.get("execution_order", list(range(len(tools))))
                deps = [tuple(d) for d in data.get("dependencies", [])]
                rollback = data.get("rollback_plan", ["undo"] * len(tools))

                parallel_groups = self._build_parallel_groups(order, deps)

                return ToolChainPlan(
                    tools=tools,
                    execution_order=order,
                    dependencies=deps,
                    rollback_plan=rollback,
                    parallel_groups=parallel_groups,
                    confidence=0.7,
                )
        except Exception as e:
            logger.error(f"[tool_chain_planner] LLM error: {e}")


        # Fallback to sequential
        return ToolChainPlan(
            tools=tools,
            execution_order=list(range(len(tools))),
            dependencies=[(i, i+1) for i in range(len(tools)-1)],
            rollback_plan=["undo"] * len(tools),
            confidence=0.4,
        )

    # -----------------------------------------------------------------------
    # Stats
    # -----------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return planning statistics."""
        total = self._template_hits + self._resolver_hits + self._llm_hits
        return {
            "total": total,
            "template_hits": self._template_hits,
            "resolver_hits": self._resolver_hits,
            "llm_hits": self._llm_hits,
            "template_rate": self._template_hits / total if total else 0,
        }
