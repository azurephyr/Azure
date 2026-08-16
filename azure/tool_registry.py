"""
Tool Registry - Auto-Discovery System for LLM-Driven Architecture

Automatically discovers all available Discord management tools and generates
LLM-readable descriptions. No manual maintenance needed.

Philosophy: Python provides tools, LLM makes decisions.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolInfo:
    """Metadata about a tool."""
    name: str
    function: Callable
    signature: str
    docstring: str
    parameters: dict[str, Any]
    examples: list[str] = field(default_factory=list)
    category: str = "general"


class ToolRegistry:
    """
    Auto-discovers and catalogs all available Discord management tools.

    The LLM uses this registry to:
    1. See what capabilities are available
    2. Understand how to use each tool
    3. Choose the right tools for any request

    No hardcoded patterns - pure capability discovery.
    """

    def __init__(self, discord_tools):
        """
        Initialize tool registry from Discord management tools.

        Args:
            discord_tools: DiscordManagementTools instance
        """
        self.discord_tools = discord_tools
        self.tools: dict[str, ToolInfo] = {}
        self._discover_tools()
        self._categorize_tools()

    def _discover_tools(self):
        """Auto-discover all async methods from DiscordManagementTools."""
        for name in dir(self.discord_tools):
            # Skip private methods and non-callables
            if name.startswith('_'):
                continue

            attr = getattr(self.discord_tools, name)

            # Only include async methods (actual Discord operations)
            if not asyncio.iscoroutinefunction(attr):
                continue

            # Extract metadata
            sig = inspect.signature(attr)
            doc = inspect.getdoc(attr) or "No description available."

            # Parse parameters
            params = {}
            for param_name, param in sig.parameters.items():
                if param_name in ('self', 'guild', 'ctx'):
                    continue
                params[param_name] = {
                    'type': str(param.annotation) if param.annotation != inspect.Parameter.empty else 'Any',
                    'default': str(param.default) if param.default != inspect.Parameter.empty else None,
                    'required': param.default == inspect.Parameter.empty
                }

            # Extract natural language examples from docstring
            examples = self._extract_examples(doc)

            # Store tool info
            self.tools[name] = ToolInfo(
                name=name,
                function=attr,
                signature=str(sig),
                docstring=doc,
                parameters=params,
                examples=examples,
            )

    def _extract_examples(self, docstring: str) -> list[str]:
        """Extract natural language examples from docstring."""
        examples = []
        lines = docstring.split('\n')
        in_examples = False

        for line in lines:
            line = line.strip()
            if 'Natural language examples:' in line or 'Examples:' in line:
                in_examples = True
                continue

            if in_examples:
                if line.startswith('-') or line.startswith('•'):
                    example = line.lstrip('-•').strip().strip('"')
                    if example:
                        examples.append(example)
                elif line.startswith('Args:') or line.startswith('Parameters:'):
                    break

        return examples

    def _categorize_tools(self):
        """Categorize tools for better LLM understanding."""
        categories = {
            'role': ['create_role', 'edit_role', 'delete_role', 'assign_role', 'remove_role'],
            'channel': ['create_channel', 'edit_channel', 'delete_channel', 'move_channel', 'sync_channel_permissions'],
            'category': ['create_category', 'edit_category', 'delete_category'],
            'permission': ['set_channel_permissions', 'clear_channel_permissions'],
            'member': ['kick_member', 'ban_member', 'unban_member', 'timeout_member', 'set_nickname',
                       'move_member_to_voice', 'deafen_member', 'mute_member'],
            'webhook': ['create_webhook', 'delete_webhook'],
            'server': ['set_server_name', 'set_verification_level', 'set_content_filter',
                       'set_notifications', 'set_afk_channel', 'set_system_channel', 'set_rules_channel'],
            'event': ['create_scheduled_event', 'delete_scheduled_event'],
            'invite': ['create_invite'],
            'message': ['pin_message', 'unpin_message'],
            'thread': ['create_thread', 'archive_thread'],
            'forum': ['create_forum_channel', 'create_forum_post', 'manage_forum_tags'],
            'stage': ['create_stage_channel', 'start_stage_instance', 'manage_stage_speaker'],
            'sticker': ['create_sticker', 'delete_sticker'],
            'emoji': ['create_emoji', 'delete_emoji'],
            'automod': ['create_automod_rule', 'enable_spam_filter', 'enable_keyword_filter'],
            'audit': ['get_audit_logs', 'find_who_did_action'],
            'voice': ['set_voice_bitrate', 'set_voice_user_limit', 'set_voice_region'],
            'welcome': ['set_welcome_screen'],
            'template': ['create_server_template', 'sync_server_template'],
            'analysis': ['get_server_state', 'preflight_check'],
            'planning': ['generate_plan', 'execute_plan', 'execute_plan_parallel'],
            'undo': ['undo_last'],
        }

        for category, tool_names in categories.items():
            for tool_name in tool_names:
                if tool_name in self.tools:
                    self.tools[tool_name].category = category

    def get_tool_descriptions_for_llm(self, include_examples: bool = True,
                                      categories: list[str] = None) -> str:
        """
        Generate LLM-readable tool descriptions.

        Args:
            include_examples: Include natural language examples
            categories: Filter by categories (None = all)

        Returns:
            Formatted string describing all available tools
        """
        output = []
        output.append("# AVAILABLE DISCORD MANAGEMENT TOOLS\n")
        output.append("You have access to the following tools for managing Discord servers:\n")

        # Group by category
        tools_by_category = {}
        for tool in self.tools.values():
            if categories and tool.category not in categories:
                continue
            if tool.category not in tools_by_category:
                tools_by_category[tool.category] = []
            tools_by_category[tool.category].append(tool)

        # Format each category
        for category, tools in sorted(tools_by_category.items()):
            output.append(f"\n## {category.upper()} TOOLS\n")

            for tool in sorted(tools, key=lambda t: t.name):
                output.append(f"\n### {tool.name}")

                # First line of docstring (summary)
                first_line = tool.docstring.split('\n')[0]
                output.append(f"{first_line}\n")

                # Parameters
                if tool.parameters:
                    output.append("**Parameters:**")
                    for param_name, param_info in tool.parameters.items():
                        required = "REQUIRED" if param_info['required'] else f"optional, default={param_info['default']}"
                        output.append(f"  - {param_name} ({param_info['type']}): {required}")
                    output.append("")

                # Examples
                if include_examples and tool.examples:
                    output.append("**Natural Language Examples:**")
                    for example in tool.examples[:3]:  # Max 3 examples
                        output.append(f'  - "{example}"')
                    output.append("")

        return "\n".join(output)

    def get_tool_descriptions_json(self) -> str:
        """Generate JSON format tool descriptions for LLM function calling."""
        tools_json = []

        for tool in self.tools.values():
            # Skip internal tools
            if tool.name in ('generate_plan', 'execute_plan', 'execute_plan_parallel',
                            'preflight_check', 'get_server_state', 'undo_last'):
                continue

            tool_spec = {
                "name": tool.name,
                "description": tool.docstring.split('\n')[0],  # First line
                "category": tool.category,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }

            # Add parameters
            for param_name, param_info in tool.parameters.items():
                tool_spec["parameters"]["properties"][param_name] = {
                    "type": self._map_type(param_info['type']),
                    "description": f"Parameter for {tool.name}"
                }
                if param_info['required']:
                    tool_spec["parameters"]["required"].append(param_name)

            # Add examples
            if tool.examples:
                tool_spec["examples"] = tool.examples[:3]

            tools_json.append(tool_spec)

        return json.dumps(tools_json, indent=2)

    def _map_type(self, type_str: str) -> str:
        """Map Python type to JSON schema type."""
        type_str = type_str.lower()
        if 'str' in type_str:
            return "string"
        elif 'int' in type_str:
            return "integer"
        elif 'bool' in type_str:
            return "boolean"
        elif 'list' in type_str or 'sequence' in type_str:
            return "array"
        elif 'dict' in type_str:
            return "object"
        else:
            return "string"

    def get_tool(self, name: str) -> ToolInfo:
        """Get tool info by name."""
        return self.tools.get(name)

    def search_tools(self, query: str) -> list[ToolInfo]:
        """Search tools by name, description, or examples."""
        query_lower = query.lower()
        results = []

        for tool in self.tools.values():
            # Search in name
            if query_lower in tool.name.lower():
                results.append(tool)
                continue

            # Search in docstring
            if query_lower in tool.docstring.lower():
                results.append(tool)
                continue

            # Search in examples
            if any(query_lower in ex.lower() for ex in tool.examples):
                results.append(tool)
                continue

        return results

    def get_categories(self) -> list[str]:
        """Get list of all tool categories."""
        return list(set(tool.category for tool in self.tools.values()))

    def get_tools_by_category(self, category: str) -> list[ToolInfo]:
        """Get all tools in a specific category."""
        return [tool for tool in self.tools.values() if tool.category == category]

    def get_summary(self) -> str:
        """Get a brief summary of available tools."""
        categories = self.get_categories()
        total_tools = len(self.tools)

        summary = []
        summary.append(f"Tool Registry: {total_tools} tools across {len(categories)} categories")
        summary.append("")

        for category in sorted(categories):
            tools = self.get_tools_by_category(category)
            tool_names = [t.name for t in tools]
            summary.append(f"  {category.upper()}: {len(tools)} tools - {', '.join(tool_names[:3])}...")

        return "\n".join(summary)


def create_registry(discord_tools) -> ToolRegistry:
    """Convenience function to create a tool registry."""
    return ToolRegistry(discord_tools)
