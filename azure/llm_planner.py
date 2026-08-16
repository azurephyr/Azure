"""
LLM Planner - Zero Hardcoding, Full Autonomy

The LLM makes ALL decisions:
- What tools to use
- What parameters to pass
- What order to execute
- How to handle errors

Python only provides tools - never dictates behavior.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from .tool_registry import ToolRegistry

logger = logging.getLogger("azure.llm_planner")


@dataclass
class ExecutionResult:
    """Result of executing a single tool."""
    tool_name: str
    success: bool
    detail: str = ""
    error: str = ""
    reasoning: str = ""  # Why the LLM chose this tool


class LLMPlanner:
    """
    LLM-driven planning and execution engine.

    NO HARDCODED LOGIC. The LLM decides everything:
    1. Interprets user intent
    2. Chooses appropriate tools
    3. Determines parameters
    4. Plans execution order
    5. Handles errors and retries

    This is true AI autonomy.
    """

    def __init__(self, llm, tool_registry: ToolRegistry, discord_tools):
        """
        Initialize LLM planner.

        Args:
            llm: Local LLM instance (handles chat completions)
            tool_registry: Registry of available tools
            discord_tools: DiscordManagementTools instance (for execution)
        """
        self.llm = llm
        self.registry = tool_registry
        self.discord_tools = discord_tools
        self.max_retries = 3
        self.verbose = False  # Changed to False to reduce log spam

        # Plan caching
        self._plan_cache = {}
        self._cache_ttl = 3600  # 1 hour
        self._cache_max_size = 50

    async def generate_plan(self, user_request: str, server_state: dict,
                           guild_id: int, user_id: int = None) -> dict:
        """
        Let the LLM generate an execution plan from scratch.

        NO HARDCODED PATTERNS. The LLM analyzes:
        - What the user wants
        - Current server state
        - Available tools
        - Dependencies and order

        Args:
            user_request: Natural language request from user
            server_state: Current Discord server state
            guild_id: Discord guild ID
            user_id: Optional user ID for context

        Returns:
            JSON plan with tool calls
        """

        # Build condensed tool list grouped by category
        list(self.registry.tools.keys())
        categories = self.registry.get_categories()
        cat_lines = []
        for cat in sorted(categories):
            tools_in_cat = [t.name for t in self.registry.get_tools_by_category(cat)]
            if tools_in_cat:
                cat_lines.append(f"  {cat.upper()}: {', '.join(sorted(tools_in_cat))}")
        tool_descriptions = "Tools by category:\n" + "\n".join(cat_lines)

        # Create prompt that gives LLM full autonomy
        prompt = self._build_planning_prompt(
            user_request=user_request,
            server_state=server_state,
            tool_descriptions=tool_descriptions
        )

        if self.verbose:
            logger.info(f"[LLM-Planner] Generating plan for: '{user_request}'")


        # Let LLM make ALL decisions
        messages = [
            {
                "role": "system",
                "content": "You are a Discord server management tool. You MUST output ONLY executable ACTION lines. NO explanations, NO markdown, NO bullet points, NO numbered lists. Every line must be: ACTION tool_name param=value. If you output anything else, the system will fail."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]

        # Generate plan
        try:
            response = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.llm.chat(
                    messages, max_tokens=2048, temperature=0.3,
                )
            )

            logger.info(f"[LLM-Planner] Raw LLM response (first 300): {response[:300]}")

            # Try ACTION format first (works with lite models)
            plan = self._parse_action_format(response, user_request)

            # Fall back to JSON parsing
            if not plan.get("steps"):
                plan = self._parse_plan_response(response)

            # Fall back to extracting actions from markdown text
            if not plan.get("steps"):
                plan = self._parse_markdown_actions(response, user_request)

            # Final fallback: use LLM to convert markdown into specific actions
            if not plan.get("steps") and response.strip():
                logger.info("[LLM-Planner] All parsers failed — using LLM to extract actions from markdown")
                plan = await self._llm_extract_actions(response, user_request)

            step_count = len(plan.get("steps", []))
            logger.info(f"[LLM-Planner] Parsed plan: {step_count} steps")
            if step_count == 0:
                logger.warning(f"[LLM-Planner] EMPTY PLAN. Raw: {response[:500]}")

            return plan

        except Exception as e:
            if self.verbose:
                logger.error(f"[LLM-Planner] ERROR in plan generation: {e}")


            # Return empty plan on error
            return {
                "analysis": f"Failed to generate plan: {str(e)}",
                "reasoning": "LLM plan generation failed",
                "steps": [],
                "error": str(e)
            }

    def _build_planning_prompt(self, user_request: str, server_state: dict,
                               tool_descriptions: str) -> str:
        """Build the prompt that gives LLM full context and autonomy."""

        # Build detailed role info with permissions
        roles_info = []
        for r in server_state.get('roles', [])[:15]:
            perms = r.get('permissions', [])
            member_count = r.get('member_count', 0)
            roles_info.append(f"  - {r['name']}: {member_count} members, perms={perms[:8]}")
        roles_text = '\n'.join(roles_info) or '  none'

        # Build detailed channel info with overrides
        channels_info = []
        for c in server_state.get('channels', [])[:20]:
            cat = c.get('category', 'none')
            overrides = c.get('overrides', [])
            ch_line = f"  - #{c['name']} ({c['type']}, category={cat})"
            if overrides:
                for o in overrides:
                    allow = ', '.join(o.get('allow', [])[:5])
                    deny = ', '.join(o.get('deny', [])[:5])
                    ch_line += f"\n    {o['type']} @{o['target']}: allow=[{allow}] deny=[{deny}]"
            channels_info.append(ch_line)
        channels_text = '\n'.join(channels_info) or '  none'

        categories_text = ', '.join([c['name'] for c in server_state.get('categories', [])]) or 'none'

        # Detect if user wants destructive actions
        any(w in user_request.lower() for w in ["delete", "remove", "clear", "reset", "clean", "start from scratch", "nuke", "wipe"])

        prompt = f"""You are a Discord server management bot. Analyze the server state and take action.

USER REQUEST: "{user_request}"

CURRENT SERVER STATE:
Server: {server_state.get('server_name', 'Unknown')} | {server_state.get('member_count', 0)} members
Verification: {server_state.get('verification_level', 'unknown')}
Notifications: {server_state.get('default_notifications', 'unknown')}
Categories: {categories_text}

ROLES (name: members, permissions):
{roles_text}

CHANNELS (name, type, category, permission overrides):
{channels_text}

YOUR TASK:
1. Analyze what needs to change based on the user's request
2. Write ACTION lines to make those changes happen

ACTION FORMAT (one per line):
ACTION action_name param1=value1 param2=value2

AVAILABLE ACTIONS:
ACTION create_role name=NAME color=COLOR
ACTION delete_role name=NAME
ACTION edit_role name=NAME color=COLOR permissions=PERM1,PERM2
ACTION create_category name=NAME
ACTION delete_category name=NAME
ACTION create_channel name=NAME type=text|voice|announcements|stage category=CATEGORY
ACTION delete_channel name=NAME
ACTION edit_channel name=NAME topic=TEXT slowmode_delay=NUMBER nsfw=BOOL
ACTION move_channel channel=CHANNEL category=CATEGORY
ACTION set_permissions channel=CHANNEL role=ROLE send_messages=true|false view_channel=true|false manage_messages=true|false
ACTION clear_permissions channel=CHANNEL role=ROLE
ACTION set_server_name name=TEXT
ACTION set_verification_level level=low|medium|high|very_high
ACTION purge_messages channel=CHANNEL limit=NUMBER
ACTION kick member=NAME reason=TEXT
ACTION ban member=NAME reason=TEXT
ACTION timeout member=NAME duration=SECONDS reason=TEXT

RULES:
- Write ONLY ACTION lines. No explanations, no markdown, no bullet points.
- Create categories BEFORE channels that belong in them
- When auditing permissions, check each channel's overrides against each role's permissions
- If a role has a permission but a channel override denies it, the channel override wins
- Delete truly unused roles (0 members, not @everyone, not managed)
- Fix permission conflicts you find
- Use the user's request as your guide
"""
        return prompt

    def _parse_action_format(self, response: str, user_request: str = "") -> dict:
        """Parse simple ACTION format from lite models into a plan dict.

        Format: ACTION tool_name param1=value1 param2=value2
        """
        import re
        steps = []
        analysis_lines = []

        for line in response.strip().splitlines():
            line = line.strip()
            if not line:
                continue

            # Match ACTION lines (also handle numbered prefixes like "1. ACTION ...")
            m = re.match(r'^(?:\d+\.?\s*)?(?:ACTION|action|Tool|tool|Step|step)[\s:]+(\w+)\s*(.*)', line, re.IGNORECASE)
            if m:
                tool_name = m.group(1).strip()
                params_str = m.group(2).strip()
                params = {}
                # Parse key=value pairs (handles quoted values with spaces)
                for kv in re.finditer(r'(\w+)=(".*?"|\'.*?\'|\S+)', params_str):
                    val = kv.group(2).strip('"\'')
                    params[kv.group(1)] = val
                steps.append({
                    "tool": tool_name,
                    "params": params,
                    "reasoning": f"LLM planned: {tool_name}",
                })
            elif line and not line.startswith(("```", "#", "Example", "Rules", "Write")):
                analysis_lines.append(line)

        analysis = " ".join(analysis_lines) or user_request or "Execute planned actions"
        return {"analysis": analysis, "steps": steps}

    def _parse_plan_response(self, response: str) -> dict:
        """Parse LLM response into structured plan."""
        # Clean response
        response = response.strip()

        # Remove markdown code blocks if present
        if response.startswith("```json"):
            response = response[7:]
        elif response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]

        response = response.strip()

        # Find JSON content
        start = response.find("{")
        end = response.rfind("}")
        if start != -1 and end != -1 and end > start:
            response = response[start:end + 1]

        try:
            plan = json.loads(response)

            # Validate plan structure
            if "steps" not in plan:
                plan["steps"] = []
            if "analysis" not in plan:
                plan["analysis"] = "No analysis provided"
            if "reasoning" not in plan:
                plan["reasoning"] = "No reasoning provided"

            # Validate each step
            available_names = list(self.registry.tools.keys())
            valid_steps = []
            for i, step in enumerate(plan.get("steps", [])):
                if not isinstance(step, dict):
                    logger.warning(f"[LLM-Planner] Step {i} is not a dict, skipping")
                    continue

                tool_name = step.get("tool")
                if not tool_name:
                    logger.warning(f"[LLM-Planner] Step {i} missing tool name, skipping")
                    continue

                # Verify tool exists in registry
                tool_info = self.registry.get_tool(tool_name)
                if not tool_info:
                    # Fuzzy match: try substring/contains against known tools
                    matched_name = None
                    tn = tool_name.lower().replace("-", "_").replace(" ", "_")
                    for registered in available_names:
                        r = registered.lower()
                        if tn in r or r in tn:
                            matched_name = registered
                            break
                    if matched_name:
                        logger.info(
                            f"[LLM-Planner] Step {i}: fuzzy-matched '{tool_name}' → '{matched_name}'"
                        )
                        step["tool"] = matched_name
                    else:
                        logger.warning(
                            f"[LLM-Planner] Step {i} unknown tool: '{tool_name}', "
                            f"keeping anyway. Available: {available_names}"
                        )

                # Ensure params is a dict
                if "params" not in step:
                    step["params"] = {}
                elif not isinstance(step["params"], dict):
                    logger.warning(f"[LLM-Planner] Step {i} params not a dict, resetting")
                    step["params"] = {}

                valid_steps.append(step)

            plan["steps"] = valid_steps
            return plan

        except json.JSONDecodeError as e:
            logger.error(f"[LLM-Planner] JSON parse error: {e}")
            logger.error(f"[LLM-Planner] Raw response (first 500): {response[:500]}")


            return {
                "analysis": "Failed to parse LLM response",
                "reasoning": "JSON decode error",
                "steps": [],
                "error": str(e),
                "raw_response": response
            }

    def _parse_markdown_actions(self, response: str, user_request: str = "") -> dict:
        """Extract executable actions from markdown text when LLM ignores format.

        Handles real-world markdown output patterns like:
        - 'Delete unnecessary roles (e.g., test roles, abandoned staff roles)'
        - '- **Delete** the channel `announcements`'
        - 'Remove `Administrator` from `Trial Mod`'
        - 'Archive `#old-events`'
        - 'Lock `#staff-chat` to `Mod+`'
        """
        import re
        steps = []
        seen = set()  # deduplicate

        # Strip all markdown formatting from the entire response for cleaner matching
        def strip_md(text):
            text = re.sub(r'[*_`]', '', text)
            text = re.sub(r'#{1,6}\s*', '', text)
            text = re.sub(r'\d+\.\s*', '', text)
            return text.strip()

        # Split into lines and also into sentences (for multi-action lines)
        lines = response.splitlines()
        all_text = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith(('```', '---', '===', '>')):
                continue
            # Split on bullet points and numbered items
            parts = re.split(r'[-*•]\s+|\d+\.\s+', line)
            for p in parts:
                p = p.strip()
                if p:
                    all_text.append(p)

        # Also process the full text as one block for patterns that span lines
        full_text = strip_md(response)

        # Pattern list: (regex_pattern, action_name, param_extractor)
        # param_extractor receives the match and returns a dict of params
        _patterns = [
            # Delete role
            (r'delete\s+(?:the\s+)?(?:role\s+)?[`"]?(\w[\w\s-]*?)[`"]?\s*(?:\(|$|,|and|or)', 'delete_role', lambda m: {"role": m.group(1).strip()}),
            (r'delete\s+role\s+[`"]?(\w[\w\s-]*?)[`"]?', 'delete_role', lambda m: {"role": m.group(1).strip()}),
            # Delete channel
            (r'delete\s+(?:the\s+)?(?:channel\s+)?[`"]?#?(\w[\w\s-]*?)[`"]?\s*(?:\(|$|,|and|or)', 'delete_channel', lambda m: {"channel": m.group(1).strip()}),
            (r'delete\s+channel\s+[`"]?#?(\w[\w\s-]*?)[`"]?', 'delete_channel', lambda m: {"channel": m.group(1).strip()}),
            # Delete category
            (r'delete\s+(?:the\s+)?(?:category\s+)?[`"]?(\w[\w\s-]*?)[`"]?\s*(?:\(|$|,|and|or)', 'delete_category', lambda m: {"category": m.group(1).strip()}),
            # Create role
            (r'create\s+(?:a\s+)?role\s+[`"]?(\w[\w\s-]*?)[`"]?', 'create_role', lambda m: {"role": m.group(1).strip()}),
            # Create channel
            (r'create\s+(?:a\s+)?(?:text\s+|voice\s+)?channel\s+[`"]?(\w[\w\s-]*?)[`"]?', 'create_channel', lambda m: {"channel": m.group(1).strip()}),
            # Create category
            (r'create\s+(?:a\s+)?category\s+[`"]?(\w[\w\s-]*?)[`"]?', 'create_category', lambda m: {"category": m.group(1).strip()}),
            # Rename
            (r'rename\s+[`"]?#?(\w[\w\s-]*?)[`"]?\s+to\s+[`"]?(\w[\w\s-]*?)[`"]?', 'edit_channel', lambda m: {"channel": m.group(1).strip(), "name": m.group(2).strip()}),
            # Archive channel (move to archive category, not delete)
            (r'archive\s+[`"]?#?(\w[\w\s-]*?)[`"]?', 'archive_channel', lambda m: {"channel": m.group(1).strip()}),
            # Move channel to category
            (r'move\s+[`"]?#?(\w[\w\s-]*?)[`"]?\s+(?:to|under|into)\s+[`"]?(\w[\w\s-]*?)[`"]?', 'move_channel', lambda m: {"channel": m.group(1).strip(), "category": m.group(2).strip()}),
            # Move channel to top
            (r'move\s+[`"]?#?(\w[\w\s-]*?)[`"]?\s+to\s+top', 'move_channel', lambda m: {"channel": m.group(1).strip(), "category": "top"}),
            # Set permissions / restrict
            (r'restrict\s+[`"]?@everyone[`"]?\s+from\s+[`"]?#?(\w[\w\s-]*?)[`"]?', 'set_permissions', lambda m: {"channel": m.group(1).strip(), "role": "@everyone", "send_messages": "false"}),
            (r'set\s+permissions?\s+(?:on|for)\s+[`"]?#?(\w[\w\s-]*?)[`"]?', 'set_permissions', lambda m: {"channel": m.group(1).strip()}),
            # Lock channel to role
            (r'lock\s+[`"]?#?(\w[\w\s-]*?)[`"]?\s+to\s+[`"]?(\w[\w\s-]*?)[`"]?', 'set_permissions', lambda m: {"channel": m.group(1).strip(), "role": m.group(2).strip(), "send_messages": "true"}),
            # Remove permission from role
            (r'remove\s+[`"]?(\w[\w\s-]*?)[`"]?\s+from\s+[`"]?(\w[\w\s-]*?)[`"]?', 'set_permissions', lambda m: {"role": m.group(1).strip(), "channel": m.group(2).strip(), "send_messages": "false"}),
            # Purge/clean messages
            (r'purge\s+(?:all\s+)?messages?\s+(?:in|from|on)\s+[`"]?#?(\w[\w\s-]*?)[`"]?', 'purge_messages', lambda m: {"channel": m.group(1).strip()}),
            (r'clean(?:e?d)?\s+(?:all\s+)?messages?\s+(?:in|from|on)\s+[`"]?#?(\w[\w\s-]*?)[`"]?', 'purge_messages', lambda m: {"channel": m.group(1).strip()}),
            (r'bulk\s+delete\s+(?:messages?\s+)?(?:in|from|on)\s+[`"]?#?(\w[\w\s-]*?)[`"]?', 'purge_messages', lambda m: {"channel": m.group(1).strip()}),
            # Kick
            (r'kick\s+(?:member\s+)?[`"]?(\w[\w\s-]*?)[`"]?', 'kick', lambda m: {"member": m.group(1).strip()}),
            # Ban
            (r'ban\s+(?:member\s+)?[`"]?(\w[\w\s-]*?)[`"]?', 'ban', lambda m: {"member": m.group(1).strip()}),
            # Timeout
            (r'timeout\s+(?:member\s+)?[`"]?(\w[\w\s-]*?)[`"]?', 'timeout', lambda m: {"member": m.group(1).strip()}),
            # Merge roles (delete the second one)
            (r'merge\s+[`"]?(\w[\w\s-]*?)[`"]?\s+(?:and|with|into)\s+[`"]?(\w[\w\s-]*?)[`"]?', 'delete_role', lambda m: {"role": m.group(2).strip()}),
            # Enable/disable features
            (r'enable\s+community', 'enable_community_mode', lambda m: {}),
            (r'set\s+verification\s+level\s+to\s+(\w+)', 'set_verification_level', lambda m: {"level": m.group(1)}),
        ]

        for text_line in all_text:
            clean = strip_md(text_line)
            if not clean or len(clean) < 5:
                continue

            for pattern, action, param_fn in _patterns:
                m = re.search(pattern, clean, re.IGNORECASE)
                if m:
                    try:
                        params = param_fn(m)
                    except Exception:
                        params = {"name": clean[:50]}

                    # Build a unique key to deduplicate
                    param_str = "|".join(f"{k}={v}" for k, v in sorted(params.items()) if isinstance(v, (str, int, float, bool)))
                    key = f"{action}:{param_str}"
                    if key in seen:
                        continue
                    seen.add(key)

                    step = {
                        "tool": action,
                        "params": params,
                        "reasoning": f"Extracted from markdown: {clean[:80]}",
                    }
                    steps.append(step)
                    break  # one action per line

        # Also scan the full text for patterns that might span multiple lines
        for pattern, action, param_fn in _patterns:
            for m in re.finditer(pattern, full_text, re.IGNORECASE):
                try:
                    params = param_fn(m)
                except Exception:
                    continue
                param_str = "|".join(f"{k}={v}" for k, v in sorted(params.items()) if isinstance(v, (str, int, float, bool)))
                key = f"{action}:{param_str}"
                if key not in seen:
                    seen.add(key)
                    steps.append({
                        "tool": action,
                        "params": params,
                        "reasoning": f"Extracted from full text: {action}",
                    })

        if steps:
            logger.info(f"[LLM-Planner] Extracted {len(steps)} actions from markdown text")

        return {
            "analysis": f"Extracted {len(steps)} actions from markdown response",
            "reasoning": "Markdown fallback parser",
            "steps": steps,
        }

    async def _llm_extract_actions(self, markdown_response: str, user_request: str) -> dict:
        """Extract actions directly from markdown analysis by scanning for concrete items.

        No LLM call — pure regex scanning for backtick-wrapped names and action verbs.
        This is faster and more reliable than asking an LLM to convert formats.
        """
        import re
        steps = []
        seen = set()

        # Normalize the full text
        text = markdown_response
        text.lower()

        # Scan for backtick-wrapped items and map to actions based on surrounding context
        # Pattern: find backtick items and check the surrounding text for action verbs
        backtick_pattern = re.compile(r'`([^`]+)`')

        for match in backtick_pattern.finditer(text):
            item = match.group(1).strip()
            if not item or len(item) < 2:
                continue

            # Get surrounding context (100 chars before and after)
            start = max(0, match.start() - 100)
            end = min(len(text), match.end() + 100)
            context = text[start:end].lower()
            context_before = text[start:match.start()].lower()

            # Skip generic items
            if item.lower() in ('@everyone', '@here', 'true', 'false', 'none', 'null', 'text', 'voice', 'category'):
                continue

            # Determine action based on context
            step = None

            # DELETE role/channel
            if any(w in context_before for w in ['delete', 'remove', 'drop', 'get rid of', 'eliminate']):
                if item.startswith('@') or 'role' in context:
                    key = f"delete_role:{item}"
                    if key not in seen:
                        seen.add(key)
                        step = {"tool": "delete_role", "params": {"role": item.lstrip('@')}, "reasoning": f"Delete role {item}"}
                elif item.startswith('#') or 'channel' in context:
                    key = f"delete_channel:{item}"
                    if key not in seen:
                        seen.add(key)
                        step = {"tool": "delete_channel", "params": {"channel": item.lstrip('#')}, "reasoning": f"Delete channel {item}"}

            # CREATE role/channel
            elif any(w in context_before for w in ['create', 'add', 'make', 'new']):
                if 'role' in context or item.startswith('@'):
                    key = f"create_role:{item}"
                    if key not in seen:
                        seen.add(key)
                        step = {"tool": "create_role", "params": {"role": item.lstrip('@')}, "reasoning": f"Create role {item}"}
                elif 'channel' in context or item.startswith('#'):
                    key = f"create_channel:{item}"
                    if key not in seen:
                        seen.add(key)
                        step = {"tool": "create_channel", "params": {"channel": item.lstrip('#')}, "reasoning": f"Create channel {item}"}

            # RENAME
            elif any(w in context_before for w in ['rename', 'change name', 'update name']):
                key = f"rename:{item}"
                if key not in seen:
                    seen.add(key)
                    step = {"tool": "edit_role", "params": {"role": item.lstrip('@')}, "reasoning": f"Rename {item}"}

            # RESTRICT/LOCK (permission changes)
            elif any(w in context for w in ['restrict', 'lock', 'deny', 'block', 'remove access', 'revoke']):
                if item.startswith('#') or 'channel' in context:
                    key = f"restrict:{item}"
                    if key not in seen:
                        seen.add(key)
                        step = {"tool": "set_permissions", "params": {"channel": item.lstrip('#'), "role": "@everyone", "send_messages": "false"}, "reasoning": f"Restrict {item}"}

            # UNLOCK/OPEN
            elif any(w in context for w in ['unlock', 'open', 'allow', 'grant']):
                if item.startswith('#') or 'channel' in context:
                    key = f"unlock:{item}"
                    if key not in seen:
                        seen.add(key)
                        step = {"tool": "set_permissions", "params": {"channel": item.lstrip('#'), "role": "@everyone", "send_messages": "true"}, "reasoning": f"Unlock {item}"}

            # MERGE (delete the second one)
            elif 'merge' in context_before:
                key = f"merge:{item}"
                if key not in seen:
                    seen.add(key)
                    step = {"tool": "delete_role", "params": {"role": item.lstrip('@')}, "reasoning": f"Merge (delete) {item}"}

            if step:
                steps.append(step)

        # Also scan for lines that mention specific channels/roles with action verbs
        # even without backticks
        lines = text.splitlines()
        for line in lines:
            line_lower = line.lower().strip()
            if not line:
                continue
            # Only skip markdown headers like "### Header", not instructions
            if line.startswith('#') and ' ' in line and line.split(' ', 1)[1][0:1].isupper():
                continue

            # "Delete: `Item1`, `Item2`, `Item3`" and "Delete role: X, Y"
            dm = re.search(r'(?:delete|remove)\s*[:：-]\s*(.+)', line, re.IGNORECASE)
            if dm:
                items_text = dm.group(1)
                # Extract backticked, quoted, or plain comma-separated items
                items = re.findall(r"`([^`]+)`|\"([^\"]+)\"|'([^']+)'|(\b[\w][\w\s-]*?\b)(?:,|\s+\(|\s*$)", items_text)
                flat_items = []
                for item_tuple in items:
                    item = next((x for x in item_tuple if x), "").strip()
                    if item and item.lower() not in ('role', 'channel'):
                        flat_items.append(item)
                for item in flat_items:
                    is_role = item.startswith('@') or ('role' in line_lower and 'channel' not in line_lower)
                    tool = "delete_role" if is_role else "delete_channel"
                    param = "role" if is_role else "channel"
                    stripped = item.lstrip('@#').strip()
                    key = f"delete_{'role' if is_role else 'channel'}:{stripped}"
                    if key not in seen:
                        seen.add(key)
                        steps.append({"tool": tool, "params": {param: stripped}, "reasoning": f"Delete {item}"})
                continue

            # "Rename: `A` → `B`" or "Rename `A` to `B`"
            rm = re.search(r'rename\s*[:：-]?\s*`([^`]+)`\s*(?:→|->|to)\s*`([^`]+)`', line, re.IGNORECASE)
            if not rm:
                rm = re.search(r'rename\s+`([^`]+)`\s*(?:→|->|to)\s*`([^`]+)`', line, re.IGNORECASE)
            if rm:
                old_name, new_name = rm.groups()
                key = f"rename:{old_name}:{new_name}"
                if key not in seen:
                    seen.add(key)
                    param = "role" if old_name.startswith('@') else "channel" if old_name.startswith('#') else "role"
                    steps.append({
                        "tool": "edit_role" if param == "role" else "edit_channel",
                        "params": {param: old_name.lstrip('@#'), "name": new_name},
                        "reasoning": f"Rename {old_name} to {new_name}",
                    })
                continue

            # Channel permission lines: "#channel: only Role can Send Messages" / "#channel: deny @everyone Send Messages"
            cm = re.search(r'`?#([\w][\w\s-]*?)`?\s*[:：-]\s*(?:only|allow|deny|restrict|lock)?\s*`?(@?[\w][\w\s-]*?)`?\s*(?:can\s+)?`?(\w[\w\s]*)`?', line, re.IGNORECASE)
            if cm:
                channel, role, perm_text = cm.groups()
                perms = re.findall(r'\b(\w+)\b', perm_text)
                allow = []
                deny = []
                negative = any(w in line_lower for w in ['deny', 'restrict', 'lock', 'cannot', 'can\'t', 'only'])
                for p in perms:
                    pl = p.lower()
                    if pl in ('send', 'messages'):
                        pl = 'send_messages'
                    if pl in ('view', 'channel'):
                        pl = 'view_channel'
                    if pl in ('manage', 'messages'):
                        pl = 'manage_messages'
                    if negative:
                        deny.append(pl)
                    else:
                        allow.append(pl)
                if allow or deny:
                    key = f"set_perms:{channel}:{role}:{'|'.join(allow)}:{'|'.join(deny)}"
                    if key not in seen:
                        seen.add(key)
                        steps.append({
                            "tool": "set_permissions",
                            "params": {"channel": channel.strip(), "role": role.strip().lstrip('@'), "allow": allow, "deny": deny},
                            "reasoning": f"Set permissions on {channel}",
                        })
                continue

            # "Delete role X" / "Delete channel X" without backticks (legacy)
            dm = re.search(r'(?:delete|remove)\s+(?:the\s+)?(?:role|channel)\s+(\w[\w\s-]*?)(?:\s*$|\s*[,.])', line, re.IGNORECASE)
            if dm:
                name = dm.group(1).strip()
                is_role = 'role' in line_lower and 'channel' not in line_lower
                key = f"delete_{'role' if is_role else 'channel'}:{name}"
                if key not in seen:
                    seen.add(key)
                    tool = "delete_role" if is_role else "delete_channel"
                    param = "role" if is_role else "channel"
                    steps.append({"tool": tool, "params": {param: name}, "reasoning": f"Delete {name}"})

            # "Set permissions for X in Y" (legacy)
            pm = re.search(r'set\s+permissions?\s+(?:for|on)\s+(.+?)\s+in\s+(.+)', line, re.IGNORECASE)
            if pm:
                role, channel = pm.groups()
                key = f"set_perms:{role}:{channel}"
                if key not in seen:
                    seen.add(key)
                    steps.append({
                        "tool": "set_permissions",
                        "params": {"channel": channel.strip().strip('#`"\''), "role": role.strip().strip('@`"\''), "send_messages": "false"},
                        "reasoning": f"Set permissions for {role} in {channel}",
                    })

        if steps:
            logger.info(f"[LLM-Planner] Extracted {len(steps)} actions from markdown scan")

        return {
            "analysis": f"Extracted {len(steps)} actions from markdown scan",
            "reasoning": "Markdown direct scan (no LLM)",
            "steps": steps,
        }

    async def execute_plan(self, guild, plan: dict, ctx=None,
                          requester_name: str = "") -> list[ExecutionResult]:
        """
        Execute LLM-generated plan.

        Args:
            guild: Discord guild object
            plan: LLM-generated plan with tool calls
            ctx: Optional Discord context for progress updates
            requester_name: Name of user who made request

        Returns:
            List of execution results
        """
        steps = plan.get("steps", [])

        if not steps:
            if self.verbose:
                logger.info("[LLM-Planner] No steps in plan, nothing to execute")

            return []

        if self.verbose:
            logger.info(f"[LLM-Planner] Executing {len(steps)} steps")


        results = []

        for i, step in enumerate(steps, 1):
            tool_name = step.get("tool", "unknown")
            params = step.get("params", {})
            reasoning = step.get("reasoning", "No reasoning provided")

            if self.verbose:
                logger.info(f"[LLM-Planner] Step {i}/{len(steps)}: {tool_name}")


            # Get tool from registry
            tool_info = self.registry.get_tool(tool_name)

            if not tool_info:
                if self.verbose:
                    logger.error(f"[LLM-Planner] ERROR: Unknown tool '{tool_name}'")


                results.append(ExecutionResult(
                    tool_name=tool_name,
                    success=False,
                    error=f"Unknown tool: {tool_name}",
                    reasoning=reasoning
                ))
                continue

            # Execute tool with LLM-chosen parameters
            try:
                # Add guild as first parameter (all tools need it)
                result = await tool_info.function(guild, **params)

                results.append(ExecutionResult(
                    tool_name=tool_name,
                    success=result.success,
                    detail=result.detail,
                    error=result.error,
                    reasoning=reasoning
                ))

                if self.verbose:
                    status = "✅" if result.success else "❌"
                    logger.info(f"[LLM-Planner] {status} {tool_name}: {result.detail or result.error}")


            except Exception as e:
                if self.verbose:
                    logger.error(f"[LLM-Planner] ❌ Exception executing {tool_name}: {e}")


                results.append(ExecutionResult(
                    tool_name=tool_name,
                    success=False,
                    error=str(e),
                    reasoning=reasoning
                ))

        return results

    async def execute_with_self_correction(self, guild, user_request: str,
                                          server_state: dict, ctx=None) -> dict:
        """
        Execute plan with LLM-driven self-correction.

        If steps fail, ask the LLM to FIX its own plan and retry.
        This is true autonomous error recovery.

        Returns:
            dict with results and correction history
        """
        correction_history = []

        # Track failures from the previous iteration so the LLM can be asked
        # to fix them on the next pass. Initialized empty so the first
        # iteration's else-branch (line ~454) never references an undefined
        # `failures` symbol.
        failures: list = []

        for attempt in range(self.max_retries):
            if self.verbose:
                logger.info(f"[LLM-Planner] Attempt {attempt + 1}/{self.max_retries}")


            # Generate plan
            if attempt == 0:
                plan = await self.generate_plan(user_request, server_state, guild.id)
            else:
                # Ask LLM to fix previous failures
                plan = await self._generate_corrected_plan(
                    original_request=user_request,
                    previous_plan=plan,
                    failures=failures,
                    server_state=server_state
                )

            # Execute plan
            results = await self.execute_plan(guild, plan, ctx)

            # Check for failures
            failures = [r for r in results if not r.success]

            if not failures:
                # Success! No need to retry
                if self.verbose:
                    logger.info("[LLM-Planner] ✅ Plan executed successfully")


                return {
                    "success": True,
                    "plan": plan,
                    "results": results,
                    "attempts": attempt + 1,
                    "correction_history": correction_history
                }

            # Record correction attempt
            correction_history.append({
                "attempt": attempt + 1,
                "failures": len(failures),
                "errors": [f.error for f in failures]
            })

            if self.verbose:
                logger.warning(f"[LLM-Planner] ⚠️ {len(failures)} failures, will retry")


        # Max retries reached
        if self.verbose:
            logger.error("[LLM-Planner] ❌ Max retries reached, giving up")


        return {
            "success": False,
            "plan": plan,
            "results": results,
            "attempts": self.max_retries,
            "correction_history": correction_history,
            "error": f"{len(failures)} steps failed after {self.max_retries} attempts"
        }

    async def _generate_corrected_plan(self, original_request: str, previous_plan: dict,
                                       failures: list[ExecutionResult], server_state: dict) -> dict:
        """
        Ask LLM to fix its own mistakes.

        This is self-correction - the LLM learns from execution results.
        """

        # Build correction prompt
        failure_details = []
        for fail in failures:
            failure_details.append({
                "tool": fail.tool_name,
                "error": fail.error,
                "reasoning": fail.reasoning
            })

        correction_prompt = f"""Your previous plan had some failures. Please generate a CORRECTED plan.

# ORIGINAL USER REQUEST
"{original_request}"

# YOUR PREVIOUS PLAN
{json.dumps(previous_plan, indent=2)}

# WHAT FAILED
{json.dumps(failure_details, indent=2)}

# YOUR TASK
Analyze why these steps failed and generate a CORRECTED plan that:
1. Fixes the errors (wrong parameters, missing prerequisites, etc.)
2. Skips steps that are impossible (e.g., deleting non-existent items)
3. Uses alternative approaches if needed
4. Adds missing dependencies

Think about:
- Did I use the wrong parameter values?
- Did I forget to create something first? (e.g., category before channel)
- Is the item already existing? (can't create duplicate)
- Is the item missing? (can't delete non-existent)

Generate a CORRECTED JSON plan that will succeed. Output ONLY valid JSON.
"""

        messages = [
            {
                "role": "system",
                "content": "You are a self-correcting AI. Learn from failures and fix your mistakes."
            },
            {
                "role": "user",
                "content": correction_prompt
            }
        ]

        try:
            response = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.llm.chat(messages, max_tokens=2048, temperature=0.3)
            )

            corrected_plan = self._parse_plan_response(response)

            if self.verbose:
                logger.info(f"[LLM-Planner] Generated corrected plan with {len(corrected_plan.get('steps', []))} steps")


            return corrected_plan

        except Exception as e:
            if self.verbose:
                logger.error(f"[LLM-Planner] ERROR generating correction: {e}")


            # Return original plan if correction fails
            return previous_plan


def create_planner(llm, discord_tools) -> LLMPlanner:
    """
    Convenience function to create an LLM planner.

    Args:
        llm: Local LLM instance
        discord_tools: DiscordManagementTools instance

    Returns:
        Configured LLMPlanner
    """
    from .tool_registry import create_registry

    registry = create_registry(discord_tools)
    return LLMPlanner(llm, registry, discord_tools)
