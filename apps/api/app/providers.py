from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    supports_deep_search: bool

    def validate(self, secret: str) -> str | None:
        if not secret or secret != secret.strip():
            return "key is empty"
        if self.id == "openai" and not secret.startswith("sk-"):
            return "OpenAI keys start with sk-"
        if self.id == "anthropic" and not secret.startswith("sk-ant-"):
            return "Anthropic keys start with sk-ant-"
        if self.id == "google" and len(secret) < 20:
            return "Google keys must be at least 20 characters"
        if self.id == "xai" and not secret.startswith("xai-"):
            return "xAI keys start with xai-"
        return None

    def deep_search(self, query: str, *, key: str):
        from app.search import run_deep_search

        return run_deep_search(self.id, query, key=key)


PROVIDERS: dict[str, ProviderSpec] = {
    spec.id: spec
    for spec in (
        ProviderSpec("openai", "OpenAI", True),
        ProviderSpec("anthropic", "Anthropic", True),
        ProviderSpec("google", "Google", True),
        ProviderSpec("xai", "xAI", True),
    )
}


def get_provider(provider_id: str) -> ProviderSpec:
    try:
        return PROVIDERS[provider_id]
    except KeyError as exc:
        raise ValueError(f"unknown provider: {provider_id}") from exc
