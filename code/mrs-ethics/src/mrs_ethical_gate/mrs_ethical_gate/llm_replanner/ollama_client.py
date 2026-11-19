"""Ollama API client for LLM-based replanning.

Wraps the Ollama Python library with:
- Configurable model selection (default: phi3.5)
- Timeout handling -> LLM_TIMEOUT
- Retry logic for transient errors
- Structured output using Pydantic JSON schema
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from mrs_common import reasons as R

# Default configuration
DEFAULT_MODEL = "phi3.5"
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT = 60.0  # seconds
DEFAULT_MAX_RETRIES = 2


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
    ):
        self.model = model
        self.host = host
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature
        self._client = None

    def _get_client(self):
        """Lazy initialization of Ollama client."""
        if self._client is None:
            try:
                from ollama import Client
                self._client = Client(host=self.host, timeout=self.timeout)
            except ImportError:
                raise ImportError(
                    "ollama package not installed. Run: pip install ollama"
                )
        return self._client

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
        """Execute a single chat request."""
        client = self._get_client()

        # Build request kwargs
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "options": {"temperature": self.temperature},
        }

        # Add structured output format if schema provided
        if json_schema is not None:
            kwargs["format"] = json_schema

        try:
            response = client.chat(**kwargs)
        except Exception as e:
            error_str = str(e).lower()
            if "timeout" in error_str or "timed out" in error_str:
                raise TimeoutError(str(e))
            raise

        latency_ms = (time.perf_counter() - start_time) * 1000

        # Extract token counts if available
        tokens_in = 0
        tokens_out = 0
        if hasattr(response, "prompt_eval_count"):
            tokens_in = response.prompt_eval_count or 0
        if hasattr(response, "eval_count"):
            tokens_out = response.eval_count or 0

        # Extract content
        content = ""
        if hasattr(response, "message") and hasattr(response.message, "content"):
            content = response.message.content
        elif isinstance(response, dict):
            content = response.get("message", {}).get("content", "")

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
            client = self._get_client()
            # Try a minimal request
            client.chat(
                model=self.model,
                messages=[{"role": "user", "content": "hi"}],
                options={"num_predict": 1},
            )
            return True
        except Exception:
            return False
