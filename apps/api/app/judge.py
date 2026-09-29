from __future__ import annotations

import json
import time
from typing import Any, Callable, Protocol

import httpx

DEFAULT_JUDGE_MODEL = "qwen3.6:27b"
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


class RubricsUnsupported(JudgeUnavailable):
    """The judge cannot register rubrics: an engine without tenant rubrics, or a judge with no registry."""

    def __init__(self, message: str, request_id: str = "") -> None:
        super().__init__(502, message, request_id)


class RubricLimitReached(JudgeUnavailable):
    """The platform tenant holds as many rubrics as the engine allows."""

    def __init__(self, message: str, request_id: str = "") -> None:
        super().__init__(502, message, request_id)


class Judge(Protocol):
    def run_eval(
        self,
        *,
        rubric: str,
        prompt: str,
        response: str,
        expected: str | None = None,
        response_b: str | None = None,
        judge_model: str | None = None,
        repeats: int = 1,
        temperature: float = 0.0,
    ) -> dict[str, Any]: ...

    def register_rubric(self, definition: dict[str, Any]) -> dict[str, Any]: ...


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
    """Calls llm_inference_engine's /v1/evals routes. The bearer token is the tenant key, so the rubrics it
    registers belong to the platform tenant."""

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
        judge_model: str | None = None,
        repeats: int = 1,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """One verdict, or `repeats` of them at `temperature` with seeds 0, 1, 2, ...; the first is also the top level."""
        body: dict[str, Any] = {"rubric": rubric, "prompt": prompt, "response": response, "seed": 0}
        if judge_model or self.judge_model:
            body["judge_model"] = judge_model or self.judge_model
        if expected is not None:
            body["expected"] = expected
        if response_b is not None:
            body["response_b"] = response_b
        if repeats > 1:
            body["n"] = repeats
            body["temperature"] = temperature
        result = self._post("/v1/evals/run", body)
        try:
            payload = result.json()
            # An engine without repeats returns only `verdict`; that is one repeat, whatever was asked.
            verdicts = [_verdict(item) for item in payload.get("verdicts") or [payload["verdict"]]]
            if repeats > 1:
                # A sampled repeat can come back as an empty object on one seed (qwen3.8:27b did on seed 1 for pairwise),
                # so it is asked once more on a seed no repeat used. A retry that is still empty stays unreadable.
                for index, verdict in enumerate(verdicts):
                    if not _empty(verdict):
                        continue
                    try:
                        again = self._post("/v1/evals/run", {**{k: v for k, v in body.items() if k != "n"}, "seed": repeats + index, "temperature": temperature})
                        retried = _verdict(again.json()["verdict"])
                    except (JudgeUnavailable, ValueError, KeyError, TypeError):
                        continue
                    if retried["readable"]:
                        verdicts[index] = {**retried, "retried": True}
            return {
                **verdicts[0],
                "verdicts": verdicts,
                "judge_model": payload.get("judge_model") or "",
                "duration_ms": float(payload.get("duration_ms") or 0),
                "rubric_digest": payload.get("rubric_digest"),
            }
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise JudgeUnavailable(502, "The judge returned a response the studio could not read.", _request_id(result)) from exc

    def register_rubric(self, definition: dict[str, Any]) -> dict[str, Any]:
        """Register or replace a rubric for the platform tenant. Returns its name and the engine's digest of it."""
        result = self._post("/v1/evals/rubrics", definition, missing="The judge's engine does not accept rubrics. It needs llm_inference_engine with tenant rubrics.")
        try:
            payload = result.json()
            return {"name": payload["name"], "digest": payload["digest"]}
        except (ValueError, KeyError, TypeError) as exc:
            raise JudgeUnavailable(502, "The judge returned a response the studio could not read.", _request_id(result)) from exc

    def list_rubrics(self) -> list[dict[str, Any]]:
        """The built-in rubrics and the platform tenant's own, as the engine lists them."""
        try:
            result = self._client.get("/v1/evals/rubrics", headers={"Authorization": f"Bearer {self._api_key}"})
        except httpx.HTTPError as exc:
            raise JudgeUnavailable(503, "The judge could not be reached. Check INFERENCE_ENGINE_BASE_URL.") from exc
        if not result.is_success:
            raise self._failure(result)
        try:
            return list(result.json()["data"])
        except (ValueError, KeyError, TypeError) as exc:
            raise JudgeUnavailable(502, "The judge returned a response the studio could not read.", _request_id(result)) from exc

    def delete_rubric(self, name: str) -> bool:
        """Remove one of the platform tenant's rubrics. False when the engine did not have it."""
        try:
            result = self._client.delete(f"/v1/evals/rubrics/{name}", headers={"Authorization": f"Bearer {self._api_key}"})
        except httpx.HTTPError as exc:
            raise JudgeUnavailable(503, "The judge could not be reached. Check INFERENCE_ENGINE_BASE_URL.") from exc
        if result.status_code == 404:
            return False
        if result.is_success:
            return True
        raise self._failure(result)

    def _post(self, path: str, body: dict[str, Any], missing: str | None = None) -> httpx.Response:
        for attempt in range(2):
            try:
                result = self._client.post(
                    path,
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
            if missing and result.status_code in {404, 405} and _engine_type(result) is None:
                raise RubricsUnsupported(missing, _request_id(result))
            raise self._failure(result)
        raise AssertionError("unreachable")

    def _failure(self, result: httpx.Response) -> JudgeUnavailable:
        request_id = _request_id(result)
        status = result.status_code
        kind = _engine_type(result)
        if status in {401, 403}:
            return JudgeUnavailable(502, "The judge rejected the platform key. Check INFERENCE_ENGINE_API_KEY.", request_id)
        if status in {429, 503}:
            return JudgeUnavailable(503, "The judge is busy or starting. Try again shortly.", request_id)
        if status == 504:
            seconds = _engine_detail(result).get("timeout_seconds")
            after = f" after {seconds:g} seconds" if isinstance(seconds, int | float) else ""
            return JudgeUnavailable(504, f"The judge timed out{after}.", request_id)
        if kind == "rubric_limit_reached":
            limit = _engine_detail(result).get("limit")
            return RubricLimitReached(f"The judge's engine holds its limit of {limit} rubrics for this platform.", request_id)
        if kind == "context_length_exceeded":
            detail = _engine_detail(result)
            needed, window = detail.get("requested_tokens"), detail.get("context_window")
            sizes = f" It needed {needed} tokens of a {window}-token window." if needed and window else ""
            return JudgeUnavailable(502, f"A journey did not fit the judge's context window.{sizes} Lower JUDGE_PROMPT_TOKENS.", request_id)
        reason = _engine_message(result).replace(self._api_key, "[redacted]")
        return JudgeUnavailable(502, f"The judge failed with {status}: {reason}", request_id)

    def close(self) -> None:
        self._client.close()


def _empty(verdict: dict[str, Any]) -> bool:
    """A verdict the model answered with an empty JSON object."""
    try:
        return not verdict["readable"] and json.loads(verdict["raw"] or "null") == {}
    except ValueError:
        return False


def _verdict(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("raw") or ""
    return {
        "score": float(item["score"]),
        "parsed": item.get("parsed") or {},
        "raw": raw,
        # The engine scores an unparseable verdict as 0; that is not a real score.
        "readable": item.get("parse_status") != "failed" and bool(raw.strip()),
    }


def _engine_detail(result: httpx.Response) -> dict[str, Any]:
    try:
        payload = result.json()
    except ValueError:
        return {}
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return detail if isinstance(detail, dict) else {}


def _engine_type(result: httpx.Response) -> str | None:
    """The engine's typed error, such as `context_length_exceeded`; None for a bare HTTP error like an unknown route."""
    kind = _engine_detail(result).get("type")
    return str(kind) if kind else None


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
