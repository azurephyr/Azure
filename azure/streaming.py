"""
Azure Discord Bot - Response Streaming Support

Provides token-by-token streaming from LLM with live Discord message updates.
This creates a more engaging user experience by showing responses as they're generated.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass

logger = logging.getLogger("azure.streaming")


@dataclass
class StreamChunk:
    """Represents a single chunk of streamed text."""
    text: str
    timestamp: float
    is_final: bool = False
    total_tokens: int = 0


class ResponseStreamer:
    """Manages streaming responses from LLM to Discord with live updates."""

    def __init__(
        self,
        min_update_interval: float = 0.5,
        chunk_size: int = 10,
        max_message_length: int = 2000
    ) -> None:
        """Initialize response streamer.

        Args:
            min_update_interval: Minimum seconds between Discord message edits (avoid rate limits)
            chunk_size: Number of tokens to accumulate before updating
            max_message_length: Maximum Discord message length
        """
        self.min_update_interval = min_update_interval
        self.chunk_size = chunk_size
        self.max_message_length = max_message_length
        self._last_update_time = 0.0

    async def stream_to_discord(
        self,
        message,  # Discord message object
        llm_stream: AsyncGenerator[str, None],
        prefix: str = "",
        suffix: str = ""
    ) -> str:
        """Stream LLM response with live Discord message updates.

        Args:
            message: Discord message to edit with updates
            llm_stream: Async generator yielding token strings
            prefix: Text to prepend to streamed content
            suffix: Text to append to streamed content (e.g., "✍️ typing...")

        Returns:
            Final complete response text
        """
        accumulated = ""
        chunk_buffer = ""
        last_update = time.time()
        token_count = 0

        try:
            async for token in llm_stream:
                accumulated += token
                chunk_buffer += token
                token_count += 1

                # Update Discord message periodically
                now = time.time()
                should_update = (
                    len(chunk_buffer) >= self.chunk_size or
                    (now - last_update) >= self.min_update_interval
                )

                if should_update:
                    # Truncate if needed
                    display_text = accumulated
                    max_content = self.max_message_length - len(prefix) - len(suffix)
                    if max_content < 3:
                        max_content = 3
                    if len(display_text) > max_content:
                        display_text = display_text[:max_content - 3] + "..."

                    full_text = f"{prefix}{display_text}{suffix}"

                    try:
                        await message.edit(content=full_text)
                        last_update = now
                        chunk_buffer = ""
                    except Exception as e:
                        logger.warning(f"[streaming] Failed to update message: {e}")

            # Final update without suffix
            final_text = accumulated
            final_max = self.max_message_length - len(prefix)
            # Guard against a long prefix producing a negative slice bound,
            # which would silently corrupt/empty the output.
            if final_max < 3:
                final_max = 3
            if len(final_text) > final_max:
                final_text = final_text[:final_max - 3] + "..."

            try:
                await message.edit(content=f"{prefix}{final_text}")
            except Exception as e:
                logger.error(f"[streaming] Failed final update: {e}")

            logger.info(f"[streaming] Completed: {token_count} tokens, {len(accumulated)} chars")
            return accumulated

        except Exception as e:
            logger.error(f"[streaming] Error during stream: {e}")
            return accumulated

    async def stream_llm_chat(
        self,
        llm,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.7
    ) -> AsyncGenerator[str, None]:
        """Convert LLM chat to async generator for streaming.

        Args:
            llm: LLM instance with chat method
            messages: Chat messages list
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature

        Yields:
            Token strings as they're generated
        """
        try:
            # Check if LLM supports streaming
            if hasattr(llm, 'chat_stream'):
                # Use native streaming if available
                async for token in llm.chat_stream(messages, max_tokens=max_tokens, temperature=temperature):
                    yield token
            else:
                # Fallback: simulate streaming by chunking non-streaming response
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: llm.chat(messages, max_tokens=max_tokens, temperature=temperature)
                )

                # Yield response in chunks to simulate streaming
                words = response.split()
                for i, word in enumerate(words):
                    yield word + (" " if i < len(words) - 1 else "")
                    await asyncio.sleep(0.01)  # Small delay to simulate streaming

        except Exception as e:
            logger.error(f"[streaming] LLM stream error: {e}")
            yield f"Error: {str(e)}"


class StreamBuffer:
    """Buffer for managing streamed content with overflow handling."""

    def __init__(self, max_size: int = 4096) -> None:
        """Initialize stream buffer.

        Args:
            max_size: Maximum buffer size in characters
        """
        self.max_size = max_size
        self._buffer = ""
        self._overflow = ""

    def append(self, text: str) -> tuple[str, bool]:
        """Append text to buffer.

        Args:
            text: Text to append

        Returns:
            Tuple of (current_buffer_content, has_overflow)
        """
        self._buffer += text

        if len(self._buffer) > self.max_size:
            # Move overflow to separate storage
            self._overflow += self._buffer[self.max_size:]
            self._buffer = self._buffer[:self.max_size]
            return self._buffer, True

        return self._buffer, False

    def get_content(self) -> str:
        """Get current buffer content."""
        return self._buffer

    def get_full_content(self) -> str:
        """Get buffer + overflow content."""
        return self._buffer + self._overflow

    def clear(self) -> None:
        """Clear buffer and overflow."""
        self._buffer = ""
        self._overflow = ""

    @property
    def has_overflow(self) -> bool:
        """Check if buffer has overflow."""
        return len(self._overflow) > 0


# =============================================================================
# Streaming Helpers
# =============================================================================

async def stream_with_typing(
    channel,  # Discord channel
    llm_stream: AsyncGenerator[str, None],
    streamer: ResponseStreamer | None = None
) -> str:
    """Stream LLM response while showing typing indicator.

    Args:
        channel: Discord channel object
        llm_stream: Async generator yielding tokens
        streamer: ResponseStreamer instance (creates default if None)

    Returns:
        Complete response text
    """
    if streamer is None:
        streamer = ResponseStreamer()

    # Send initial message
    message = await channel.send("✍️ *Generating response...*")

    try:
        # Stream response with live updates
        response = await streamer.stream_to_discord(
            message,
            llm_stream,
            prefix="",
            suffix=" ✍️"
        )
        return response
    except Exception as e:
        logger.error(f"[streaming] Error: {e}")
        await message.edit(content=f"❌ Error generating response: {str(e)}")
        raise


async def stream_with_progress(
    channel,  # Discord channel
    llm_stream: AsyncGenerator[str, None],
    total_tokens_estimate: int = 512
) -> str:
    """Stream LLM response with progress bar.

    Args:
        channel: Discord channel object
        llm_stream: Async generator yielding tokens
        total_tokens_estimate: Estimated total tokens for progress bar

    Returns:
        Complete response text
    """
    _streamer = ResponseStreamer()
    message = await channel.send("▱▱▱▱▱▱▱▱▱▱ 0%")

    accumulated = ""
    token_count = 0

    try:
        async for token in llm_stream:
            accumulated += token
            token_count += 1

            # Update progress bar every 10 tokens
            if token_count % 10 == 0:
                progress = min(token_count / total_tokens_estimate, 1.0)
                filled = int(progress * 10)
                bar = "▰" * filled + "▱" * (10 - filled)
                percent = int(progress * 100)

                preview = accumulated[-100:] if len(accumulated) > 100 else accumulated

                with contextlib.suppress(Exception):
                    await message.edit(content=f"{bar} {percent}%\n\n{preview}...")

        # Final update with complete response
        await message.edit(content=accumulated[:2000])
        return accumulated

    except Exception as e:
        logger.error(f"[streaming] Progress stream error: {e}")
        await message.edit(content=f"❌ Error: {str(e)}")
        raise


# =============================================================================
# Example Usage
# =============================================================================

async def example_usage():
    """Example of how to use streaming in Discord bot."""

    # Create streamer
    _streamer = ResponseStreamer(min_update_interval=0.5)

    # Simulate LLM stream
    async def mock_llm_stream():
        text = "This is a long response that will be streamed token by token to Discord. " * 10
        for word in text.split():
            yield word + " "
            await asyncio.sleep(0.05)

    # In your Discord bot message handler:
    # message = await ctx.send("Generating...")
    # response = await streamer.stream_to_discord(
    #     message,
    #     mock_llm_stream(),
    #     prefix="**Response:** ",
    #     suffix=" ✍️"
    # )

    print("Streaming example completed")


if __name__ == "__main__":
    asyncio.run(example_usage())
