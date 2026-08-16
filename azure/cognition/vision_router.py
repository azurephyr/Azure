"""
VisionRouter — Deliverable 6: Multimodal Input Routing (Attachment Awareness)

Detects image attachments on incoming messages and routes them through
a vision-capable pass before the main reasoning loop.

Note: Currently, this module provides "attachment awareness" (extracting
metadata like filename, content type, and dimensions) rather than true
multimodal visual reasoning, unless an external vision model is explicitly configured.

Architecture:
  on_message detects image → VisionRouter.describe() →
  appends description to message text → CognitivePipeline processes

Since local Qwen 2.5-3B is not vision-capable, this module:
  1. Detects images and notes their presence (Attachment Awareness)
  2. If a vision model is configured (cloud or larger local), uses it
  3. Falls back to image metadata (filename, dimensions, format)
  4. Future: integrate with a vision model (Qwen-VL, etc.)

Usage:
    router = VisionRouter()
    context = await router.process_attachments(message.attachments)
    # context is appended to the user's message text
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ImageDescription:
    """Description of an attached image."""
    filename: str = ""
    url: str = ""
    content_type: str = ""
    size_bytes: int = 0
    width: int | None = None
    height: int | None = None
    description: str = ""  # Vision model output (if available)
    extracted_text: str = ""  # OCR text (if available)


class VisionRouter:
    """
    Routes image attachments through a vision-capable pass.

    For now, this is a lightweight wrapper that:
      - Extracts image metadata
      - Optionally calls a vision model (if configured)
      - Returns structured context for the reasoning pipeline
    """

    def __init__(self, vision_model=None):
        """
        Args:
            vision_model: Optional vision-capable model (e.g., Qwen-VL, cloud API).
                          If None, falls back to metadata-only descriptions.
        """
        self.vision_model = vision_model

    async def process_attachments(self, attachments: list) -> str:
        """
        Process a list of Discord message attachments.

        Returns a string of context to append to the user's message.
        """
        images = [a for a in attachments if a.content_type and a.content_type.startswith("image/")]
        if not images:
            return ""

        descriptions = []
        for img in images[:3]:  # Limit to 3 images for context size
            desc = await self._describe_image(img)
            descriptions.append(desc)

        if not descriptions:
            return ""

        parts = ["[User attached images:]"]
        for d in descriptions:
            parts.append(f"  - {d.filename} ({d.content_type})")
            if d.description:
                parts.append(f"    Description: {d.description}")
            if d.extracted_text:
                parts.append(f"    Text in image: {d.extracted_text}")
            if d.width and d.height:
                parts.append(f"    Dimensions: {d.width}x{d.height}")

        return "\n".join(parts)

    async def _describe_image(self, attachment) -> ImageDescription:
        """Describe a single image attachment."""
        desc = ImageDescription(
            filename=attachment.filename,
            url=attachment.url,
            content_type=attachment.content_type or "image/unknown",
            size_bytes=attachment.size,
        )

        # If a vision model is configured, use it
        if self.vision_model is not None:
            try:
                vision_text = await self._call_vision_model(attachment.url)
                if vision_text:
                    desc.description = vision_text
            except Exception as e:
                desc.description = f"[Vision model error: {e}]"

        # Try to infer dimensions from common formats
        if attachment.width and attachment.height:
            desc.width = attachment.width
            desc.height = attachment.height

        return desc

    async def _call_vision_model(self, image_url: str) -> str | None:
        """
        Call a vision-capable model to describe the image.

        This is a placeholder for future vision model integration.
        Currently supports:
          - No vision model (returns None, falls back to metadata)
          - Cloud vision APIs (if configured)
          - Local vision models (if loaded)
        """
        # Placeholder: no vision model configured
        return None

    def get_info(self) -> dict:
        """Return vision router status."""
        return {
            "vision_model_loaded": self.vision_model is not None,
            "model_name": getattr(self.vision_model, "model_name", None),
        }
