from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


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


@dataclass(frozen=True)
class KeyCheck:
    """The outcome of one authenticated call: valid, rejected, or unreachable."""

    status: str
    detail: str


# A cheap authenticated read per provider: listing models costs nothing and needs a working key.
_CHECKS = {
    "openai": ("OPENAI_BASE_URL", "https://api.openai.com", "/v1/models", lambda key: {"Authorization": f"Bearer {key}"}),
    "anthropic": (
        "ANTHROPIC_BASE_URL",
        "https://api.anthropic.com",
        "/v1/models",
        lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"},
    ),
    "google": ("GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com", "/v1beta/models", lambda key: {"x-goog-api-key": key}),
    "xai": ("XAI_BASE_URL", "https://api.x.ai", "/v1/models", lambda key: {"Authorization": f"Bearer {key}"}),
}


def check_key(provider_id: str, key: str, *, transport: httpx.BaseTransport | None = None) -> KeyCheck:
    """Ask the provider whether it accepts the key. The key goes only to the provider, in a header."""
    spec = get_provider(provider_id)
    env, default, path, headers = _CHECKS[spec.id]
    base = os.environ.get(env, default).strip().rstrip("/") or default
    try:
        with httpx.Client(base_url=base, transport=transport, timeout=15.0) as client:
            response = client.get(path, headers=headers(key))
    except httpx.HTTPError:
        return KeyCheck("unreachable", f"Could not reach {spec.label}.")
    code = response.status_code
    if code == 200:
        return KeyCheck("valid", f"{spec.label} accepted the key.")
    if code == 429:
        return KeyCheck("valid", f"{spec.label} accepted the key but is rate limiting it.")
    # Google answers 400 API_KEY_INVALID for a key it does not know.
    if code in {401, 403} or (spec.id == "google" and code == 400):
        return KeyCheck("rejected", f"{spec.label} rejected the key.")
    return KeyCheck("unreachable", f"{spec.label} answered {code}; the key was not checked.")
