from __future__ import annotations

from typing import Any, Protocol

import httpx


class EvalNotConfigured(RuntimeError):
    pass


class Judge(Protocol):
    def run_eval(
        self,
        *,
        rubric: str,
        prompt: str,
        response: str,
        expected: str | None = None,
        response_b: str | None = None,
    ) -> dict[str, Any]: ...


class InferenceEngineClient:
    """Calls llm_inference_engine POST /v1/evals/run. The bearer token is the tenant key."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        tenant: str,
        org_id: str,
        key_id: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise EvalNotConfigured("INFERENCE_ENGINE_API_KEY is missing")
        if not base_url:
            raise EvalNotConfigured("INFERENCE_ENGINE_BASE_URL is missing")
        self.tenant = tenant
        self.org_id = org_id
        self.key_id = key_id
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            transport=transport,
            timeout=60.0,
        )

    def run_eval(
        self,
        *,
        rubric: str,
        prompt: str,
        response: str,
        expected: str | None = None,
        response_b: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"rubric": rubric, "prompt": prompt, "response": response, "seed": 0}
        if expected is not None:
            body["expected"] = expected
        if response_b is not None:
            body["response_b"] = response_b
        result = self._client.post(
            "/v1/evals/run",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=body,
        )
        result.raise_for_status()
        payload = result.json()
        verdict = payload["verdict"]
        return {
            "score": float(verdict["score"]),
            "parsed": verdict.get("parsed") or {},
            "raw": verdict.get("raw") or "",
            "judge_model": payload.get("judge_model") or "",
            "duration_ms": float(payload.get("duration_ms") or 0),
        }

    def close(self) -> None:
        self._client.close()
