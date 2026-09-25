"""Provider-side web search for a warm banking study.

The account's own key is sent only on this request. Results are scrubbed
before they are stored. The generator does not call the provider.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

from app.providers import get_provider
from sectors.steering import scrub_text


class DeepSearchError(Exception):
    def __init__(self, provider: str, status: int, detail: str) -> None:
        self.provider = provider
        self.status = status
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class DeepSearchResult:
    text: str
    sources: list[str] = field(default_factory=list)
    model: str = ""


def default_query(
    sub_domains: list[str],
    language: str,
    *,
    label: str = "banking",
    events: list[str] | None = None,
) -> str:
    scope = ", ".join(sub_domains) if sub_domains else label
    names = events or [
        "application.submitted",
        "kyc.passed",
        "account.opened",
        "account.funded",
        "card.issued",
        "card.activated",
        "loan.disbursed",
        "complaint.received",
    ]
    listed = ", ".join(names[:8])
    return (
        f"Research representative {label} customer journeys for {scope}. "
        f"Write in language {language}. "
        "Name public sources, the usual order of business events, channels, products, and currencies. "
        "Do not include personal data, real account numbers, or secrets. "
        f"Use event names such as {listed} only when they fit the scope."
    )


def run_deep_search(
    provider: str,
    query: str,
    *,
    key: str,
    transport: httpx.BaseTransport | None = None,
) -> DeepSearchResult:
    spec = get_provider(provider)
    if not spec.supports_deep_search:
        raise DeepSearchError(spec.id, 0, "provider cannot run deep search")
    base, path, headers, body, parse = _request(spec.id, query, key)
    try:
        with httpx.Client(base_url=base, transport=transport, timeout=90.0) as client:
            response = client.post(path, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise DeepSearchError(spec.id, exc.response.status_code, _safe(exc.response.text, key)) from exc
    except httpx.HTTPError as exc:
        raise DeepSearchError(spec.id, 0, "network error") from exc
    except ValueError as exc:
        raise DeepSearchError(spec.id, 0, "provider returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise DeepSearchError(spec.id, 0, "provider returned invalid JSON")
    text, sources, model = parse(payload)
    if not text.strip():
        raise DeepSearchError(spec.id, 0, "provider returned no text")
    return DeepSearchResult(text=text.strip(), sources=_http_sources(sources), model=model[:120])


def _request(provider: str, query: str, key: str):
    if provider == "openai":
        model = os.environ.get("OPENAI_SEARCH_MODEL", "gpt-5.5").strip() or "gpt-5.5"
        return (
            _base("OPENAI_BASE_URL", "https://api.openai.com"),
            "/v1/responses",
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            {
                "model": model,
                "input": query,
                "tools": [{"type": "web_search"}],
                "include": ["web_search_call.action.sources"],
            },
            _responses_payload,
        )
    if provider == "anthropic":
        model = os.environ.get("ANTHROPIC_SEARCH_MODEL", "claude-sonnet-4-5").strip() or "claude-sonnet-4-5"
        return (
            _base("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
            "/v1/messages",
            {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            {
                "model": model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": query}],
                "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            },
            _anthropic_payload,
        )
    if provider == "google":
        model = os.environ.get("GOOGLE_SEARCH_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
        return (
            _base("GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com"),
            f"/v1beta/models/{model}:generateContent",
            {"x-goog-api-key": key, "Content-Type": "application/json"},
            {
                "contents": [{"role": "user", "parts": [{"text": query}]}],
                "tools": [{"google_search": {}}],
            },
            _google_payload,
        )
    if provider == "xai":
        model = os.environ.get("XAI_SEARCH_MODEL", "grok-4.5").strip() or "grok-4.5"
        return (
            _base("XAI_BASE_URL", "https://api.x.ai"),
            "/v1/responses",
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            {
                "model": model,
                "input": [{"role": "user", "content": query}],
                "tools": [{"type": "web_search"}],
            },
            _responses_payload,
        )
    raise DeepSearchError(provider, 0, "provider cannot run deep search")


def _base(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip().rstrip("/")
    return value or default


def _responses_payload(payload: dict) -> tuple[str, list[str], str]:
    texts: list[str] = []
    sources: list[str] = []
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        texts.append(output_text)
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            action = item.get("action") or {}
            if isinstance(action, dict):
                for source in action.get("sources") or []:
                    if isinstance(source, dict) and source.get("url"):
                        sources.append(str(source["url"]))
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"output_text", "text"} and part.get("text"):
                texts.append(str(part["text"]))
            for note in part.get("annotations") or []:
                if isinstance(note, dict) and note.get("url"):
                    sources.append(str(note["url"]))
    return "\n".join(texts).strip(), sources, str(payload.get("model") or "")


def _anthropic_payload(payload: dict) -> tuple[str, list[str], str]:
    texts: list[str] = []
    sources: list[str] = []
    for block in payload.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and block.get("text"):
            texts.append(str(block["text"]))
            for cite in block.get("citations") or []:
                if isinstance(cite, dict) and cite.get("url"):
                    sources.append(str(cite["url"]))
        if block.get("type") == "web_search_tool_result":
            for result in block.get("content") or []:
                if isinstance(result, dict) and result.get("url"):
                    sources.append(str(result["url"]))
    return "\n".join(texts).strip(), sources, str(payload.get("model") or "")


def _google_payload(payload: dict) -> tuple[str, list[str], str]:
    texts: list[str] = []
    sources: list[str] = []
    candidates = payload.get("candidates") or []
    if candidates and isinstance(candidates[0], dict):
        content = candidates[0].get("content") or {}
        for part in content.get("parts") or []:
            if isinstance(part, dict) and part.get("text"):
                texts.append(str(part["text"]))
        meta = candidates[0].get("groundingMetadata") or candidates[0].get("grounding_metadata") or {}
        chunks = meta.get("groundingChunks") or meta.get("grounding_chunks") or []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            web = chunk.get("web") or {}
            uri = web.get("uri") or web.get("url")
            if uri:
                sources.append(str(uri))
    model = payload.get("modelVersion") or payload.get("model_version") or ""
    return "\n".join(texts).strip(), sources, str(model)


def _http_sources(sources: list[str]) -> list[str]:
    cleaned: list[str] = []
    for item in sources:
        url = item.strip()
        if not url.startswith(("https://", "http://")) or len(url) > 300 or url in cleaned:
            continue
        cleaned.append(url)
        if len(cleaned) == 12:
            break
    return cleaned


def _safe(text: str, key: str) -> str:
    redacted = text.replace(key, "") if key else text
    return scrub_text(redacted)[:240].strip()
