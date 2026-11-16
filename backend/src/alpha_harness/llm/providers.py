"""Which assistants can answer, and how to get a key for each.

Every provider here has a **free tier that needs no card**. That is the whole selection
rule: the assistant is optional, so a provider that asks for payment details first turns an
optional convenience into a purchase decision.

All of them speak the OpenAI chat-completions protocol, Google through its compatible
endpoint, which is why one small client in :mod:`.openai_compat` serves every one.

**A key only ever answers for its own provider**, so rotation filters on provider before it
looks at budget.

**No models or limits live here.** Both change every few weeks, so the user sets each model
up with the limits their provider shows them; ``limits_url`` is where to look.
"""

from dataclasses import dataclass, field

from ..schemas import Out


@dataclass(frozen=True, slots=True)
class Provider:
    """One assistant, and what it takes to start using it."""

    id: str
    label: str
    #: Where its chat-completions endpoint lives.
    base_url: str
    #: Where to get a key. Shown as a link, because "search for it" loses people.
    onboarding_url: str
    #: Where the provider shows its rate limits, the account's own page where there is one.
    limits_url: str
    #: What a key from this provider looks like, so a pasted wrong one is caught early.
    key_hint: str
    #: True for a provider that bills the user. Kept apart everywhere it is shown, and its
    #: keys are refused without a daily cap — see :meth:`KeyStore.add`.
    paid: bool = False
    #: What a paid tier bills, in plain words; shown above its key field.
    tier_note: str = ""
    #: What every key of this provider must start with, so a key pasted under the wrong
    #: provider is refused when it is added instead of failing later as "invalid header".
    key_prefixes: tuple[str, ...] = ()
    #: Prefixes that belong to *another* provider and so are never right here.
    foreign_prefixes: tuple[str, ...] = ()
    #: Sent with every request, the key among them where ``{key}`` appears. For a provider
    #: whose native endpoints do not take the Bearer token the OpenAI SDK sends.
    headers: dict[str, str] = field(default_factory=dict)
    #: An output ceiling to send, for a provider that requires one or cuts answers short.
    max_tokens: int | None = None
    #: False where ``response_format`` is rejected rather than ignored.
    send_response_format: bool = True

    def headers_for(self, key: str) -> dict[str, str]:
        return {name: value.replace("{key}", key) for name, value in self.headers.items()}


class LLMProvider(Out):
    """:class:`Provider` on the wire."""

    id: str
    label: str
    onboarding_url: str
    limits_url: str
    key_hint: str
    tier_note: str
    #: True for a provider that bills the user rather than offering a free tier.
    paid: bool


class LLMProviders(Out):
    providers: list[LLMProvider]
    default: str
    paid_note: str


PROVIDERS: dict[str, Provider] = {
    "google": Provider(
        id="google",
        label="Google AI Studio",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        onboarding_url="https://aistudio.google.com/apikey",
        limits_url="https://aistudio.google.com/rate-limit",
        key_hint="AIza…",
        key_prefixes=("AIza",),
    ),
    "groq": Provider(
        id="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        onboarding_url="https://console.groq.com/keys",
        limits_url="https://console.groq.com/settings/limits",
        key_hint="gsk_…",
    ),
    "cerebras": Provider(
        id="cerebras",
        label="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        onboarding_url="https://cloud.cerebras.ai/",
        limits_url="https://inference-docs.cerebras.ai/support/rate-limits",
        key_hint="csk-…",
    ),
    "openrouter": Provider(
        id="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        onboarding_url="https://openrouter.ai/keys",
        limits_url="https://openrouter.ai/docs/api-reference/limits",
        key_hint="sk-or-…",
        key_prefixes=("sk-or-",),
    ),
    "nvidia": Provider(
        id="nvidia",
        label="NVIDIA NIM",
        base_url="https://integrate.api.nvidia.com/v1",
        onboarding_url="https://build.nvidia.com/",
        limits_url="https://build.nvidia.com/faq",
        key_hint="nvapi-…",
    ),
    "mistral": Provider(
        id="mistral",
        label="Mistral",
        base_url="https://api.mistral.ai/v1",
        onboarding_url="https://console.mistral.ai/api-keys/",
        limits_url="https://admin.mistral.ai/plateforme/limits",
        key_hint="…",
    ),
    "github": Provider(
        id="github",
        label="GitHub Models",
        base_url="https://models.github.ai/inference",
        onboarding_url="https://github.com/settings/tokens",
        limits_url="https://docs.github.com/en/github-models/use-github-models/prototyping-with-ai-models#rate-limits",
        key_hint="ghp_… or github_pat_…",
    ),
    "huggingface": Provider(
        id="huggingface",
        label="Hugging Face",
        base_url="https://router.huggingface.co/v1",
        onboarding_url="https://huggingface.co/settings/tokens",
        limits_url="https://huggingface.co/docs/inference-providers/pricing",
        key_hint="hf_…",
    ),
    # --- bring your own, and your own bill ---------------------------------------------
    #
    # Everything above is free because the assistant is optional and a card turns an
    # optional convenience into a purchase decision. These two are for the user who already
    # pays for one of them and would rather spend that than a free tier's daily limit.
    # They are kept behind their own heading in the UI so the default stays
    # "no card", and a key for either is refused without a daily request cap: the free
    # providers stop on their own, and these stop only when told to.
    "openai": Provider(
        id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        onboarding_url="https://platform.openai.com/api-keys",
        limits_url="https://platform.openai.com/settings/organization/limits",
        key_hint="sk-…",
        foreign_prefixes=("sk-ant-", "sk-or-"),
        tier_note="Your own OpenAI account — billed to you, not free.",
        paid=True,
    ),
    "anthropic": Provider(
        id="anthropic",
        # Anthropic's OpenAI-compatible endpoint, which takes the same Bearer token and the
        # same ``/chat/completions`` shape, so no second client is needed. Anthropic calls
        # it a compatibility layer for evaluation rather than a production path; the ways it
        # differs that matter here are that ``response_format`` is ignored — callers already
        # ask for JSON in the prompt and parse defensively — and that thinking output is not
        # returned, which nothing here reads.
        #
        # fix: only ``/chat/completions`` takes the Bearer token. ``/v1/models`` — which the
        # key check and the model picker call — is Anthropic's *native* endpoint and answers
        # 401 "x-api-key header is required" (then 400 for a missing anthropic-version) to the
        # Bearer-only request the SDK sends, so no Anthropic key could ever be checked or
        # have its models listed. Both headers go on every request; the compatibility
        # endpoint accepts them too.
        label="Anthropic Claude",
        base_url="https://api.anthropic.com/v1",
        onboarding_url="https://platform.claude.com/settings/keys",
        limits_url="https://platform.claude.com/settings/limits",
        key_hint="sk-ant-…",
        key_prefixes=("sk-ant-",),
        tier_note="Your own Anthropic account — billed to you, not free.",
        paid=True,
        headers={"x-api-key": "{key}", "anthropic-version": "2023-06-01"},
        # The Messages API behind the compatibility layer needs a ceiling; twenty Alphas in
        # JSON run to a few thousand tokens, and a cut-off answer is a whole call wasted.
        max_tokens=16_000,
        # Its endpoint now answers 400 "response_format.type: Input should be 'json_schema'"
        # to ``json_object``, so none is sent; the prompt asks for JSON.
        send_response_format=False,
    ),
}

DEFAULT_PROVIDER = "google"

#: Said once, under the paid heading. The cap is the whole point: a free tier stops by
#: itself and a paid account does not, so the app will not hold one of these keys until it
#: has been told where to stop.
PAID_NOTE = (
    "These bill your own account. Nothing here is needed — every provider above is free — "
    "but a key you already pay for is not held to a free tier's daily limit. You set a "
    "daily request cap when you add one, and Alpha Harness stops at it."
)


def get(provider_id: str | None) -> Provider:
    """One provider, or Google when nothing was said."""
    return PROVIDERS.get(provider_id or DEFAULT_PROVIDER, PROVIDERS[DEFAULT_PROVIDER])


def catalogue() -> LLMProviders:
    """Every provider, Google first and the paid ones last, for the screen where a key is
    added. The order is the argument: free is the default because it is first and because
    nothing below the fold is needed to use the app."""
    return LLMProviders(
        providers=[
            LLMProvider.model_validate(p, from_attributes=True)
            for p in sorted(PROVIDERS.values(), key=lambda p: p.paid)
        ],
        default=DEFAULT_PROVIDER,
        paid_note=PAID_NOTE,
    )
