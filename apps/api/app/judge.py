from __future__ import annotations

import time
from typing import Any, Callable, Protocol

import httpx

DEFAULT_JUDGE_MODEL = "qwen3.8:27b"
# The engine allows a completion 240 s; the client waits longer so the engine reports its own timeout.
JUDGE_TIMEOUT_SECONDS = 300.0
RETRY_AFTER_CAP_SECONDS = 30.0


class EvalNotConfigured(RuntimeError):
    pass


class JudgeUnavailable(RuntimeError):
    """The engine could not return a verdict. `status` is the HTTP status the studio API should answer with."""

    def __init__(self, status: int, message: str, request_id: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.request_id = request_id

    def detail(self) -> str:
        if self.request_id:
            return f"{self.message} Engine request id: {self.request_id}."
        return self.message


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


def normalize_base_url(value: str) -> str:
    """Return the engine origin without a trailing slash or /v1, or raise EvalNotConfigured."""
    url = value.strip()
    while True:
        trimmed = url.rstrip("/.")
        if trimmed.endswith("/v1"):
            trimmed = trimmed[: -len("/v1")]
        if trimmed == url:
            break
        url = trimmed
    parsed = httpx.URL(url) if url else None
    if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.host:
        raise EvalNotConfigured(
            "INFERENCE_ENGINE_BASE_URL must be the engine origin, such as https://engine.example.com"
        )
    return url


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
        judge_model: str = DEFAULT_JUDGE_MODEL,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise EvalNotConfigured("INFERENCE_ENGINE_API_KEY is missing")
        if not base_url:
            raise EvalNotConfigured("INFERENCE_ENGINE_BASE_URL is missing")
        self.tenant = tenant
        self.org_id = org_id
        self.key_id = key_id
        self.judge_model = judge_model
        self._api_key = api_key
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=normalize_base_url(base_url),
            transport=transport,
            timeout=httpx.Timeout(JUDGE_TIMEOUT_SECONDS, connect=10.0),
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
        if self.judge_model:
            body["judge_model"] = self.judge_model
        if expected is not None:
            body["expected"] = expected
        if response_b is not None:
            body["response_b"] = response_b
        result = self._post(body)
        try:
            payload = result.json()
            verdict = payload["verdict"]
            raw = verdict.get("raw") or ""
            return {
                "score": float(verdict["score"]),
                "parsed": verdict.get("parsed") or {},
                "raw": raw,
                # The engine scores an unparseable verdict as 0; that is not a real score.
                "readable": verdict.get("parse_status") != "failed" and bool(raw.strip()),
                "judge_model": payload.get("judge_model") or "",
                "duration_ms": float(payload.get("duration_ms") or 0),
            }
        except (ValueError, KeyError, TypeError) as exc:
            raise JudgeUnavailable(502, "The judge returned a response the studio could not read.", _request_id(result)) from exc

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        for attempt in range(2):
            try:
                result = self._client.post(
                    "/v1/evals/run",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=body,
                )
            except httpx.TimeoutException as exc:
                raise JudgeUnavailable(504, f"The judge did not answer within {int(JUDGE_TIMEOUT_SECONDS)} seconds.") from exc
            except httpx.TransportError as exc:
                raise JudgeUnavailable(503, "The judge could not be reached. Check INFERENCE_ENGINE_BASE_URL.") from exc
            if result.status_code in {429, 503} and attempt == 0:
                wait = _retry_after(result)
                if wait is not None:
                    self._sleep(wait)
                    continue
            if result.is_success:
                return result
            raise self._failure(result)
        raise AssertionError("unreachable")

    def _failure(self, result: httpx.Response) -> JudgeUnavailable:
        request_id = _request_id(result)
        status = result.status_code
        if status in {401, 403}:
            return JudgeUnavailable(502, "The judge rejected the platform key. Check INFERENCE_ENGINE_API_KEY.", request_id)
        if status in {429, 503}:
            return JudgeUnavailable(503, "The judge is busy or starting. Try again shortly.", request_id)
        if status == 504:
            return JudgeUnavailable(504, "The judge timed out.", request_id)
        reason = _engine_message(result).replace(self._api_key, "[redacted]")
        return JudgeUnavailable(502, f"The judge failed with {status}: {reason}", request_id)

    def close(self) -> None:
        self._client.close()


def _request_id(result: httpx.Response) -> str:
    return result.headers.get("x-request-id", "")


def _retry_after(result: httpx.Response) -> float | None:
    raw = result.headers.get("retry-after", "").strip()
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if seconds < 0 or seconds > RETRY_AFTER_CAP_SECONDS:
        return None
    return seconds


def _engine_message(result: httpx.Response) -> str:
    try:
        payload = result.json()
    except ValueError:
        return result.reason_phrase or "no detail"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:300]
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return str(detail)[:300] if detail else (result.reason_phrase or "no detail")
