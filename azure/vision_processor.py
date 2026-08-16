"""
Azure Vision Processor — Multi-Modal Image Understanding

Processes image attachments using:
- BLIP/CLIP for image captioning (if transformers available)
- OCR for text extraction (if pytesseract/easyocr available)
- Object detection (if available)

Graceful degradation: if no vision libraries, returns basic file info.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("azure.vision")


@dataclass
class VisionResult:
    """Result of image processing."""
    caption: str = ""
    ocr_text: str = ""
    objects: list[str] = field(default_factory=list)
    dominant_colors: list[str] = field(default_factory=list)
    file_type: str = ""
    file_size: int = 0
    width: int = 0
    height: int = 0
    has_text: bool = False
    confidence: float = 0.0

    def to_context(self) -> str:
        """Convert vision result to a text context string for the LLM."""
        parts = []
        if self.caption:
            parts.append(f"Image description: {self.caption}")
        if self.ocr_text:
            parts.append(f"Text in image: {self.ocr_text}")
        if self.objects:
            parts.append(f"Objects detected: {', '.join(self.objects)}")
        if not parts:
            parts.append(f"Image file ({self.file_type}, {self.width}x{self.height})")
        return "\n".join(parts)


class VisionProcessor:
    """
    Multi-modal image understanding processor.

    Usage:
        processor = VisionProcessor()
        result = await processor.process_attachment(attachment_bytes, filename="image.png")
        context = result.to_context()
    """

    def __init__(self):
        self._blip = None
        self._blip_processor = None
        self._blip_model = None
        self._clip_processor = None
        self._clip_model = None
        self._tesseract = None
        self._initialized = False

    def _init_models(self) -> None:
        """Lazy-load vision models."""
        if self._initialized:
            return
        self._initialized = True

        # Try BLIP for captioning
        try:
            from transformers import BlipForConditionalGeneration, BlipProcessor
            self._blip_processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
            self._blip_model = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base")
            self._blip = True
            logger.info("[vision] BLIP loaded")

        except Exception:
            logger.exception("[vision] BLIP init failed")

        # Try CLIP for object detection
        try:
            from transformers import CLIPModel, CLIPProcessor
            self._clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
            self._clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
            logger.info("[vision] CLIP loaded")

        except Exception:
            logger.exception("[vision] CLIP init failed")

        # Try pytesseract for OCR
        try:
            import pytesseract
            from PIL import Image
            self._tesseract = pytesseract
            self._pil = Image
            logger.info("[vision] Tesseract OCR loaded")

        except Exception:
            logger.exception("[vision] Tesseract init failed")

    async def process_attachment(self, attachment_bytes: bytes, filename: str) -> VisionResult:
        """Process a Discord image attachment."""
        self._init_models()

        result = VisionResult(file_type=filename.split(".")[-1].lower() if "." in filename else "unknown")
        result.file_size = len(attachment_bytes)

        # Get image dimensions
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(attachment_bytes))
            result.width, result.height = img.size
            # Convert to RGB if needed
            if img.mode != "RGB":
                img = img.convert("RGB")
        except Exception:
            return result

        # BLIP captioning
        if self._blip:
            try:
                inputs = self._blip_processor(img, return_tensors="pt")
                import torch
                with torch.no_grad():
                    output = self._blip_model.generate(**inputs, max_new_tokens=50)
                caption = self._blip_processor.decode(output[0], skip_special_tokens=True)
                result.caption = caption.strip()
                result.confidence = 0.8
            except Exception as e:
                logger.error(f"[vision] BLIP error: {e}")


        # OCR
        if self._tesseract:
            try:
                text = self._tesseract.image_to_string(img).strip()
                if text:
                    result.ocr_text = text
                    result.has_text = True
            except Exception as e:
                logger.error(f"[vision] OCR error: {e}")


        # Basic object detection via CLIP (if available)
        if self._clip_processor and self._clip_model:
            try:
                candidate_labels = ["a photo of a person", "a photo of a cat", "a photo of a dog",
                                    "a photo of food", "a photo of a landscape", "a photo of text",
                                    "a screenshot", "a meme", "a chart or graph", "a diagram"]
                inputs = self._clip_processor(text=candidate_labels, images=img, return_tensors="pt", padding=True)
                import torch
                with torch.no_grad():
                    outputs = self._clip_model(**inputs)
                logits_per_image = outputs.logits_per_image
                probs = logits_per_image.softmax(dim=1)
                top_idx = probs[0].argmax().item()
                result.objects.append(candidate_labels[top_idx].replace("a photo of ", ""))
                result.confidence = max(result.confidence, float(probs[0][top_idx]))
            except Exception as e:
                logger.warning(f"CLIP object detection failed: {e}")

        return result

    @staticmethod
    def _is_safe_url(url: str) -> bool:
        """Block SSRF and dangerous schemes. Allow only http(s)."""
        if not isinstance(url, str):
            return False
        url = url.strip()
        if not url:
            return False
        low = url.lower()
        for blocked in (
            "file://",
            "ftp://",
            "gopher://",
            "ldap://",
            "dict://",
            "data:",
            "javascript:",
        ):
            if low.startswith(blocked):
                return False
        if not (low.startswith("http://") or low.startswith("https://")):
            return False
        from urllib.parse import urlparse
        try:
            host = urlparse(url).hostname or ""
        except Exception:
            return False
        host = host.lower().strip("[]")
        # Block obvious metadata/loopback hostnames early.
        if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0", "169.254.169.254"}:
            return False

        # Resolve the hostname and reject if ANY resolved address is internal.
        # This closes the DNS-rebinding hole: ipaddress.ip_address(host) raises
        # ValueError for a hostname, so a name resolving to a private IP would
        # otherwise slip through.
        import socket
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            # Can't resolve — treat as unsafe rather than fetching blindly.
            return False
        for info in infos:
            ip_str = info[4][0]
            if VisionProcessor._is_blocked_ip(ip_str):
                return False
        return True

    @staticmethod
    def _is_blocked_ip(host: str) -> bool:
        """True if host is a literal IP in a private/loopback/link-local/etc range."""
        if host.startswith(("127.", "10.", "192.168.", "172.16.", "172.17.",
                            "172.18.", "172.19.", "172.20.", "172.21.",
                            "172.22.", "172.23.", "172.24.", "172.25.",
                            "172.26.", "172.27.", "172.28.", "172.29.",
                            "172.30.", "172.31.", "169.254.")):
            return True
        try:
            import ipaddress
            ip = ipaddress.ip_address(host)
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
                return True
        except ValueError:
            return False
        return False

    async def process_url(self, url: str) -> VisionResult:
        """Process an image from a URL with SSRF protection."""
        if not self._is_safe_url(url):
            return VisionResult(
                caption="[blocked: URL failed safety check (scheme/host not permitted)]"
            )
        try:
            import urllib.request
            import urllib.error

            # Refuse to follow redirects: the upfront host check only validated
            # the original URL, and urlopen would otherwise transparently follow
            # a 3xx into an internal/metadata endpoint (SSRF).
            class _NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    raise urllib.error.HTTPError(
                        url, 399, "Redirects are not allowed", {}, None
                    )

            opener = urllib.request.build_opener(_NoRedirect)
            loop = asyncio.get_running_loop()
            req = urllib.request.Request(url, headers={"User-Agent": "AzureBot/1.0"})
            data = await loop.run_in_executor(
                None,
                lambda: opener.open(req, timeout=10).read(20 * 1024 * 1024)
            )
            return await self.process_attachment(data, filename=url.split("/")[-1] or "image")
        except Exception as e:
            return VisionResult(caption=f"Could not load image from URL: {e}")
