# ruff: noqa: S101  -- a test file asserts
# pyright: reportPrivateUsage=false
"""The LLM fixes: Anthropic's headers, pasted-key cleaning, provider-aware errors, salvage."""

import asyncio

import httpx2
import pytest

from alpha_harness.labs import power_pool as pp
from alpha_harness.llm import providers
from alpha_harness.llm.keys import clean_key, prefix_problem
from alpha_harness.llm.openai_compat import OpenAICompatible, ProviderError
from alpha_harness.llm.service import _is_rate_limit, _plain_reason

KEY = "sk-ant-api03-abcdefghijklmnop"


def test_anthropic_sends_its_native_auth_headers() -> None:
    headers = providers.get("anthropic").headers_for(KEY)
    assert headers == {"x-api-key": KEY, "anthropic-version": "2023-06-01"}
    assert providers.get("google").headers_for("AIzaX") == {}


def test_models_list_reaches_anthropic_with_x_api_key() -> None:
    seen: dict[str, str] = {}

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.update(request.headers)
        if "x-api-key" not in request.headers:
            return httpx2.Response(401, json={"error": {"message": "x-api-key header is required"}})
        return httpx2.Response(200, json={"data": [{"id": "claude-x", "object": "model"}]})

    async def listed() -> list[str]:
        spec = providers.get("anthropic")
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(answer)) as http:
            client = OpenAICompatible(spec.base_url, KEY, http, headers=spec.headers_for(KEY))
            return await client.models(limit=1000)

    assert asyncio.run(listed()) == ["claude-x"]
    assert seen["x-api-key"] == KEY
    assert seen["anthropic-version"] == "2023-06-01"


@pytest.mark.parametrize(
    "pasted",
    [
        f"x-api-key: {KEY}",
        f"Authorization: Bearer {KEY}",
        f'"{KEY}"',
        f"  {KEY}\n",
        f"ANTHROPIC_API_KEY={KEY}",
    ],
)
def test_pasted_wrappers_are_removed(pasted: str) -> None:
    assert clean_key(pasted) == KEY


def test_a_key_under_the_wrong_provider_is_refused() -> None:
    assert prefix_problem(KEY, "anthropic") is None
    assert "Anthropic" in (prefix_problem(KEY, "openai") or "")
    assert "Anthropic" in (prefix_problem("AIzaSyX", "anthropic") or "")
    assert prefix_problem("sk-or-v1-x", "openrouter") is None
    assert prefix_problem("sk-proj-x", "openai") is None


def test_errors_name_the_provider_not_google() -> None:
    error = ProviderError(401, "{'type': 'authentication_error', 'message': 'invalid x-api-key'}")
    reason = _plain_reason(error, "anthropic") or ""
    assert "Anthropic" in reason
    assert "Google" not in reason


def test_an_empty_balance_is_not_a_rate_limit() -> None:
    error = ProviderError(429, "insufficient_quota: You exceeded your current quota")
    assert _is_rate_limit(error) == (False, False)
    assert "billing" in (_plain_reason(error, "openai") or "")


def test_a_cut_off_answer_keeps_its_complete_alphas() -> None:
    text = '{"alphas": [{"expression": "rank(a)"}, {"expression": "rank(b)"}, {"expression": "ra'
    assert pp.parse_alphas(text) == [{"expression": "rank(a)"}, {"expression": "rank(b)"}]


def test_prose_around_the_json_does_not_lose_it() -> None:
    text = 'Sure! {"alphas": [{"expression": "rank(a)"}]} Let me know.'
    assert pp.parse_alphas(text) == [{"expression": "rank(a)"}]
