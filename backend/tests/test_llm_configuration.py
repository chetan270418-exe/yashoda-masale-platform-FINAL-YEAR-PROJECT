"""Tests for the LLM provider configuration and the RAG citation validation.

Covers:
- The LLM provider is actually configurable (Decision D-010). The adapter used
  to read settings that did not exist, so a real provider could never be
  selected and the app always ran on the mock.
- A provider failure never leaks the API key or the request URL.
- RAG citations are validated against the context that was really supplied
  (Rule 63), and invented citations are counted and removed.
"""

from __future__ import annotations

import urllib.request

import pytest

from app.ai.llm_adapter import (
    DEFAULT_MODELS,
    OPENAI_COMPATIBLE_BASE_URLS,
    LiveHTTPLLMAdapter,
    MockLLMAdapter,
    describe_active_adapter,
    get_llm_adapter,
)
from app.ai.rag.service import REFUSAL_MESSAGE, answer_rag_query
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Settings is cached, so every test must start from a clean read."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _CannedAdapter:
    """Minimal adapter that returns a fixed answer, for RAG citation tests."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.last_prompt: str | None = None
        self.last_system_prompt: str | None = None

    def generate(self, prompt, system_prompt=None, temperature=0.2) -> str:
        self.last_prompt = prompt
        self.last_system_prompt = system_prompt
        return self.response

    def generate_json(self, prompt, system_prompt=None, temperature=0.1) -> dict:
        return {}


# --- provider configuration ----------------------------------------------


def test_settings_expose_the_llm_configuration():
    """Settings must actually declare the LLM fields the adapter reads."""
    settings = get_settings()
    assert settings.llm_provider
    assert settings.llm_api_key is not None
    assert settings.llm_base_url is not None
    assert settings.llm_model is not None
    assert settings.llm_timeout_seconds > 0


def test_default_adapter_is_the_deterministic_mock(monkeypatch):
    """With no provider configured the app must stay offline and deterministic."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_API_KEY", "")
    assert isinstance(get_llm_adapter(), MockLLMAdapter)


def test_configured_provider_selects_the_live_adapter(monkeypatch):
    """A real provider plus a key must produce the live adapter."""
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_API_KEY", "gsk_test_key")
    adapter = get_llm_adapter()
    assert isinstance(adapter, LiveHTTPLLMAdapter)
    assert adapter.provider == "groq"
    assert adapter.base_url == OPENAI_COMPATIBLE_BASE_URLS["groq"]


def test_placeholder_keys_fall_back_to_the_mock(monkeypatch):
    """A placeholder key must never be sent to a provider."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "change-me")
    assert isinstance(get_llm_adapter(), MockLLMAdapter)

    monkeypatch.setenv("LLM_API_KEY", "mock-key")
    assert isinstance(get_llm_adapter(), MockLLMAdapter)


def test_unknown_provider_without_base_url_falls_back_to_the_mock(monkeypatch):
    """An unconfigured provider must fail safe, not guess a URL."""
    monkeypatch.setenv("LLM_PROVIDER", "some-unknown-provider")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_BASE_URL", "")
    assert isinstance(get_llm_adapter(), MockLLMAdapter)


def test_unknown_provider_with_base_url_is_allowed(monkeypatch):
    """A self-hosted or proxied endpoint is configured with LLM_BASE_URL."""
    monkeypatch.setenv("LLM_PROVIDER", "my-proxy")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_BASE_URL", "https://proxy.internal/v1/")
    adapter = get_llm_adapter()
    assert isinstance(adapter, LiveHTTPLLMAdapter)
    assert adapter.base_url == "https://proxy.internal/v1"


def test_model_and_timeout_are_configurable(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_API_KEY", "gsk_test_key")
    monkeypatch.setenv("LLM_MODEL", "my-custom-model")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "42")
    adapter = get_llm_adapter()
    assert adapter.model_name == "my-custom-model"
    assert adapter.timeout == 42


def test_default_model_per_provider():
    """Each known provider has a sensible default model."""
    for provider, model in DEFAULT_MODELS.items():
        assert model, f"{provider} has no default model"


def test_describe_active_adapter_never_leaks_the_key(monkeypatch):
    """The adapter description is safe to log or show in a health check."""
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_API_KEY", "gsk_super_secret_value")
    described = describe_active_adapter()
    assert described["provider"] == "groq"
    assert described["using_mock"] is False
    assert "gsk_super_secret_value" not in str(described)
    assert "api_key" not in described


def test_provider_failure_does_not_leak_the_key_or_the_url(monkeypatch):
    """A provider error must not be echoed back to the caller."""

    def _boom(*args, **kwargs):
        raise RuntimeError("connection to https://api.example.com/v1?key=gsk_secret failed")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    adapter = LiveHTTPLLMAdapter(provider="openai", api_key="gsk_secret")
    result = adapter.generate("hello")

    assert "gsk_secret" not in result
    assert "api.example.com" not in result
    assert result == "The AI service is temporarily unavailable. Please try again."


# --- RAG citation validation ----------------------------------------------


def test_rag_accepts_a_valid_citation():
    """A citation that points at a supplied chunk is kept and reported."""
    adapter = _CannedAdapter("Turmeric powder is made from Salem rhizomes [S1].")
    result = answer_rag_query("How is turmeric powder made?", adapter=adapter)

    assert result["invalid_citations"] == 0
    assert len(result["cited_sources"]) == 1
    assert "[S1]" in result["answer"]


def test_rag_context_carries_the_citation_labels():
    """The model must be shown which labels it is allowed to cite."""
    adapter = _CannedAdapter("Answer [S1].")
    answer_rag_query("How is turmeric powder made?", adapter=adapter)
    assert adapter.last_prompt is not None
    assert "[Source: S1 |" in adapter.last_prompt


def test_rag_refuses_an_answer_whose_only_citation_is_invented():
    """Every citation invented means nothing in the answer is grounded."""
    adapter = _CannedAdapter("Turmeric is best used with [S9].")
    result = answer_rag_query("How is turmeric powder made?", adapter=adapter)

    assert result["answer"] == REFUSAL_MESSAGE
    assert result["invalid_citations"] == 1
    assert result["sources"] == []
    assert result["cited_sources"] == []


def test_rag_strips_an_invented_citation_but_keeps_the_valid_one():
    """A mixed answer loses the invented label and keeps the real one."""
    adapter = _CannedAdapter("Turmeric powder [S1] and a made-up detail [S7].")
    result = answer_rag_query("How is turmeric powder made?", adapter=adapter)

    assert result["invalid_citations"] == 1
    assert "[S1]" in result["answer"]
    assert "[S7]" not in result["answer"]
    assert len(result["cited_sources"]) == 1


def test_rag_keeps_the_exact_refusal_unchanged():
    """The refusal string must stay byte-for-byte exact (Rule 63)."""
    adapter = _CannedAdapter("Approved information not available.")
    result = answer_rag_query("How is turmeric powder made?", adapter=adapter)
    assert result["answer"] == REFUSAL_MESSAGE


def test_rag_with_no_match_returns_the_refusal():
    """A query that matches nothing must not be answered."""
    result = answer_rag_query("zzzzqqqqxxxx not in the knowledge base", adapter=_CannedAdapter("x"))
    assert result["answer"] == REFUSAL_MESSAGE
    assert result["sources"] == []
