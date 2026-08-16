"""Template save/load/apply handlers."""
from __future__ import annotations

import logging
from typing import Any

from ..config import CHUNK_SIZE, CONFIRM_TIMEOUT

logger = logging.getLogger("azure.discord.message")


async def _handle_template(message: Any, params: dict[str, str]) -> None:
    """Handle template save, load, and list operations.

    Manages server configuration templates — saving the current server
    state as a template, loading and applying a saved template, or
    listing available templates.

    Args:
        message: The Discord message triggering the command.
        params: Parsed parameters containing 'template_action' and
                'template_name' keys.

    Supported actions:
        - list (or auto): Show all saved and built-in templates.
        - save: Save the current server state as a named template.
        - load: Apply a saved template to the server (requires confirmation).
    """
    from bot.context import ctx

    from .llm_handler import _llm_response
    if not ctx.mgmt_tools:
        msg = await _llm_response("Tools not available for template operation.", "❌ Management tools not available.")
        await message.channel.send(msg)
        return
    if not message.guild:
        msg = await _llm_response("Templates only work in servers.", "❌ Templates only work in servers.")
        await message.channel.send(msg)
        return

    action = params.get("template_action", "list")
    template_name = params.get("template_name", "")

    if action == "list" or action == "auto":
        templates = ctx.mgmt_tools.templates.list_templates()
        if not templates:
            msg = await _llm_response(
                "No templates available. Suggest built-in templates: gaming, community, minimal.",
                "📦 No templates saved yet. Built-in templates: **gaming**, **community**, **minimal**.\nSay `apply gaming template` to use one."
            )
            await message.channel.send(msg[:CHUNK_SIZE])
            return
        lines = [f"**{t['name']}** — {t['description']}" for t in templates]
        msg = await _llm_response(
            f"List these templates: {', '.join(t['name'] for t in templates)}",
            "📦 **Server Templates**\n" + "\n".join(f"{i+1}. {line}" for i, line in enumerate(lines))
        )
        await message.channel.send(msg[:CHUNK_SIZE])
        return

    member = message.guild.get_member(message.author.id)
    is_owner = message.guild.owner_id == message.author.id
    is_admin = member and member.guild_permissions.administrator

    if action == "save":
        if not template_name:
            msg = await _llm_response("No template name provided for save.", "❌ Please provide a template name. Example: `save this as 'gaming'`")
            await message.channel.send(msg)
            return
        if not (is_owner or is_admin):
            msg = await _llm_response(f"User {message.author.name} lacks permission to save templates.", "⚠️ Only owners/admins can save templates.")
            await message.channel.send(msg)
            return
        path = await ctx.mgmt_tools.templates.save_template(message.guild, template_name, f"Saved by {message.author.display_name}")
        msg = await _llm_response(f"Template '{template_name}' saved at {path}.", f"💾 Template **'{template_name}'** saved!\n`{path}`")
        await message.channel.send(msg[:CHUNK_SIZE])
        return

    if action == "load":
        if not template_name:
            msg = await _llm_response("No template name provided.", "❌ Please provide a template name. Example: `apply gaming template`")
            await message.channel.send(msg)
            return
        if not (is_owner or is_admin):
            msg = await _llm_response(f"User {message.author.name} lacks permission to apply templates.", "⚠️ Only owners/admins can apply templates.")
            await message.channel.send(msg)
            return

        template = ctx.mgmt_tools.templates.load_template(template_name)
        if not template:
            msg = await _llm_response(
                f"Template '{template_name}' not found. Suggest alternatives.",
                f"❌ Template **'{template_name}'** not found.\nUse `list templates` to see available ones."
            )
            await message.channel.send(msg[:CHUNK_SIZE])
            return

        plan = ctx.mgmt_tools.templates.to_plan(template_name)
        total = len(plan.get("steps", []))
        if total == 0:
            msg = await _llm_response(f"Template '{template_name}' has no steps.", f"❌ Template **'{template_name}'** has no steps.")
            await message.channel.send(msg[:CHUNK_SIZE])
            return

        confirm_prompt = await _llm_response(
            f"Template '{template_name}' has {total} steps. Ask the user to confirm applying it.",
            f"📋 **Template '{template_name}'** has {total} steps.\nReply **yes** to apply it."
        )
        await message.channel.send(confirm_prompt[:CHUNK_SIZE])

        def check_reply(m):
            return m.author == message.author and m.channel == message.channel

        from .message_handler import _clear_pending_confirmation, _set_pending_confirmation
        _set_pending_confirmation(str(message.author.id), str(message.channel.id))
        try:
            reply = await ctx.bot.wait_for("message", timeout=CONFIRM_TIMEOUT, check=check_reply)
            text_lower = reply.content.lower().strip()
            if text_lower in ("yes", "go", "do it", "ok", "sure", "yep", "yeah"):
                await ctx.mgmt_tools.execute_plan(message.guild, plan, message.channel,
                                               requester_name=message.author.display_name,
                                               requester_id=message.author.id)
            else:
                cancel_msg = await _llm_response("Template application cancelled by user.", "🚫 Template application cancelled.")
                await message.channel.send(cancel_msg)
        except TimeoutError:
            timeout_msg = await _llm_response("Template application timed out.", "⏰ No response. Cancelled.")
            await message.channel.send(timeout_msg)
        finally:
            _clear_pending_confirmation(str(message.author.id), str(message.channel.id))
