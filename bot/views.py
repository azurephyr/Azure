"""Discord UI views for the Azure Discord bot."""

from __future__ import annotations

import asyncio

import discord

from bot.config import CHUNK_SIZE


class PlanExecutionView(discord.ui.View):
    """Discord UI view for confirming or cancelling cognitive pipeline plan execution.

    Displays Execute and Cancel buttons allowing the original requester to
    approve or reject a proposed plan.  Only the user who triggered the plan
    can interact with the buttons.

    Args:
        state: The cognitive pipeline state containing the plan to execute.
        message: The Discord message that triggered the plan.
        user: Display name of the requesting user.
        is_directed: Whether the message was directed at the bot.
        is_dm: Whether the message was sent in a DM.
        mentioned: Whether the bot was mentioned.
        server_name: Name of the server where the message was sent.
    """

    def __init__(self, state, message: discord.Message, user: str, is_directed: bool, is_dm: bool, mentioned: bool, server_name: str):
        super().__init__(timeout=300)
        self.state = state
        self.message = message
        self.user = user
        self.is_directed = is_directed
        self.is_dm = is_dm
        self.mentioned = mentioned
        self.server_name = server_name

    @discord.ui.button(label="🚀 Execute Plan", style=discord.ButtonStyle.success)
    async def execute_plan(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle the Execute Plan button click.

        Verifies the interacting user is the original requester, disables
        the buttons, and runs the plan through the cognitive pipeline.

        Args:
            interaction: The Discord interaction from the button click.
            button: The button that was clicked.
        """
        from bot.context import ctx
        from bot.handlers.llm_handler import _llm_response

        if interaction.user.id != self.message.author.id:
            msg = await _llm_response(
                f"User {interaction.user.name} tried to execute someone else's plan. Deny them.",
                "Only the requester can execute this plan.",
                max_tokens=30
            )
            await interaction.response.send_message(msg, ephemeral=True)
            return

        # Disable buttons
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

        # Approve the plan in the state
        self.state.needs_confirmation = False
        self.state.plan.requires_confirmation = False
        self.state.execution_result = None
        self.state.response = None
        self.state.raw_message = "yes"

        exec_msg = await _llm_response("Plan execution starting.", "🚀 **Executing Plan...** Please wait.", max_tokens=20)
        await interaction.followup.send(exec_msg)

        if ctx.cognitive_pipeline:
            try:
                loop = asyncio.get_running_loop()
                def _run_exec():
                    return ctx.cognitive_pipeline._execute(self.state, {}, self.message.author.guild_permissions.administrator if hasattr(self.message.author, 'guild_permissions') else False, self.message.guild is not None)

                response, success = await loop.run_in_executor(None, _run_exec)

                if success:
                    result_msg = await _llm_response(
                        f"Plan executed successfully. Result: {response[:200]}",
                        f"✅ **Plan Execution Complete**\n{response[:CHUNK_SIZE]}"
                    )
                    await interaction.channel.send(result_msg[:CHUNK_SIZE])
                else:
                    fail_msg = await _llm_response(
                        f"Plan execution failed. Error: {response[:200]}",
                        f"❌ **Plan Execution Failed**\n{response[:CHUNK_SIZE]}"
                    )
                    await interaction.channel.send(fail_msg[:CHUNK_SIZE])
            except Exception as e:
                err_msg = await _llm_response(f"Execution error: {e}", f"⚠️ **Execution Error:** {e}")
                await interaction.channel.send(err_msg[:CHUNK_SIZE])

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_plan(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle the Cancel Plan button click.

        Verifies the interacting user is the original requester, disables
        the buttons, and sends a cancellation confirmation.

        Args:
            interaction: The Discord interaction from the button click.
            button: The button that was clicked.
        """
        from bot.handlers.llm_handler import _llm_response

        if interaction.user.id != self.message.author.id:
            msg = await _llm_response(
                f"User {interaction.user.name} tried to cancel someone else's plan. Deny them.",
                "Only the requester can cancel this plan.",
                max_tokens=30
            )
            await interaction.response.send_message(msg, ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        cancel_msg = await _llm_response("Plan cancellation confirmed.", "❌ Plan execution cancelled.", max_tokens=20)
        await interaction.followup.send(cancel_msg)
