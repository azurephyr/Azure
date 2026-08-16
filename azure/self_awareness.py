"""
Self-Awareness System for Azure

Allows the bot to:
1. Read and understand its own code
2. Edit configuration files (.env)
3. Modify its own behavior safely
4. Apply changes with validation and rollback

Inspired by JARVIS - the bot knows itself and can self-configure.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("azure.self_awareness")


class SelfAwareness:
    """System for self-code modification and configuration."""

    def __init__(self, project_root: Path = None):
        if project_root is None:
            project_root = Path(__file__).parent.parent
        self.project_root = project_root
        self.env_path = project_root / ".env"
        self.backup_dir = project_root / "logs" / "self_edits"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def backup_file(self, file_path: Path) -> Path:
        """Create timestamped backup before editing."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"{file_path.name}.{timestamp}.backup"
        shutil.copy2(file_path, backup_path)
        return backup_path

    def read_env(self) -> dict[str, str]:
        """Read current .env configuration."""
        if not self.env_path.exists():
            return {}

        config = {}
        with open(self.env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    config[key.strip()] = value.strip()
        return config

    def write_env(self, config: dict[str, str]) -> bool:
        """Write configuration to .env file."""
        try:
            # Backup first
            if self.env_path.exists():
                self.backup_file(self.env_path)

            # Read current file to preserve comments and structure
            if self.env_path.exists():
                with open(self.env_path, encoding='utf-8') as f:
                    lines = f.readlines()
            else:
                lines = []

            # Update or add config values
            updated_lines = []
            updated_keys = set()

            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith('#') and '=' in stripped:
                    key = stripped.split('=', 1)[0].strip()
                    if key in config:
                        # Update existing value
                        updated_lines.append(f"{key}={config[key]}\n")
                        updated_keys.add(key)
                    else:
                        updated_lines.append(line)
                else:
                    # Preserve comments and empty lines
                    updated_lines.append(line)

            # Add new keys that weren't in file
            for key, value in config.items():
                if key not in updated_keys:
                    updated_lines.append("\n# Added by self-awareness system\n")
                    updated_lines.append(f"{key}={value}\n")

            # Write back
            with open(self.env_path, 'w', encoding='utf-8') as f:
                f.writelines(updated_lines)

            return True
        except Exception as e:
            logger.error(f"[self_awareness] Failed to write .env: {e}")

            return False

    def update_config(self, key: str, value: str) -> bool:
        """Update a single configuration value."""
        config = self.read_env()
        config[key] = value
        return self.write_env(config)

    def parse_access_control_intent(self, user_input: str, user_id: str) -> dict | None:
        """
        Parse natural language access control requests.

        Examples:
        - "only let me talk to you" → owner_only mode
        - "let anyone talk to you" → anyone mode
        - "only respond in DMs" → dm_only mode
        - "let @User also talk to you" → add to allowed users
        """
        lower = user_input.lower()

        # Only me / owner only
        if any(phrase in lower for phrase in [
            "only let me talk",
            "only i can talk",
            "ignore everyone else",
            "only respond to me",
            "private mode",
        ]):
            return {
                "action": "set_chat_mode",
                "mode": "owner_only",
                "reason": "User requested exclusive access",
            }

        # Anyone
        if any(phrase in lower for phrase in [
            "let anyone talk",
            "everyone can talk",
            "public mode",
            "respond to everyone",
        ]):
            return {
                "action": "set_chat_mode",
                "mode": "anyone",
                "reason": "User enabled public access",
            }

        # DM only
        if any(phrase in lower for phrase in [
            "only in dm",
            "only direct message",
            "private messages only",
        ]):
            return {
                "action": "set_chat_mode",
                "mode": "dm_only",
                "reason": "User enabled DM-only mode",
            }

        # Mention only
        if any(phrase in lower for phrase in [
            "only when mentioned",
            "only @",
            "mention only",
        ]):
            return {
                "action": "set_chat_mode",
                "mode": "mention_only",
                "reason": "User enabled mention-only mode",
            }

        # Add specific user
        # Extract user ID from @mention or raw ID
        if "let" in lower and "talk" in lower:
            # Look for user ID pattern
            user_id_match = re.search(r'<@!?(\d+)>|(\d{17,20})', user_input)
            if user_id_match:
                target_user_id = user_id_match.group(1) or user_id_match.group(2)
                return {
                    "action": "add_allowed_user",
                    "user_id": target_user_id,
                    "reason": f"User granted access to {target_user_id}",
                }

        return None

    def apply_access_control(self, intent: dict) -> tuple[bool, str]:
        """Apply access control changes to .env."""
        action = intent.get("action")

        if action == "set_chat_mode":
            mode = intent.get("mode")
            success = self.update_config("AZURE_CHAT_MODE", mode)
            if success:
                return True, f"✅ Chat mode set to `{mode}`. Configuration updated."
            else:
                return False, "❌ Failed to update configuration file."

        elif action == "add_allowed_user":
            user_id = intent.get("user_id")
            config = self.read_env()

            # Get current allowed users
            current = config.get("AZURE_ALLOWED_USERS", "")
            user_list = [u.strip() for u in current.split(",") if u.strip()]

            if user_id not in user_list:
                user_list.append(user_id)

                # Update config
                config["AZURE_CHAT_MODE"] = "specific_users"
                config["AZURE_ALLOWED_USERS"] = ",".join(user_list)

                success = self.write_env(config)
                if success:
                    return True, f"✅ Added user {user_id} to allowed list. Chat mode set to `specific_users`."
                else:
                    return False, "❌ Failed to update configuration."
            else:
                return True, f"ℹ️  User {user_id} is already in the allowed list."

        return False, "❌ Unknown action."

    def parse_model_intent(self, user_input: str) -> dict | None:
        """
        Parse model configuration requests.

        Examples:
        - "use the larger model" → switch to 7B
        - "use faster model" → switch to 3B
        - "enable cognitive mode" → turn on cognitive pipeline
        """
        lower = user_input.lower()

        # Cognitive mode
        if "enable cognitive" in lower or "turn on cognitive" in lower:
            return {
                "action": "set_config",
                "key": "AZURE_COGNITIVE_MODE",
                "value": "1",
                "reason": "User enabled cognitive pipeline",
            }

        if "disable cognitive" in lower or "turn off cognitive" in lower:
            return {
                "action": "set_config",
                "key": "AZURE_COGNITIVE_MODE",
                "value": "0",
                "reason": "User disabled cognitive pipeline",
            }

        # Moderation phase
        if "dry run" in lower or "test mode" in lower:
            return {
                "action": "set_config",
                "key": "AZURE_MODERATION_PHASE",
                "value": "dry_run",
                "reason": "User set moderation to dry run",
            }

        return None

    def get_current_config(self) -> str:
        """Get human-readable current configuration."""
        config = self.read_env()

        chat_mode = config.get("AZURE_CHAT_MODE", "anyone")
        cognitive = config.get("AZURE_COGNITIVE_MODE", "1")
        moderation = config.get("AZURE_MODERATION_PHASE", "dry_run")
        model = config.get("AZURE_MODEL_PATH", "models/qwen2.5-3b-instruct-q4_k_m.gguf")

        lines = [
            "📋 **Current Configuration:**",
            f"• **Chat Mode:** `{chat_mode}`",
        ]

        if chat_mode == "specific_users":
            allowed = config.get("AZURE_ALLOWED_USERS", "")
            user_count = len([u for u in allowed.split(",") if u.strip()])
            lines.append(f"• **Allowed Users:** {user_count} user(s)")

        lines.extend([
            f"• **Cognitive Mode:** {'✅ Enabled' if cognitive == '1' else '❌ Disabled'}",
            f"• **Moderation:** `{moderation}`",
            f"• **Model:** `{model.split('/')[-1]}`",
        ])

        return "\n".join(lines)

    def read_own_code(self, module_name: str) -> str | None:
        """Read a module's source code."""
        try:
            module_path = self.project_root / "azure" / f"{module_name}.py"
            if module_path.exists():
                with open(module_path, encoding='utf-8') as f:
                    return f.read()
            return None
        except Exception as e:
            logger.error(f"[self_awareness] Failed to read {module_name}: {e}")

            return None

    def understand_codebase(self) -> dict[str, any]:
        """Provide high-level understanding of the codebase structure."""
        structure = {
            "core_modules": [
                "agent.py - Main AI agent with LLM and memory",
                "local_llm.py - Local model inference",
                "discord_persona.py - Response formatting",
            ],
            "cognition": [
                "cognitive_pipeline.py - 10-phase reasoning",
                "mode_classifier.py - Intent detection",
                "planning_engine.py - Task planning",
                "reasoning_engine.py - Deep reasoning",
            ],
            "moderation": [
                "engine.py - Moderation pipeline",
                "behavioral.py - User behavior analysis",
                "risk.py - Risk assessment",
            ],
            "discord_tools": [
                "discord_tools_expanded.py - Server management",
                "agentic_tools.py - Tool registry",
            ],
            "self_awareness": [
                "self_awareness.py - This module (self-configuration)",
                "jarvis_interface.py - Beautiful terminal UI",
            ],
        }

        return structure

    def can_safely_edit(self, intent: str) -> tuple[bool, str]:
        """
        Determine if a configuration change is safe to apply.

        Returns:
            (is_safe, reason)
        """
        # .env file edits are always safe (can be reverted)
        safe_patterns = [
            "chat mode",
            "access control",
            "allowed users",
            "cognitive mode",
            "moderation phase",
            "configuration",
            "settings",
        ]

        intent_lower = intent.lower()

        if any(pattern in intent_lower for pattern in safe_patterns):
            return True, ".env configuration changes are safe and reversible"

        # Code edits are NOT safe (need human approval)
        dangerous_patterns = [
            "edit code",
            "modify code",
            "change function",
            "delete file",
        ]

        if any(pattern in intent_lower for pattern in dangerous_patterns):
            return False, "Code modifications require explicit human approval for safety"

        return False, "Unknown intent - defaulting to safe mode"

    def log_self_edit(self, action: str, details: str, success: bool):
        """Log self-edits for auditing."""
        log_file = self.backup_dir / "edit_log.jsonl"
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "details": details,
            "success": success,
        }

        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + "\n")


# Global instance
self_awareness = SelfAwareness()


def handle_self_config_request(user_input: str, user_id: str) -> str | None:
    """
    Handle natural language configuration requests.

    Returns response message or None if not a config request.
    """
    # Check for access control
    access_intent = self_awareness.parse_access_control_intent(user_input, user_id)
    if access_intent:
        is_safe, safety_reason = self_awareness.can_safely_edit(user_input)
        if is_safe:
            success, message = self_awareness.apply_access_control(access_intent)
            if success:
                self_awareness.log_self_edit(
                    action=access_intent["action"],
                    details=access_intent["reason"],
                    success=True
                )
            return message
        else:
            return f"⚠️  I cannot make that change: {safety_reason}"

    # Check for model config
    model_intent = self_awareness.parse_model_intent(user_input)
    if model_intent and model_intent.get("action") == "set_config":
        is_safe, safety_reason = self_awareness.can_safely_edit(user_input)
        if is_safe:
            success = self_awareness.update_config(
                model_intent["key"],
                model_intent["value"]
            )
            if success:
                self_awareness.log_self_edit(
                    action="set_config",
                    details=model_intent["reason"],
                    success=True
                )
                return f"✅ Configuration updated: {model_intent['key']} = {model_intent['value']}"
            else:
                return "❌ Failed to update configuration."
        else:
            return f"⚠️  I cannot make that change: {safety_reason}"

    # Show current config
    if any(phrase in user_input.lower() for phrase in [
        "show config",
        "current settings",
        "what's your config",
        "who can talk to you",
    ]):
        return self_awareness.get_current_config()

    return None
