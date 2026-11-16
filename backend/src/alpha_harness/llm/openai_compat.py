"""One client for every provider: each of them, Google included, speaks chat-completions.

Built on the OpenAI SDK, which also streams. Google's endpoint is its OpenAI-compatible
one, which holds the answer to a JSON schema and takes a reasoning effort.
"""

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from openai import APIStatusError, AsyncOpenAI, omit

if TYPE_CHECKING:
    import httpx2
    from openai.types.chat import ChatCompletionMessageParam
    from openai.types.chat.completion_create_params import ResponseFormat
    from openai.types.shared import ReasoningEffort

log = structlog.get_logger(__name__)

#: Waits before sending again after an overload: an overloaded model usually clears in seconds.
OVERLOADED_WAITS = (2.0, 5.0)
#: "Overloaded", in every provider's dialect: 503 for most, 529 for Anthropic, and the 502/504
#: a gateway answers with when the model behind it did not.
OVERLOADED = frozenset({502, 503, 504, 529})

#: How much of a provider's error is kept. All of it that matters: whether a Google 429 is
#: the per-minute or the per-day limit is said only in its ``details``, near the end.
DETAIL_CHARS = 2_000


class ProviderError(RuntimeError):
    """A provider's refusal: its HTTP status, and its own words about why."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status} {detail}")
        self.status = status


class OpenAICompatible:
    """A chat-completions client for one key, on a connection pool it shares."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        http: httpx2.AsyncClient,
        *,
        headers: dict[str, str] | None = None,
        max_tokens: int | None = None,
    ) -> None:
        # No SDK retries: a 429 means this key is spent for now, which rotation answers by
        # moving to the next key, and sending it again only burns the day's requests.
        self._sdk = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=http,
            max_retries=0,
            default_headers=headers or None,
        )
        #: Sent only where a provider needs it: Anthropic requires an output ceiling, and
        #: without one a batch of twenty Alphas can be cut off mid-JSON.
        self._max_tokens = max_tokens

    async def generate(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float | None,
        schema: dict[str, Any] | None,
        reasoning_effort: ReasoningEffort = None,
    ) -> tuple[str, dict[str, Any]]:
        """One completion as JSON: its text, and the ``usage`` the provider reported.

        ``schema`` holds the answer to it; without one only a JSON object is asked for. Not
        every provider honours either, which is why the callers also ask for JSON in the
        prompt itself and parse defensively. A 503 is sent again after a short wait.
        """
        for wait in OVERLOADED_WAITS:
            try:
                return await self._complete(
                    model, system, user, temperature, schema, reasoning_effort
                )
            except ProviderError as exc:
                if exc.status not in OVERLOADED:
                    raise
            log.info("llm.overloaded", model=model, wait=wait)
            await asyncio.sleep(wait)
        return await self._complete(model, system, user, temperature, schema, reasoning_effort)

    async def _complete(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float | None,
        schema: dict[str, Any] | None,
        reasoning_effort: ReasoningEffort,
    ) -> tuple[str, dict[str, Any]]:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        response_format: ResponseFormat = (
            {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
            if schema is not None
            else {"type": "json_object"}
        )
        try:
            completion = await self._sdk.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature if temperature is not None else omit,
                response_format=response_format,
                reasoning_effort=reasoning_effort if reasoning_effort is not None else omit,
                max_tokens=self._max_tokens if self._max_tokens is not None else omit,
            )
        except APIStatusError as exc:
            raise ProviderError(exc.status_code, _detail(exc.response)) from exc
        choice = completion.choices[0] if completion.choices else None
        text = choice.message.content if choice else None
        usage = completion.usage.model_dump() if completion.usage else {}
        if choice is not None and choice.finish_reason == "length":
            # Cut off at the output ceiling. The text is still returned, since a parser can
            # recover the complete items, but the caller is told why the rest is missing.
            usage["truncated"] = True
            log.warning("llm.truncated", model=model)
        return text or "", usage

    async def models(self, **query: object) -> list[str]:
        """Model ids this key can reach. Not billed against the generation quota."""
        try:
            page = await self._sdk.models.list(extra_query=query or None)
        except APIStatusError as exc:
            raise ProviderError(exc.status_code, _detail(exc.response)) from exc
        # Google's ids come back as ``models/gemini-…``; its chat endpoint takes either.
        return sorted({model.id.removeprefix("models/") for model in page.data})


def _detail(response: httpx2.Response) -> str:
    """The provider's own words about what went wrong, details included."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:DETAIL_CHARS]
    # Google's compatible endpoint answers with a list holding the one error.
    if isinstance(body, list) and body and isinstance(body[0], dict):
        body = body[0]
    error = body.get("error") if isinstance(body, dict) else None
    return str(error or body)[:DETAIL_CHARS]
