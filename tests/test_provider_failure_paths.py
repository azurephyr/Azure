"""Regression tests for provider failures and Discord reply delivery."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from azure.api_llm import ApiLLM, ProviderRequestError
from azure.failover_chain import FailoverChain


def test_openai_compatible_auth_or_credit_error_does_not_try_fallback_model():
    llm = ApiLLM.__new__(ApiLLM)
    llm._model = "primary"
    llm._fallback_model = "fallback"

    calls = []

    def fail_once(*args, **kwargs):
        calls.append((args, kwargs))
        raise ProviderRequestError("provider rejected request", status_code=402)

    llm._chat_openai_model = fail_once

    with pytest.raises(ProviderRequestError):
        llm._chat_openai([], 0.0, 16)

    assert len(calls) == 1


def test_failover_returns_provider_error_without_exhausting_all_tiers():
    class QuotaLLM:
        calls = 0

        def chat(self, *args, **kwargs):
            self.calls += 1
            raise ProviderRequestError("provider has no credits", status_code=402)

    llm = QuotaLLM()
    result = FailoverChain(llm=llm).respond("hello")

    assert "credits" in result.text.lower()
    assert result.tier_name == "provider_unavailable"
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_empty_reply_never_claims_task_completed():
    from bot.handlers.message_handler import _send_empty_reply

    progress = MagicMock()
    progress.channel = MagicMock()
    progress.content = "🧠 **Thinking...**"
    edited = {}

    async def edit(**kwargs):
        edited.update(kwargs)
        return progress

    progress.edit = edit

    await _send_empty_reply(progress, tracker=None, text="hello", edit_state={})

    assert "couldn't produce a response" in edited["content"].lower()
    assert "done" not in edited["content"].lower()
