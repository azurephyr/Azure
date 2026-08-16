"""Per-provider model catalogs with pricing, context, and capabilities.

Each provider has its own curated list of models with metadata.
Used by model_selector and settings_handler for autocomplete and recommendations.

Catalog sourced from provider docs and research (July 2026).
Only text/chat models included — embeddings, TTS, image gen, moderation, etc. excluded.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    context_window: int
    input_price: float   # per million tokens
    output_price: float  # per million tokens
    free_tier: bool = False
    max_output: int = 0
    description: str = ""

    @property
    def label(self) -> str:
        ctx = self.context_window
        if ctx >= 1_000_000:
            ctx_str = f"{ctx // 1_000_000}M"
        elif ctx >= 1_000:
            ctx_str = f"{ctx // 1_000}K"
        else:
            ctx_str = str(ctx)
        price = "free" if self.free_tier else f"${self.input_price:.2f}/${self.output_price:.2f}"
        return f"{self.name} ({ctx_str} ctx, {price})"


# ── OpenAI ──────────────────────────────────────────────────────────

OPENAI_MODELS = [
    ModelInfo("gpt-5.6-sol", "GPT-5.6 Sol", 1_050_000, 5.00, 30.00, max_output=128_000,
              description="Frontier model, complex reasoning and coding"),
    ModelInfo("gpt-5.6-terra", "GPT-5.6 Terra", 1_050_000, 2.50, 15.00, max_output=128_000,
              description="Balances intelligence and cost"),
    ModelInfo("gpt-5.6-luna", "GPT-5.6 Luna", 1_050_000, 1.00, 6.00, max_output=128_000,
              description="Cost-optimized for high-volume workloads"),
    ModelInfo("gpt-5.5", "GPT-5.5", 1_050_000, 5.00, 30.00, max_output=128_000,
              description="Previous flagship reasoning model"),
    ModelInfo("gpt-5.5-pro", "GPT-5.5 Pro", 1_050_000, 30.00, 180.00, max_output=128_000,
              description="Enhanced reasoning variant"),
    ModelInfo("gpt-5.4", "GPT-5.4", 1_050_000, 2.50, 15.00, max_output=128_000,
              description="Previous generation general-purpose"),
    ModelInfo("gpt-5.4-mini", "GPT-5.4 Mini", 1_050_000, 0.75, 4.50, max_output=128_000,
              description="Cost-efficient GPT-5.4 variant"),
    ModelInfo("gpt-5.4-nano", "GPT-5.4 Nano", 1_050_000, 0.20, 1.25, max_output=128_000,
              description="Most cost-efficient GPT-5.4"),
    ModelInfo("gpt-5.4-pro", "GPT-5.4 Pro", 1_050_000, 30.00, 180.00, max_output=128_000,
              description="Enhanced reasoning variant of GPT-5.4"),
    ModelInfo("gpt-5", "GPT-5", 400_000, 1.25, 10.00, max_output=16_384,
              description="Intelligent reasoning model for coding and agentic tasks"),
    ModelInfo("gpt-5-mini", "GPT-5 Mini", 400_000, 0.25, 2.00, max_output=16_384,
              description="Near-frontier intelligence for cost-sensitive workloads"),
    ModelInfo("gpt-5-nano", "GPT-5 Nano", 400_000, 0.05, 0.40, max_output=16_384,
              description="Fastest and most cost-efficient GPT-5 variant"),
    ModelInfo("gpt-5.3-codex", "GPT-5.3-Codex", 400_000, 1.75, 14.00, max_output=128_000,
              description="Most capable agentic coding model"),
    ModelInfo("gpt-5.2", "GPT-5.2", 400_000, 1.75, 14.00, max_output=128_000,
              description="Previous frontier with configurable reasoning"),
    ModelInfo("gpt-4.1", "GPT-4.1", 1_050_000, 2.00, 8.00, max_output=32_768,
              description="Previous-gen, 1M context window"),
    ModelInfo("gpt-4.1-mini", "GPT-4.1 Mini", 1_050_000, 0.40, 1.60, max_output=32_768,
              description="Cost-efficient GPT-4.1 variant"),
    ModelInfo("gpt-4.1-nano", "GPT-4.1 Nano", 1_050_000, 0.10, 0.40, max_output=32_768,
              description="Most cost-efficient GPT-4.1"),
    ModelInfo("gpt-4o", "GPT-4o", 128_000, 2.50, 10.00, max_output=16_384,
              description="Flagship model, best reasoning"),
    ModelInfo("gpt-4o-mini", "GPT-4o Mini", 128_000, 0.15, 0.60, max_output=16_384,
              description="Fast & cheap, great for most tasks"),
    ModelInfo("gpt-4-turbo", "GPT-4 Turbo", 128_000, 10.00, 30.00, max_output=4_096,
              description="Legacy flagship, strong reasoning"),
    ModelInfo("o3", "o3", 200_000, 2.00, 8.00, max_output=100_000,
              description="Reasoning model for complex tasks"),
    ModelInfo("o3-pro", "o3 Pro", 200_000, 20.00, 80.00, max_output=100_000,
              description="Extended compute reasoning"),
    ModelInfo("o3-mini", "o3 Mini", 200_000, 1.10, 4.40, max_output=100_000,
              description="Cost-efficient reasoning"),
    ModelInfo("o4-mini", "o4 Mini", 200_000, 1.10, 4.40, max_output=100_000,
              description="Cost-efficient reasoning for math/coding"),
    ModelInfo("o1", "o1", 200_000, 15.00, 60.00, max_output=100_000,
              description="Complex reasoning (legacy)"),
]

# ── Anthropic ───────────────────────────────────────────────────────

ANTHROPIC_MODELS = [
    ModelInfo("claude-fable-5", "Claude Fable 5", 1_000_000, 10.00, 50.00, max_output=128_000,
              description="Most capable, adaptive thinking for long-running agents"),
    ModelInfo("claude-opus-4-8", "Claude Opus 4.8", 1_000_000, 5.00, 25.00, max_output=128_000,
              description="Current flagship, complex agentic coding"),
    ModelInfo("claude-opus-4-7", "Claude Opus 4.7", 1_000_000, 5.00, 25.00, max_output=128_000,
              description="Previous flagship, adaptive thinking"),
    ModelInfo("claude-opus-4-6", "Claude Opus 4.6", 1_000_000, 5.00, 25.00, max_output=128_000,
              description="Legacy with extended thinking, 1M context"),
    ModelInfo("claude-opus-4-5-20251101", "Claude Opus 4.5", 200_000, 5.00, 25.00, max_output=64_000,
              description="Legacy, 200K context, extended thinking"),
    ModelInfo("claude-opus-4-1-20250805", "Claude Opus 4.1", 200_000, 15.00, 75.00,
              description="Deprecated, retiring Aug 2026"),
    ModelInfo("claude-sonnet-5", "Claude Sonnet 5", 1_000_000, 3.00, 15.00, max_output=128_000,
              description="Best speed/intelligence balance, intro $2/$10 through Aug 2026"),
    ModelInfo("claude-sonnet-4-6", "Claude Sonnet 4.6", 1_000_000, 3.00, 15.00, max_output=128_000,
              description="Legacy balanced production model, 1M context"),
    ModelInfo("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5", 200_000, 3.00, 15.00, max_output=64_000,
              description="Legacy with extended thinking"),
    ModelInfo("claude-sonnet-4-20250514", "Claude Sonnet 4", 200_000, 3.00, 15.00, max_output=64_000,
              description="Enhanced coding and reasoning"),
    ModelInfo("claude-haiku-4-5-20251001", "Claude Haiku 4.5", 200_000, 1.00, 5.00, max_output=64_000,
              description="Fastest, near-frontier intelligence (free on claude.ai, paid API)"),
    ModelInfo("claude-3-5-haiku-20241022", "Claude 3.5 Haiku", 200_000, 0.80, 4.00, max_output=64_000,
              description="Retired legacy budget model"),
    ModelInfo("claude-3-haiku-20240307", "Claude 3 Haiku", 200_000, 0.25, 1.25, max_output=4_000,
              description="Deprecated oldest Haiku"),
    ModelInfo("claude-3-opus-20240229", "Claude 3 Opus", 200_000, 15.00, 75.00, max_output=4_096,
              description="Deprecated original Opus"),
]

# ── Google AI Studio ────────────────────────────────────────────────

GOOGLE_MODELS = [
    ModelInfo("gemini-3.5-flash", "Gemini 3.5 Flash", 1_048_576, 1.50, 9.00, max_output=65_536,
              description="Frontier agentic/coding model, best in class"),
    ModelInfo("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview", 2_000_000, 2.00, 12.00, max_output=65_536,
              description="Current flagship, advanced reasoning"),
    ModelInfo("gemini-3-pro", "Gemini 3 Pro", 1_048_576, 2.00, 12.00, max_output=65_536,
              description="Stable flagship for complex reasoning"),
    ModelInfo("gemini-3-flash-preview", "Gemini 3 Flash Preview", 1_048_576, 0.50, 3.00, max_output=65_536,
              description="Fast, capable default Flash model"),
    ModelInfo("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite", 1_048_576, 0.25, 1.50, max_output=65_536,
              description="Cheapest Tier-1 budget model"),
    ModelInfo("gemini-2.5-pro", "Gemini 2.5 Pro", 2_000_000, 1.25, 10.00, max_output=65_536,
              description="Legacy flagship, strong coding/analysis"),
    ModelInfo("gemini-2.5-flash", "Gemini 2.5 Flash", 1_048_576, 0.30, 2.50, max_output=65_536,
              description="Legacy mid-tier for everyday tasks"),
    ModelInfo("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite", 1_048_576, 0.10, 0.40, max_output=65_536,
              description="Cheapest model for high-volume tasks"),
    ModelInfo("gemini-1.5-pro", "Gemini 1.5 Pro", 2_000_000, 1.25, 5.00,
              description="2M context, strong reasoning"),
    ModelInfo("gemini-1.5-flash", "Gemini 1.5 Flash", 1_000_000, 0.075, 0.30,
              description="Previous-gen fast model"),
    ModelInfo("gemma-4-31b", "Gemma 4 31B", 256_000, 0.0, 0.0, free_tier=True,
              description="Open-weight dense 31B, server-grade performance"),
    ModelInfo("gemma-4-26b", "Gemma 4 26B", 256_000, 0.0, 0.0, free_tier=True,
              description="MoE 3.8B active, frontier-quality open model"),
    ModelInfo("gemma-4-12b", "Gemma 4 12B", 256_000, 0.0, 0.0, free_tier=True,
              description="Open-weight 12B for multimodal tasks"),
    ModelInfo("gemma-4-4b", "Gemma 4 4B", 128_000, 0.0, 0.0, free_tier=True,
              description="Open-weight 4B for edge devices"),
    ModelInfo("gemma-4-2b", "Gemma 4 2B", 128_000, 0.0, 0.0, free_tier=True,
              description="Open-weight 2B for mobile/IoT"),
    ModelInfo("gemma-3-27b-it", "Gemma 3 27B", 128_000, 0.0, 0.0, free_tier=True,
              description="Open model, free on AI Studio"),
    ModelInfo("gemma-3-12b-it", "Gemma 3 12B", 128_000, 0.0, 0.0, free_tier=True,
              description="Open model, free on AI Studio"),
    ModelInfo("gemma-3-4b-it", "Gemma 3 4B", 128_000, 0.0, 0.0, free_tier=True,
              description="Open model, free on AI Studio"),
]

# ── Groq ────────────────────────────────────────────────────────────

GROQ_MODELS = [
    ModelInfo("llama-3.3-70b-versatile", "Llama 3.3 70B Versatile", 131_072, 0.59, 0.79,
              description="Meta Llama 3.3 70B, versatile general-purpose"),
    ModelInfo("llama-3.1-8b-instant", "Llama 3.1 8B Instant", 131_072, 0.05, 0.08,
              description="Meta Llama 3.1 8B, fast inference"),
    ModelInfo("openai/gpt-oss-120b", "GPT OSS 120B", 131_072, 0.15, 0.60,
              description="OpenAI open-weight 120B, browser search and code execution"),
    ModelInfo("openai/gpt-oss-20b", "GPT OSS 20B", 131_072, 0.075, 0.30,
              description="OpenAI open-weight 20B"),
    ModelInfo("meta-llama/llama-4-scout-17b-16e-instruct", "Llama 4 Scout 17B 16E", 131_072, 0.11, 0.34,
              description="Meta Llama 4 Scout MoE, preview on Groq"),
    ModelInfo("meta-llama/llama-4-maverick-17b-128e-instruct", "Llama 4 Maverick 17B 128E", 131_072, 0.20, 0.60,
              description="Llama 4 MoE maverick, higher quality than Scout"),
    ModelInfo("qwen/qwen3-32b", "Qwen3 32B", 131_072, 0.29, 0.59,
              description="Alibaba Qwen3 32B, preview on Groq"),
    ModelInfo("qwen/qwen3.6-27b", "Qwen3.6 27B", 131_072, 0.60, 3.00,
              description="Alibaba Qwen3.6 27B, preview on Groq"),
    ModelInfo("moonshotai/kimi-k2-instruct-0905", "Moonshot Kimi K2 Instruct", 262_144, 1.00, 3.00,
              description="Moonshot AI 1T param MoE, 262K context"),
    ModelInfo("groq/compound", "Groq Compound", 131_072, 0.0, 0.0,
              description="AI system with web search and code execution"),
    ModelInfo("groq/compound-mini", "Groq Compound Mini", 131_072, 0.0, 0.0,
              description="Cost-efficient agentic AI system"),
    ModelInfo("deepseek-r1-distill-llama-70b", "DeepSeek R1 (Llama 70B)", 128_000, 0.75, 0.99,
              description="Reasoning distilled into Llama"),
    ModelInfo("meta-llama/llama-guard-4-12b", "Llama Guard 4 12B", 131_072, 0.20, 0.20,
              description="Content safety classifier"),
]

# ── Mistral AI ──────────────────────────────────────────────────────

MISTRAL_MODELS = [
    ModelInfo("mistral-medium-latest", "Mistral Medium 3.5", 256_000, 1.50, 7.50, max_output=32_768,
              description="Frontier-class multimodal, agentic and coding"),
    ModelInfo("mistral-small-latest", "Mistral Small 4", 256_000, 0.15, 0.60, max_output=32_768,
              description="Hybrid instruct/reasoning/coding, open weights"),
    ModelInfo("mistral-large-latest", "Mistral Large 3", 256_000, 0.50, 1.50, max_output=32_768,
              description="Open-weight flagship multimodal"),
    ModelInfo("ministral-3b-latest", "Ministral 3 3B", 131_072, 0.10, 0.10, max_output=32_768,
              description="Tiny efficient edge model"),
    ModelInfo("ministral-8b-latest", "Ministral 3 8B", 262_144, 0.15, 0.15, max_output=32_768,
              description="Powerful efficient edge model"),
    ModelInfo("ministral-14b-latest", "Ministral 3 14B", 262_144, 0.20, 0.20, max_output=32_768,
              description="Largest Ministral 3 edge model"),
    ModelInfo("magistral-medium-latest", "Magistral Medium", 128_000, 2.00, 5.00, max_output=32_768,
              description="Thinking model for domain-specific reasoning"),
    ModelInfo("magistral-small-latest", "Magistral Small", 128_000, 0.50, 1.50, max_output=32_768,
              description="Lightweight thinking model"),
    ModelInfo("codestral-latest", "Codestral", 128_000, 0.30, 0.90, max_output=32_768,
              description="Low-latency coding model for completion and generation"),
    ModelInfo("devstral-medium-latest", "Devstral 2", 262_144, 0.40, 2.00, max_output=8_192,
              description="Open-weights agentic coding model"),
    ModelInfo("devstral-small-latest", "Devstral Small 2", 128_000, 0.10, 0.30, max_output=32_768,
              description="Lightweight open model for coding agents"),
    ModelInfo("pixtral-large-2411", "Pixtral Large", 128_000, 2.00, 6.00, max_output=4_096,
              description="Large-scale multimodal image-text reasoning"),
    ModelInfo("open-mistral-nemo", "Mistral NeMo 12B", 128_000, 0.15, 0.15, max_output=32_768,
              description="Mistral model for code tasks, open-weight"),
    ModelInfo("open-mixtral-8x7b", "Mixtral 8x7B", 32_768, 0.70, 0.70, max_output=32_768,
              description="7B sparse MoE, 45B total, 12.9B active"),
    ModelInfo("open-mixtral-8x22b", "Mixtral 8x22B", 65_536, 2.00, 6.00, max_output=32_768,
              description="22B sparse MoE, 141B total, 39B active"),
]

# ── OpenRouter Free Models ──────────────────────────────────────────

OPENROUTER_FREE_MODELS = [
    ModelInfo("nvidia/nemotron-3-ultra-550b-a55b:free", "Nemotron 3 Ultra 550B (Free)", 1_048_576, 0, 0, free_tier=True, max_output=65_536,
              description="550B MoE (55B active), hybrid Transformer-Mamba, frontier reasoning"),
    ModelInfo("nvidia/nemotron-3-super-120b-a12b:free", "Nemotron 3 Super 120B (Free)", 1_048_576, 0, 0, free_tier=True, max_output=262_144,
              description="120B MoE (12B active), multi-agent and coding"),
    ModelInfo("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "Nemotron 3 Nano Omni 30B (Free)", 256_000, 0, 0, free_tier=True, max_output=65_536,
              description="30B-A3B multimodal for text, image, video, audio"),
    ModelInfo("nvidia/nemotron-3-nano-30b-a3b:free", "Nemotron 3 Nano 30B A3B (Free)", 256_000, 0, 0, free_tier=True, max_output=256_000,
              description="30B-A3B MoE for agentic AI systems"),
    ModelInfo("nvidia/nemotron-nano-9b-v2:free", "Nemotron Nano 9B V2 (Free)", 128_000, 0, 0, free_tier=True, max_output=128_000,
              description="9B reasoning and non-reasoning unified model"),
    ModelInfo("nvidia/nemotron-nano-12b-v2-vl:free", "Nemotron Nano 12B V2 VL (Free)", 128_000, 0, 0, free_tier=True, max_output=128_000,
              description="12B multimodal for video understanding and doc intelligence"),
    ModelInfo("nvidia/llama-3.1-nemotron-ultra-253b:free", "Nemotron Ultra 253B (Free)", 131_072, 0, 0, free_tier=True,
              description="253B Nemotron Ultra, free"),
    ModelInfo("poolside/laguna-m.1:free", "Poolside Laguna M.1 (Free)", 262_144, 0, 0, free_tier=True, max_output=32_768,
              description="Flagship coding agent model"),
    ModelInfo("poolside/laguna-xs-2.1:free", "Poolside Laguna XS 2.1 (Free)", 262_144, 0, 0, free_tier=True, max_output=32_768,
              description="33B-A3B coding agent with tool calling"),
    ModelInfo("google/gemma-4-31b-it:free", "Gemma 4 31B (Free)", 262_144, 0, 0, free_tier=True, max_output=8_192,
              description="Google's open model, multimodal"),
    ModelInfo("google/gemma-4-26b-a4b-it:free", "Gemma 4 26B A4B (Free)", 262_144, 0, 0, free_tier=True, max_output=32_768,
              description="MoE multimodal with text, image, video input"),
    ModelInfo("openai/gpt-oss-120b:free", "OpenAI GPT-OSS 120B (Free)", 131_072, 0, 0, free_tier=True, max_output=131_072,
              description="117B MoE open-weight, high-reasoning"),
    ModelInfo("openai/gpt-oss-20b:free", "OpenAI GPT-OSS 20B (Free)", 131_072, 0, 0, free_tier=True, max_output=32_768,
              description="21B MoE open-weight, low-latency"),
    ModelInfo("meta-llama/llama-4-maverick:free", "Llama 4 Maverick (Free)", 1_048_576, 0, 0, free_tier=True, max_output=16_384,
              description="Meta Llama 4 Maverick MoE, free"),
    ModelInfo("meta-llama/llama-4-scout:free", "Llama 4 Scout (Free)", 10_000_000, 0, 0, free_tier=True, max_output=16_384,
              description="Meta Llama 4 Scout, 10M context, free"),
    ModelInfo("meta-llama/llama-3.3-70b-instruct:free", "Llama 3.3 70B (Free)", 131_072, 0, 0, free_tier=True, max_output=65_536,
              description="Solid all-rounder, free"),
    ModelInfo("meta-llama/llama-3.2-3b-instruct:free", "Llama 3.2 3B (Free)", 131_072, 0, 0, free_tier=True, max_output=131_072,
              description="3B multilingual model for dialogue"),
    ModelInfo("qwen/qwen3-coder:free", "Qwen3 Coder 480B A35B (Free)", 1_048_576, 0, 0, free_tier=True, max_output=262_000,
              description="480B MoE (35B active) coding specialist, 1M context"),
    ModelInfo("qwen/qwen3-next-80b-a3b-instruct:free", "Qwen3 Next 80B A3B (Free)", 262_144, 0, 0, free_tier=True, max_output=262_144,
              description="80B-A3B fast MoE for chat and reasoning"),
    ModelInfo("qwen/qwen3-30b-a3b:free", "Qwen3 30B A3B (Free)", 131_072, 0, 0, free_tier=True,
              description="Alibaba Qwen3 30B MoE, free"),
    ModelInfo("qwen/qwen3-4b:free", "Qwen3 4B (Free)", 131_072, 0, 0, free_tier=True,
              description="Alibaba Qwen3 4B, free"),
    ModelInfo("nousresearch/hermes-3-llama-3.1-405b:free", "Hermes 3 405B (Free)", 131_072, 0, 0, free_tier=True, max_output=131_072,
              description="405B params, uncensored, free"),
    ModelInfo("cognitivecomputations/dolphin-mistral-24b-venice-edition:free", "Venice Uncensored (Free)", 32_768, 0, 0, free_tier=True, max_output=32_768,
              description="Uncensored 24B Mistral fine-tune"),
    ModelInfo("tencent/hy3:free", "Tencent Hy3 (Free)", 262_144, 0, 0, free_tier=True, max_output=262_144,
              description="295B MoE reasoning model, configurable reasoning effort"),
    ModelInfo("microsoft/phi-4-reasoning-plus:free", "Phi-4 Reasoning Plus (Free)", 32_000, 0, 0, free_tier=True,
              description="Microsoft Phi-4 reasoning, free"),
    ModelInfo("deepseek/deepseek-r1-0528:free", "DeepSeek R1 (Free)", 163_840, 0, 0, free_tier=True,
              description="DeepSeek R1 reasoning, free"),
    ModelInfo("deepseek/deepseek-chat-v3-0324:free", "DeepSeek V3 0324 (Free)", 163_840, 0, 0, free_tier=True,
              description="DeepSeek V3 chat, free"),
    ModelInfo("google/gemma-3-27b-it:free", "Gemma 3 27B (Free)", 128_000, 0, 0, free_tier=True,
              description="Google open model, free"),
]

# ── OpenRouter Paid Models ──────────────────────────────────────────

OPENROUTER_PAID_MODELS = [
    ModelInfo("nvidia/nemotron-3-ultra-550b-a55b", "Nemotron 3 Ultra 550B", 1_000_000, 0.35, 1.00, max_output=65_536,
              description="550B MoE flagship frontier-reasoning model"),
    ModelInfo("nvidia/nemotron-3-super-120b-a12b", "Nemotron 3 Super 120B", 1_000_000, 0.15, 0.60, max_output=262_144,
              description="120B MoE efficient model for multi-agent"),
    ModelInfo("meta-llama/llama-4-maverick", "Llama 4 Maverick", 1_048_576, 0.20, 0.80, max_output=16_384,
              description="400B MoE, multimodal with 1M context"),
    ModelInfo("meta-llama/llama-4-scout", "Llama 4 Scout", 10_000_000, 0.10, 0.30, max_output=16_384,
              description="109B MoE, native multimodal with 10M context"),
    ModelInfo("openai/gpt-5.6-sol", "GPT-5.6 Sol (via OpenRouter)", 1_050_000, 5.00, 30.00,
              description="OpenAI GPT-5.6 Sol"),
    ModelInfo("openai/gpt-5.6-terra", "GPT-5.6 Terra (via OpenRouter)", 1_050_000, 2.50, 15.00,
              description="OpenAI GPT-5.6 Terra"),
    ModelInfo("openai/gpt-5.6-luna", "GPT-5.6 Luna (via OpenRouter)", 1_050_000, 1.00, 6.00,
              description="OpenAI GPT-5.6 Luna"),
    ModelInfo("openai/gpt-5.4", "GPT-5.4 (via OpenRouter)", 1_050_000, 2.50, 15.00,
              description="OpenAI GPT-5.4"),
    ModelInfo("openai/gpt-4.1", "GPT-4.1 (via OpenRouter)", 1_050_000, 2.00, 8.00,
              description="OpenAI GPT-4.1"),
    ModelInfo("openai/gpt-4o", "GPT-4o (via OpenRouter)", 128_000, 2.50, 10.00,
              description="OpenAI flagship"),
    ModelInfo("openai/gpt-4o-mini", "GPT-4o Mini (via OpenRouter)", 128_000, 0.15, 0.60,
              description="OpenAI budget"),
    ModelInfo("anthropic/claude-fable-5", "Claude Fable 5 (via OpenRouter)", 1_000_000, 10.00, 50.00,
              description="Anthropic most capable"),
    ModelInfo("anthropic/claude-opus-4.8", "Claude Opus 4.8 (via OpenRouter)", 1_000_000, 5.00, 25.00,
              description="Anthropic Opus 4.8"),
    ModelInfo("anthropic/claude-opus-4.7", "Claude Opus 4.7 (via OpenRouter)", 1_000_000, 5.00, 25.00,
              description="Anthropic Opus 4.7"),
    ModelInfo("anthropic/claude-sonnet-5", "Claude Sonnet 5 (via OpenRouter)", 1_000_000, 2.00, 10.00,
              description="Anthropic latest"),
    ModelInfo("anthropic/claude-sonnet-4.6", "Claude Sonnet 4.6 (via OpenRouter)", 1_000_000, 3.00, 15.00,
              description="Anthropic Sonnet 4.6"),
    ModelInfo("anthropic/claude-sonnet-4", "Claude Sonnet 4 (via OpenRouter)", 200_000, 3.00, 15.00,
              description="Anthropic Sonnet 4"),
    ModelInfo("anthropic/claude-haiku-4.5", "Claude Haiku 4.5 (via OpenRouter)", 200_000, 1.00, 5.00,
              description="Anthropic Haiku 4.5"),
    ModelInfo("google/gemini-3.5-flash", "Gemini 3.5 Flash (via OpenRouter)", 1_048_576, 1.50, 9.00,
              description="Google Gemini 3.5 Flash"),
    ModelInfo("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro (via OpenRouter)", 2_000_000, 2.00, 12.00,
              description="Google Gemini 3.1 Pro"),
    ModelInfo("google/gemini-2.5-pro", "Gemini 2.5 Pro (via OpenRouter)", 2_000_000, 1.25, 10.00,
              description="Google Gemini 2.5 Pro"),
    ModelInfo("google/gemini-2.5-flash", "Gemini 2.5 Flash (via OpenRouter)", 1_048_576, 0.30, 2.50,
              description="Google Gemini 2.5 Flash"),
    ModelInfo("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro (via OpenRouter)", 1_048_576, 0.435, 0.87,
              description="1.6T MoE flagship reasoning"),
    ModelInfo("deepseek/deepseek-v4-flash", "DeepSeek V4 Flash (via OpenRouter)", 1_048_576, 0.077, 0.154,
              description="284B MoE efficiency-optimized"),
    ModelInfo("deepseek/deepseek-r1", "DeepSeek R1 (via OpenRouter)", 163_840, 0.55, 2.19,
              description="DeepSeek R1 reasoning"),
    ModelInfo("deepseek/deepseek-chat-v3-0324", "DeepSeek V3 (via OpenRouter)", 163_840, 0.27, 1.10,
              description="DeepSeek V3 chat"),
    ModelInfo("qwen/qwen3.6-35b-a3b", "Qwen 3.6 35B A3B (via OpenRouter)", 262_144, 0.14, 1.00,
              description="35B-A3B hybrid MoE multimodal"),
    ModelInfo("qwen/qwen3.5-flash-02-23", "Qwen 3.5 Flash (via OpenRouter)", 1_000_000, 0.065, 0.26,
              description="Fast hybrid MoE, ultra-low cost"),
    ModelInfo("x-ai/grok-4.5", "Grok 4.5 (via OpenRouter)", 500_000, 2.00, 6.00,
              description="xAI frontier model, top coding/knowledge"),
    ModelInfo("x-ai/grok-4.3", "Grok 4.3 (via OpenRouter)", 1_000_000, 1.25, 2.50,
              description="xAI reasoning model, 1M context"),
    ModelInfo("x-ai/grok-3", "Grok 3 (via OpenRouter)", 131_072, 3.00, 15.00,
              description="xAI Grok 3"),
    ModelInfo("x-ai/grok-3-mini", "Grok 3 Mini (via OpenRouter)", 131_072, 0.30, 0.50,
              description="xAI Grok 3 Mini"),
    ModelInfo("mistralai/mistral-medium-3.5", "Mistral Medium 3.5 (via OpenRouter)", 256_000, 1.50, 7.50,
              description="Mistral Medium 3.5"),
    ModelInfo("mistralai/mistral-large-3", "Mistral Large 3 (via OpenRouter)", 256_000, 0.50, 1.50,
              description="Mistral Large 3"),
]

# ── NaraRouter ───────────────────────────────────────────────────

NARAROUTER_MODELS = [
    ModelInfo("agnes-2.5-flash", "Agnes 2.5 Flash (Free)", 524_288, 0, 0, free_tier=True,
              description="Agnes 2.5 Flash via NaraRouter"),
    ModelInfo("nemotron-3-ultra", "Nemotron 3 Ultra", 1_000_000, 0.35, 1.00, max_output=65_536,
              description="NVIDIA Nemotron 3 Ultra via NaraRouter"),
    ModelInfo("auto/bynara", "Auto Router (Free)", 1_048_576, 0, 0, free_tier=True,
              description="Auto-routes to best available model"),
    ModelInfo("agnes-2.0-flash", "Agnes 2.0 Flash (Free)", 262_144, 0, 0, free_tier=True,
              description="Agnes 2.0 Flash — free, vision-capable"),
    ModelInfo("mistral-large", "Mistral Large (Free)", 262_144, 0, 0, free_tier=True,
              description="Mistral Large — free on NaraRouter"),
    ModelInfo("mistral-medium-3-5", "Mistral Medium 3.5 (Free)", 262_144, 0, 0, free_tier=True,
              description="Mistral Medium 3.5 — free, vision-capable"),
    ModelInfo("stepfun-3.7-flash", "StepFun 3.7 Flash (Free)", 262_144, 0, 0, free_tier=True,
              description="StepFun 3.7 Flash via NaraRouter"),
    ModelInfo("tencent-hy3", "Tencent Hy3 (Free)", 262_144, 0, 0, free_tier=True,
              description="Tencent Hybrid 3 — free on NaraRouter"),
    ModelInfo("deepseek-3.2", "DeepSeek V4 Flash Naraya", 1_048_576, 0.08, 0.09,
              description="DeepSeek V4 Flash via NaraRouter"),
    ModelInfo("deepseek-v4-pro", "DeepSeek V4 Pro Naraya", 1_048_576, 0.22, 0.43,
              description="DeepSeek V4 Pro via NaraRouter"),
    ModelInfo("minimax-m3", "MiniMax M3", 1_048_576, 0.15, 0.60,
              description="MiniMax M3 via NaraRouter"),
    ModelInfo("qwen-3.7-max", "Qwen 3.7 Max Naraya", 1_048_576, 0.87, 2.61,
              description="Qwen 3.7 Max via NaraRouter"),
    ModelInfo("glm-5.2", "GLM 5.2", 1_048_576, 0.28, 0.89,
              description="Zhipu GLM 5.2 via NaraRouter"),
    ModelInfo("glm-5.2-free", "GLM 5.2 Free", 1_048_576, 0.21, 0.66,
              description="Zhipu GLM 5.2 Free via NaraRouter"),
    ModelInfo("mimo-v2.5", "MiMo V2.5", 1_048_576, 0.03, 0.08,
              description="Xiaomi MiMo V2.5 via NaraRouter"),
    ModelInfo("mimo-v2.5-hermes", "MiMo V2.5 Hermes", 1_048_576, 0.03, 0.08,
              description="MiMo V2.5 Hermes via NaraRouter"),
    ModelInfo("mimo-v2.5-pro", "MiMo V2.5 Pro", 1_048_576, 0.22, 0.43,
              description="MiMo V2.5 Pro via NaraRouter"),
    ModelInfo("mimo-v2.5-pro-ultraspeed", "MiMo V2.5 Pro Ultraspeed", 1_048_576, 0.13, 0.26,
              description="MiMo V2.5 Pro Ultraspeed via NaraRouter"),
    ModelInfo("kimi-k2.6", "Kimi K2.6", 262_144, 0.20, 1.01,
              description="Moonshot Kimi K2.6 via NaraRouter"),
    ModelInfo("kimi-k2.7-code", "Kimi K2.7 Code", 262_144, 0.36, 1.72,
              description="Moonshot Kimi K2.7 Code via NaraRouter"),
    ModelInfo("kimi-k2.7-code-free", "Kimi K2.7 Code Free", 262_144, 0.36, 1.72,
              description="Moonshot Kimi K2.7 Code Free via NaraRouter"),
    ModelInfo("gpt-5.4", "GPT-5.4 (Nara)", 1_050_000, 0.50, 2.98,
              description="OpenAI GPT-5.4 via NaraRouter"),
    ModelInfo("gpt-5.5", "GPT-5.5 (Nara)", 1_050_000, 0.99, 5.96,
              description="OpenAI GPT-5.5 via NaraRouter"),
    ModelInfo("gpt-5.6-luna", "GPT 5.6 Luna (Nara)", 1_050_000, 0.40, 2.40,
              description="GPT 5.6 Luna via NaraRouter"),
    ModelInfo("gpt-5.6-sol", "GPT 5.6 Sol (Nara)", 1_050_000, 2.00, 11.98,
              description="GPT 5.6 Sol via NaraRouter"),
    ModelInfo("gpt-5.6-terra", "GPT 5.6 Terra (Nara)", 1_050_000, 1.00, 5.99,
              description="GPT 5.6 Terra via NaraRouter"),
    ModelInfo("claude-opus-4.7", "Claude Opus 4.7 (Nara)", 1_000_000, 1.69, 8.44,
              description="Anthropic Claude Opus 4.7 via NaraRouter"),
    ModelInfo("claude-opus-4.8", "Claude Opus 4.8 (Nara)", 1_000_000, 1.69, 8.44,
              description="Anthropic Claude Opus 4.8 via NaraRouter"),
    ModelInfo("claude-sonnet-5", "Claude Sonnet 5 (Nara)", 1_000_000, 0.68, 3.38,
              description="Anthropic Claude Sonnet 5 via NaraRouter"),
    ModelInfo("claude-sonnet-5-nara", "Claude Sonnet 5 byNara", 1_000_000, 0.10, 0.50,
              description="Claude Sonnet 5 optimized by NaraRouter"),
]


# ── Provider Catalog ────────────────────────────────────────────────

PROVIDER_CATALOGS: dict[str, dict] = {
    "openai": {
        "display_name": "OpenAI",
        "api_key_envs": ("AZURE_OPENAI_API_KEY", "OPENAI_API_KEY"),
        "protocol": "openai",
        "models": OPENAI_MODELS,
    },
    "anthropic": {
        "display_name": "Anthropic",
        "api_key_envs": ("AZURE_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        "protocol": "anthropic",
        "models": ANTHROPIC_MODELS,
    },
    "google": {
        "display_name": "Google AI Studio",
        "api_key_envs": ("AZURE_GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "protocol": "google",
        "models": GOOGLE_MODELS,
    },
    "groq": {
        "display_name": "Groq",
        "api_key_envs": ("AZURE_GROQ_API_KEY", "GROQ_API_KEY"),
        "protocol": "openai",
        "models": GROQ_MODELS,
    },
    "mistral": {
        "display_name": "Mistral AI",
        "api_key_envs": ("AZURE_MISTRAL_API_KEY", "MISTRAL_API_KEY"),
        "protocol": "openai",
        "models": MISTRAL_MODELS,
    },
    "openrouter": {
        "display_name": "OpenRouter",
        "api_key_envs": ("AZURE_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
        "protocol": "openai",
        "models": OPENROUTER_FREE_MODELS + OPENROUTER_PAID_MODELS,
    },
    "nararouter": {
        "display_name": "NaraRouter",
        "api_key_envs": ("AZURE_NARAROUTER_API_KEY", "NARAROUTER_API_KEY"),
        "protocol": "openai",
        "models": NARAROUTER_MODELS,
    },
}


def get_models_for_provider(provider: str) -> list[ModelInfo]:
    cat = PROVIDER_CATALOGS.get(provider)
    if not cat:
        return []
    return cat["models"]


def get_free_models_for_provider(provider: str) -> list[ModelInfo]:
    return [m for m in get_models_for_provider(provider) if m.free_tier]


def get_paid_models_for_provider(provider: str) -> list[ModelInfo]:
    return [m for m in get_models_for_provider(provider) if not m.free_tier]


def get_model_info(provider: str, model_id: str) -> ModelInfo | None:
    for m in get_models_for_provider(provider):
        if m.id == model_id:
            return m
    return None


def get_recommendations(provider: str, tier: str = "free") -> list[ModelInfo]:
    models = get_models_for_provider(provider)
    if tier == "free":
        free = [m for m in models if m.free_tier]
        if free:
            return free
    return models[:3]
