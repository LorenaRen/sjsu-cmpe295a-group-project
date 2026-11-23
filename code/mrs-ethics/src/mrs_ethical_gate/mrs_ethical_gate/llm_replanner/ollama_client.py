"""Ollama API client for LLM-based replanning.

Calls a local Ollama server's native chat API (POST {host}/api/chat) with the standard library
(urllib), so it needs no Python package beyond the project's environment:
- Configurable model selection (default: phi3.5)
- num_ctx sent on every call: Ollama's own default context (2048 or 4096 tokens) is shorter than
  a replan prompt, and Ollama cuts an over-long prompt silently; an answer that fills the window
  is reported as an error instead of being used
- Timeout handling -> LLM_TIMEOUT
- Retry logic for transient errors -> LLM_ERROR when retries do not help
- Structured output using Pydantic JSON schema (Ollama's `format`)
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from mrs_common import reasons as R

# Default configuration
DEFAULT_MODEL = "phi3.5"
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT = 60.0  # seconds
DEFAULT_MAX_RETRIES = 2
DEFAULT_NUM_CTX = 8192   # tokens, prompt + answer (same as the planner's agents)
KEEP_ALIVE = "30m"       # keep the model loaded between calls

# transport(url, body, timeout_s) -> (http status, response bytes); swapped out in tests
Transport = Callable[[str, bytes, float], "tuple[int, bytes]"]


class OllamaHTTPError(Exception):
    """Ollama answered with an HTTP error. 5xx is retried; 4xx (e.g. 404 model not found) is not:
    asking again cannot fix it."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def urllib_transport(url: str, body: bytes, timeout_s: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise TimeoutError(str(e.reason)) from e
        raise ConnectionError(f"cannot reach Ollama at {url}: {e.reason}") from e
    except socket.timeout as e:   # timed out while reading the answer
        raise TimeoutError(str(e)) from e


@dataclass(frozen=True)
class LLMResponse:
    """Response from an LLM call."""
    success: bool
    content: str | None = None          # The generated text/JSON
    error_reason: str | None = None     # One of reasons.LLM_* if failed
    error_detail: str | None = None     # Human-readable error message
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    model_name: str = ""


class OllamaClient:
    """Client for calling Ollama LLM API with structured output support."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        temperature: float = 0.0,  # Deterministic by default for planning
        num_ctx: int = DEFAULT_NUM_CTX,
        transport: Transport = urllib_transport,
    ):
        self.model = model
        self.host = host.rstrip("/").removesuffix("/v1")   # the native API, not the OpenAI-style /v1
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature
        self.num_ctx = num_ctx
        self._transport = transport

    def chat(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Send a chat request to Ollama.

        Args:
            messages: List of {"role": "user"|"assistant"|"system", "content": "..."}
            json_schema: Optional Pydantic model_json_schema() for structured output

        Returns:
            LLMResponse with success=True and content, or success=False with error info
        """
        start_time = time.perf_counter()
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                return self._do_chat(messages, json_schema, start_time)
            except TimeoutError as e:
                return LLMResponse(
                    success=False,
                    error_reason=R.LLM_TIMEOUT,
                    error_detail=f"Request timed out after {self.timeout}s: {e}",
                    latency_ms=(time.perf_counter() - start_time) * 1000,
                    model_name=self.model,
                )
            except OllamaHTTPError as e:
                last_error = e
                if e.status < 500:
                    return LLMResponse(
                        success=False,
                        error_reason=R.LLM_ERROR,
                        error_detail=f"Ollama refused the request: {e}",
                        latency_ms=(time.perf_counter() - start_time) * 1000,
                        model_name=self.model,
                    )
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))  # Exponential backoff
                    continue

        # All retries exhausted
        return LLMResponse(
            success=False,
            error_reason=R.LLM_ERROR,
            error_detail=f"API error after {self.max_retries + 1} attempts: {last_error}",
            latency_ms=(time.perf_counter() - start_time) * 1000,
            model_name=self.model,
        )

    def _do_chat(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any] | None,
        start_time: float,
    ) -> LLMResponse:
        """Execute a single chat request (POST /api/chat, no streaming)."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": KEEP_ALIVE,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        # Add structured output format if schema provided
        if json_schema is not None:
            payload["format"] = json_schema

        status, raw = self._transport(f"{self.host}/api/chat", json.dumps(payload).encode(), self.timeout)
        if status != 200:
            try:
                detail = json.loads(raw).get("error", "")
            except (ValueError, AttributeError):
                detail = raw[:300].decode(errors="replace")
            raise OllamaHTTPError(f"{detail or 'HTTP error'} (status code: {status})", status)

        latency_ms = (time.perf_counter() - start_time) * 1000
        data = json.loads(raw)
        tokens_in = int(data.get("prompt_eval_count") or 0)
        tokens_out = int(data.get("eval_count") or 0)
        if tokens_in + tokens_out >= self.num_ctx:
            # Ollama stopped at the window: the prompt may have been cut and the answer is incomplete
            return LLMResponse(
                success=False,
                error_reason=R.LLM_ERROR,
                error_detail=(f"context window full ({tokens_in} prompt + {tokens_out} answer tokens "
                              f">= num_ctx {self.num_ctx}); raise num_ctx"),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
                model_name=self.model,
            )
        content = (data.get("message") or {}).get("content", "")

        return LLMResponse(
            success=True,
            content=content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            model_name=self.model,
        )

    def is_available(self) -> bool:
        """Check if Ollama server is reachable and model is available."""
        try:
            body = json.dumps({"model": self.model, "stream": False, "messages": [{"role": "user", "content": "hi"}],
                               "options": {"num_predict": 1}}).encode()
            status, _ = self._transport(f"{self.host}/api/chat", body, self.timeout)
            return status == 200
        except Exception:
            return False
