"""Tests for LLM Adapter and Agent Tools."""

from agent.tools import check_tool_permission
from llm.adapter import LiveHTTPLLMAdapter, MockLLMAdapter


def test_mock_llm_adapter_text():
    adapter = MockLLMAdapter()
    text = adapter.generate("Tell me about Yashoda Turmeric")
    assert "Turmeric" in text or "curcumin" in text


def test_mock_llm_adapter_json_tool_routing():
    adapter = MockLLMAdapter()
    res = adapter.generate_json("User request: Check stock for turmeric")
    assert res.get("tool") == "check_stock"


def test_agent_tool_permissions():
    assert check_tool_permission("get_sales_summary", "owner") is True
    assert check_tool_permission("get_sales_summary", "dealer") is False
    assert check_tool_permission("create_order_draft", "dealer") is True
    assert check_tool_permission("create_order_draft", "consumer") is False


def test_live_adapter_fallback():
    adapter = LiveHTTPLLMAdapter(provider="gemini", api_key="mock-key")
    text = adapter.generate("Hello")
    assert len(text) > 0
