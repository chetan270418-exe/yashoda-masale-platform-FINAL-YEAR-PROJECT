"""LLM Adapter layer (Decision D-010).

Isolates external LLM providers behind an abstract interface.
Protects credentials by ensuring API keys only reside server-side (Rule 33).
Provides a deterministic Mock adapter for unit testing and CI (Rule 72).
"""

import json
import os
import urllib.request
from abc import ABC, abstractmethod
from typing import Any


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
        if (
            "unknown" in lower_prompt
            or "chocolate" in lower_prompt
            or "secret" in lower_prompt
        ):
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
        self, provider: str, api_key: str, model_name: str | None = None
    ) -> None:
        self.provider = provider.lower()
        self.api_key = api_key
        self.model_name = model_name or (
            "gemini-2.0-flash" if "gemini" in self.provider else "gpt-4o-mini"
        )

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        if (
            not self.api_key
            or self.api_key.startswith("mock")
            or self.api_key == "change-me"
        ):
            return MockLLMAdapter().generate(prompt, system_prompt, temperature)

        if "gemini" in self.provider:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model_name}:generateContent?key={self.api_key}"
            )
            contents = []
            if system_prompt:
                contents.append(
                    {
                        "role": "user",
                        "parts": [{"text": f"System context:\n{system_prompt}"}],
                    }
                )
                contents.append(
                    {
                        "role": "model",
                        "parts": [
                            {
                                "text": "Understood. I will strictly follow these instructions."
                            }
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
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception as e:
                return f"Error communicating with AI service: {e}"

        # Standard OpenAI-compatible completion
        url = "https://api.openai.com/v1/chat/completions"
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
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return f"Error communicating with AI service: {e}"

    def generate_json(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
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
    """Factory to retrieve configured LLM adapter from environment."""
    provider = os.environ.get("LLM_PROVIDER", "mock").lower()
    api_key = os.environ.get("LLM_API_KEY", "")

    if provider in ("gemini", "openai") and api_key and not api_key.startswith("mock"):
        return LiveHTTPLLMAdapter(provider=provider, api_key=api_key)

    return MockLLMAdapter()
