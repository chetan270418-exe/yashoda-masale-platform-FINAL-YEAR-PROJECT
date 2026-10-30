"""LLM Adapter layer (Decision D-010).

Isolates external LLM providers behind an abstract interface.
Protects credentials by ensuring API keys only reside server-side (Rule 33).
Provides a deterministic Mock adapter for unit testing and CI (Rule 72).
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Providers that speak the OpenAI chat-completions wire format. Selecting one
# of these only needs LLM_PROVIDER and LLM_API_KEY; LLM_BASE_URL overrides the
# default, which is how a self-hosted or proxied endpoint is used.
OPENAI_COMPATIBLE_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "mistral": "https://api.mistral.ai/v1",
    "together": "https://api.together.xyz/v1",
    "deepseek": "https://api.deepseek.com/v1",
}

# Default model per provider. An empty LLM_MODEL falls back to these.
DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "groq": "openai/gpt-oss-120b",
    "openrouter": "openai/gpt-4o-mini",
    "mistral": "mistral-small-latest",
    "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "deepseek": "deepseek-chat",
    "gemini": "gemini-2.0-flash",
}

# Message returned to callers when a provider call fails. The real cause is
# logged server-side without the key or the URL.
PROVIDER_ERROR_MESSAGE = "The AI service is temporarily unavailable. Please try again."


class LLMAdapter(ABC):
    """Abstract interface for language model generation."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        """Generate text completion from prompt."""
        raise NotImplementedError

    @abstractmethod
    def generate_json(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """Generate structured JSON response from prompt."""
        raise NotImplementedError


class MockLLMAdapter(LLMAdapter):
    """Deterministic mock LLM adapter for testing and offline environments."""

    def __init__(self, canned_response: str | None = None) -> None:
        self.canned_response = canned_response
        self.last_prompt: str | None = None
        self.last_system_prompt: str | None = None

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        self.last_prompt = prompt
        self.last_system_prompt = system_prompt
        if self.canned_response:
            return self.canned_response

        # Default intelligent mock behaviour based on prompt content
        lower_prompt = prompt.lower()
        if "turmeric" in lower_prompt or "haldi" in lower_prompt:
            return (
                "Yashoda Turmeric Powder (Haldi) is produced from premium Salem rhizomes "
                "with a guaranteed minimum curcumin content of 3.0%. Available in 100g, 250g, "
                "and 500g laminated moisture-proof pouches. Storage: keep in cool, dry conditions "
                "away from direct sunlight. Shelf life is 12 months."
            )
        if "garam masala" in lower_prompt:
            return (
                "Yashoda Garam Masala is a traditional blend of 16 aromatic whole spices, "
                "roasted and ground at controlled low temperatures. Pack sizes: 100g, 250g, 500g. "
                "Store in an airtight container to preserve essential volatile oils."
            )
        if "order" in lower_prompt or "reorder" in lower_prompt:
            return (
                "Based on your purchase history over the last 3 months, your regular monthly order "
                "consists of 50 packs of Garam Masala 250g and 30 packs of Turmeric Powder 500g. "
                "I have prepared an order draft for your review and approval."
            )
        if "unknown" in lower_prompt or "chocolate" in lower_prompt or "secret" in lower_prompt:
            return "Approved information not available."

        return (
            "Yashoda SpiceChain AI provides authentic spices, traceable batches, "
            "and FEFO inventory."
        )

    def generate_json(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        self.last_prompt = prompt
        self.last_system_prompt = system_prompt

        # Extract only user request line to avoid matching tool catalog descriptions
        user_text = prompt
        for line in prompt.splitlines():
            if "user request:" in line.lower():
                user_text = line.split(":", 1)[1]
                break

        lower = user_text.lower()
        if "sales" in lower:
            return {
                "tool": "get_sales_summary",
                "arguments": {"period": "last_30_days", "group_by": "variant"},
            }
        if "stock" in lower or "inventory" in lower:
            return {"tool": "check_stock", "arguments": {}}
        if "slow" in lower:
            return {
                "tool": "get_slow_movers",
                "arguments": {"threshold_days": 60},
            }
        if "history" in lower:
            return {"tool": "get_dealer_history", "arguments": {}}
        if "reorder" in lower:
            return {
                "tool": "create_order_draft",
                "arguments": {"notes": "Monthly recurring auto-reorder"},
            }

        return {"answer": self.generate(prompt, system_prompt, temperature)}


class LiveHTTPLLMAdapter(LLMAdapter):
    """Adapter for hosted LLMs (Gemini / OpenAI API) via standard HTTP requests."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        model_name: str | None = None,
        base_url: str | None = None,
        timeout: int = 15,
    ) -> None:
        self.provider = provider.lower().strip()
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = (base_url or OPENAI_COMPATIBLE_BASE_URLS.get(self.provider, "")).rstrip("/")
        self.model_name = model_name or DEFAULT_MODELS.get(self.provider, "gpt-4o-mini")

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        # Fallback to Mock if no key is configured
        if not self.api_key or self.api_key.startswith("mock") or self.api_key == "change-me":
            return MockLLMAdapter().generate(prompt, system_prompt, temperature)

        import json
        import urllib.request

        if "gemini" in self.provider:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"
            contents = []
            if system_prompt:
                contents.append(
                    {"role": "user", "parts": [{"text": f"System context:\n{system_prompt}"}]}
                )
                contents.append(
                    {
                        "role": "model",
                        "parts": [
                            {"text": "Understood. I will strictly follow these instructions."}
                        ],
                    }
                )
            contents.append({"role": "user", "parts": [{"text": prompt}]})

            payload = {
                "contents": contents,
                "generationConfig": {"temperature": temperature},
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception:
                # The Gemini URL carries the API key as a query parameter, and a
                # provider error body can echo request content back, so the raw
                # exception is never returned to the caller or logged verbatim.
                logger.warning(
                    "LLM request failed (provider=%s, model=%s)", self.provider, self.model_name
                )
                return PROVIDER_ERROR_MESSAGE

        # Standard OpenAI-compatible completion
        url = f"{self.base_url}/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"].strip()
        except Exception:
            logger.warning(
                "LLM request failed (provider=%s, model=%s)", self.provider, self.model_name
            )
            return PROVIDER_ERROR_MESSAGE

    def generate_json(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        import json

        text = self.generate(
            prompt
            + "\n\nReply STRICTLY with a valid JSON object. Do not include markdown or backticks.",
            system_prompt=system_prompt,
            temperature=temperature,
        )
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("\n", 1)[0]
        try:
            return json.loads(cleaned)
        except Exception:
            return {"raw_text": text}


def get_llm_adapter() -> LLMAdapter:
    """Factory to retrieve the configured LLM adapter.

    Falls back to the deterministic mock whenever no real provider is
    configured, so the app runs offline and in CI (Rule 72).
    """
    settings = get_settings()
    provider = (settings.llm_provider or "mock").strip().lower()
    api_key = (settings.llm_api_key or "").strip()

    if provider in ("", "mock") or not api_key:
        return MockLLMAdapter()
    if api_key.startswith("mock") or api_key.startswith("change-me"):
        return MockLLMAdapter()

    if provider != "gemini" and not (
        settings.llm_base_url or provider in OPENAI_COMPATIBLE_BASE_URLS
    ):
        logger.warning(
            "LLM_PROVIDER=%r has no known base URL and no LLM_BASE_URL was set; "
            "falling back to the mock adapter.",
            provider,
        )
        return MockLLMAdapter()

    return LiveHTTPLLMAdapter(
        provider=provider,
        api_key=api_key,
        model_name=settings.llm_model or None,
        base_url=settings.llm_base_url or None,
        timeout=settings.llm_timeout_seconds,
    )


def describe_active_adapter() -> dict[str, Any]:
    """Describe the configured adapter. Never includes the API key."""
    settings = get_settings()
    provider = (settings.llm_provider or "mock").strip().lower()
    api_key = (settings.llm_api_key or "").strip()
    using_mock = (
        provider in ("", "mock") or not api_key or api_key.startswith(("mock", "change-me"))
    )
    return {
        "provider": provider,
        "model": settings.llm_model or DEFAULT_MODELS.get(provider, ""),
        "base_url": settings.llm_base_url or OPENAI_COMPATIBLE_BASE_URLS.get(provider, ""),
        "using_mock": using_mock,
    }
