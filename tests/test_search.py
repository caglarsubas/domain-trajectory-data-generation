import json

import httpx

from app.search import DeepSearchError, run_deep_search


def _transport(status: int, payload: dict, capture: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        capture["path"] = request.url.path
        capture["authorization"] = request.headers.get("authorization")
        capture["api_key"] = request.headers.get("x-api-key")
        capture["goog"] = request.headers.get("x-goog-api-key")
        capture["body"] = json.loads(request.content.decode())
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def test_openai_web_search_request_and_citations():
    seen = {}
    result = run_deep_search(
        "openai",
        "retail deposits",
        key="sk-openai-search-key",
        transport=_transport(
            200,
            {
                "model": "gpt-5.5",
                "output": [
                    {"type": "web_search_call", "action": {"sources": [{"type": "url", "url": "https://example.test/bian"}]}},
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Savings accounts are funded in USD.",
                                "annotations": [{"type": "url_citation", "url": "https://example.test/usd"}],
                            }
                        ],
                    },
                ],
            },
            seen,
        ),
    )
    assert seen["path"] == "/v1/responses"
    assert seen["authorization"] == "Bearer sk-openai-search-key"
    assert seen["body"]["tools"] == [{"type": "web_search"}]
    assert seen["body"]["model"] == "gpt-5.5"
    assert "USD" in result.text
    assert result.sources == ["https://example.test/bian", "https://example.test/usd"]
    assert result.model == "gpt-5.5"


def test_anthropic_google_and_xai_use_provider_search_tools():
    anthropic = {}
    anthropic_result = run_deep_search(
        "anthropic",
        "kyc",
        key="sk-ant-search-key-1234",
        transport=_transport(
            200,
            {
                "model": "claude-sonnet-4-5",
                "content": [
                    {
                        "type": "text",
                        "text": "Identity checks pass after document review.",
                        "citations": [{"type": "web_search_result_location", "url": "https://example.test/kyc"}],
                    }
                ],
            },
            anthropic,
        ),
    )
    assert anthropic["path"] == "/v1/messages"
    assert anthropic["api_key"] == "sk-ant-search-key-1234"
    assert anthropic["authorization"] is None
    assert anthropic["body"]["tools"][0]["type"] == "web_search_20250305"
    assert anthropic_result.sources == ["https://example.test/kyc"]

    google = {}
    google_result = run_deep_search(
        "google",
        "cards",
        key="AIza-google-search-key-0001",
        transport=_transport(
            200,
            {
                "modelVersion": "gemini-2.5-flash",
                "candidates": [
                    {
                        "content": {"parts": [{"text": "A debit card is issued, then activated."}]},
                        "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://example.test/cards"}}]},
                    }
                ],
            },
            google,
        ),
    )
    assert google["path"] == "/v1beta/models/gemini-2.5-flash:generateContent"
    assert google["goog"] == "AIza-google-search-key-0001"
    assert "key=" not in google["path"]
    assert google["body"]["tools"] == [{"google_search": {}}]
    assert google_result.sources == ["https://example.test/cards"]

    xai = {}
    run_deep_search(
        "xai",
        "complaints",
        key="xai-search-key-0000001",
        transport=_transport(
            200,
            {
                "model": "grok-4.5",
                "output_text": None,
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "A complaint is received on the mobile channel."}]}
                ],
            },
            xai,
        ),
    )
    assert xai["path"] == "/v1/responses"
    assert xai["authorization"] == "Bearer xai-search-key-0000001"
    assert xai["body"]["tools"] == [{"type": "web_search"}]
    assert xai["body"]["input"][0]["role"] == "user"


def test_provider_error_does_not_keep_the_key():
    secret = "sk-leaked-search-key"
    try:
        run_deep_search(
            "openai",
            "query",
            key=secret,
            transport=_transport(401, {"error": {"message": f"bad key {secret}"}}, {}),
        )
    except DeepSearchError as exc:
        assert secret not in exc.detail
        assert exc.status == 401
        return
    raise AssertionError("expected DeepSearchError")
