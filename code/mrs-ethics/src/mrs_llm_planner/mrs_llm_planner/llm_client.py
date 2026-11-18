"""One interface to every LLM backend (Module 4, step 1). Pure Python, no ROS, no SDKs.

  client = make_client(LLMConfig(backend="anthropic", model="<model id>"))
  completion = client.complete([Message("system", ...), Message("user", ...)], temperature=0.7)
  completion.text, completion.latency_ms, completion.tokens_in, completion.tokens_out

Backends
  openai             https://api.openai.com/v1/chat/completions
  openai_compatible  the same API at base_url: vLLM or any other OpenAI-compatible server (HPC)
  anthropic          https://api.anthropic.com/v1/messages
  ollama             a local Ollama server's native /api/chat (default http://localhost:11434), e.g.
                     model phi3.5. Native rather than Ollama's OpenAI-compatible endpoint because
                     only the native one takes num_ctx: Ollama's default context (2048 or 4096
                     tokens, by version) is shorter than our prompts, and Ollama truncates an
                     over-long prompt silently, dropping the system prompt first.
  fake               scripted answers, for tests and CI; never touches the network

The HTTP backends use the standard library (urllib), so the planner needs no SDK and tests can
hand in a fake transport. Errors are split the way the reason codes are (mrs_common.reasons):
  LLMTimeout  the call exceeded its wall-clock timeout          -> llm_timeout
  LLMError    an API or network error that retries did not fix  -> llm_error
Rate limits (429), server errors (5xx) and connection errors are retried with exponential
backoff; a timeout is not, because the attempt's time budget is already spent.

API keys come from environment variables (OPENAI_API_KEY, ANTHROPIC_API_KEY by default).
load_secrets() reads KEY=VALUE lines from a git-ignored file (MRS_SECRETS, default .env) into the
environment without overriding what is already set.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence, Union

from mrs_common import reasons as R

BACKENDS = ("openai", "openai_compatible", "anthropic", "ollama", "fake")
OPENAI_URL = "https://api.openai.com/v1"
ANTHROPIC_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
OLLAMA_URL = "http://localhost:11434"
KEYLESS = frozenset({"openai_compatible", "ollama", "fake"})   # an API key is optional
DEFAULT_KEY_ENV = {"openai": "OPENAI_API_KEY", "openai_compatible": "OPENAI_API_KEY",
                   "anthropic": "ANTHROPIC_API_KEY", "ollama": "", "fake": ""}
RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


@dataclass(frozen=True)
class Message:
    role: str      # "system", "user" or "assistant"
    content: str


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    latency_ms: float
    tokens_in: int = 0
    tokens_out: int = 0


class LLMError(Exception):
    """The call failed for good (after retries). reason is the mrs_common.reasons code."""
    reason = R.LLM_ERROR


class LLMTimeout(LLMError):
    reason = R.LLM_TIMEOUT


class LLMClient(Protocol):
    model_name: str

    def complete(self, messages: Sequence[Message], *, temperature: float,
                 json_mode: bool = True, schema: Optional[dict] = None) -> Completion: ...


@dataclass(frozen=True)
class LLMConfig:
    backend: str = "fake"
    model: str = ""
    base_url: str = ""            # empty: the backend's public URL; required for openai_compatible
    api_key_env: str = ""         # empty: the backend's default variable
    timeout_s: float = 60.0       # wall-clock limit of one HTTP request
    max_retries: int = 3          # for 429 / 5xx / connection errors
    backoff_s: float = 1.0        # first wait; doubles each retry
    max_tokens: int = 2000
    num_ctx: int = 8192           # ollama only: context window [tokens], prompt + answer

    def __post_init__(self):
        if self.backend not in BACKENDS:
            raise ValueError(f"backend must be one of {BACKENDS}, got {self.backend!r}")
        if self.backend != "fake" and not self.model:
            raise ValueError(f"backend {self.backend!r} needs a model name")
        if self.backend == "openai_compatible" and not self.base_url:
            raise ValueError("backend openai_compatible needs base_url (e.g. http://gpu-node:8001/v1)")
        if self.timeout_s <= 0 or self.max_retries < 0 or self.backoff_s < 0 or self.max_tokens < 1:
            raise ValueError("timeout_s and max_tokens must be > 0; max_retries and backoff_s >= 0")
        if self.num_ctx <= self.max_tokens:
            raise ValueError(f"num_ctx ({self.num_ctx}) must be larger than max_tokens ({self.max_tokens})")

    @property
    def model_name(self) -> str:
        """What ProposedPlan.model_name and the response log record, e.g. 'anthropic/<model>'."""
        return "fake" if self.backend == "fake" else f"{self.backend}/{self.model}"


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
# (url, headers, body, timeout_s) -> (status, body). Raises TimeoutError on a timeout and
# OSError on a connection error; any status, 200 or not, is returned.
Transport = Callable[[str, dict, bytes, float], "tuple[int, bytes]"]


def urllib_transport(url: str, headers: dict, body: bytes, timeout_s: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise TimeoutError(str(e)) from e
        raise OSError(str(e.reason)) from e
    except (socket.timeout, TimeoutError) as e:
        raise TimeoutError(str(e)) from e


class _HTTPClient:
    def __init__(self, config: LLMConfig, *, transport: Transport = urllib_transport,
                 sleep: Callable[[float], None] = time.sleep, api_key: Optional[str] = None):
        self.config = config
        self.model_name = config.model_name
        self._transport = transport
        self._sleep = sleep
        env = config.api_key_env or DEFAULT_KEY_ENV[config.backend]
        self._api_key = api_key if api_key is not None else (os.environ.get(env, "") if env else "")
        if not self._api_key and config.backend not in KEYLESS:
            raise LLMError(f"no API key: set {env} (or put it in the file named by MRS_SECRETS)")

    def complete(self, messages: Sequence[Message], *, temperature: float, json_mode: bool = True,
                 schema: Optional[dict] = None) -> Completion:
        """schema: a JSON schema the answer must follow. Backends that can enforce it while
        generating (ollama, openai, openai_compatible) do; the others fall back to json_mode."""
        url, headers, payload = self._request(messages, temperature, json_mode, schema)
        body = json.dumps(payload).encode()
        start = time.perf_counter()
        wait = self.config.backoff_s
        for attempt in range(self.config.max_retries + 1):
            try:
                status, raw = self._transport(url, headers, body, self.config.timeout_s)
            except TimeoutError as e:
                raise LLMTimeout(f"no answer within {self.config.timeout_s:g} s") from e
            except OSError as e:
                status, raw = None, str(e).encode()
            if status == 200:
                text, tin, tout = self._parse(raw)
                return Completion(text=text, model=self.model_name,
                                  latency_ms=(time.perf_counter() - start) * 1e3, tokens_in=tin, tokens_out=tout)
            retryable = status is None or status in RETRY_STATUS
            if not retryable or attempt == self.config.max_retries:
                raise LLMError(f"HTTP {status if status is not None else 'connection error'}: "
                               f"{raw[:300].decode(errors='replace')}")
            self._sleep(wait)
            wait *= 2
        raise AssertionError("unreachable")

    def _request(self, messages, temperature, json_mode, schema=None) -> tuple[str, dict, dict]:
        raise NotImplementedError

    def _parse(self, raw: bytes) -> tuple[str, int, int]:
        raise NotImplementedError


class OpenAIChatClient(_HTTPClient):
    """OpenAI chat completions, or any server with the same API (vLLM) at base_url."""

    def _request(self, messages, temperature, json_mode, schema=None):
        base = (self.config.base_url or OPENAI_URL).rstrip("/")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {"model": self.config.model, "temperature": temperature, "max_tokens": self.config.max_tokens,
                   "messages": [{"role": m.role, "content": m.content} for m in messages]}
        if schema is not None:   # structured outputs; vLLM's guided decoding takes the same field
            payload["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": "reply", "schema": schema, "strict": False}}
        elif json_mode:
            payload["response_format"] = {"type": "json_object"}
        return f"{base}/chat/completions", headers, payload

    def _parse(self, raw):
        try:
            data = json.loads(raw)
            text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage") or {}
            return text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected response: {raw[:300]!r}") from e


class AnthropicClient(_HTTPClient):
    # schema: not enforced here (Anthropic constrains output through tool use, not wired up);
    # the prompt and the agent's validation still apply.
    """Anthropic messages API. System messages go into the separate `system` field."""

    def _request(self, messages, temperature, json_mode, schema=None):
        base = (self.config.base_url or ANTHROPIC_URL).rstrip("/")
        headers = {"Content-Type": "application/json", "x-api-key": self._api_key,
                   "anthropic-version": ANTHROPIC_VERSION}
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        payload = {"model": self.config.model, "temperature": temperature, "max_tokens": self.config.max_tokens,
                   "messages": [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]}
        if system:
            payload["system"] = system
        return f"{base}/messages", headers, payload

    def _parse(self, raw):
        try:
            data = json.loads(raw)
            text = "".join(block.get("text", "") for block in data["content"] if block.get("type") == "text")
            usage = data.get("usage") or {}
            return text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        except (ValueError, KeyError, TypeError) as e:
            raise LLMError(f"unexpected response: {raw[:300]!r}") from e


class OllamaClient(_HTTPClient):
    """A local Ollama server, native chat API: POST {base_url}/api/chat, no streaming.

    options.num_ctx sets the context window per request. An answer that filled the window means
    Ollama cut the prompt (it does so silently): that is an LLMError, not a reply to parse."""

    KEEP_ALIVE = "30m"   # keep the model loaded between turns; loading it takes seconds

    def _request(self, messages, temperature, json_mode, schema=None):
        base = (self.config.base_url or OLLAMA_URL).rstrip("/")
        if base.endswith("/v1"):   # the OpenAI-compatible URL given by mistake
            base = base[:-3]
        payload = {"model": self.config.model, "stream": False, "keep_alive": self.KEEP_ALIVE,
                   "messages": [{"role": m.role, "content": m.content} for m in messages],
                   "options": {"temperature": temperature, "num_ctx": self.config.num_ctx,
                               "num_predict": self.config.max_tokens}}
        if schema is not None:   # Ollama structured outputs: generation follows the schema
            payload["format"] = schema
        elif json_mode:
            payload["format"] = "json"
        return f"{base}/api/chat", {"Content-Type": "application/json"}, payload

    def _parse(self, raw):
        try:
            data = json.loads(raw)
            text = data["message"]["content"] or ""
            tin, tout = int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0))
        except (ValueError, KeyError, TypeError) as e:
            raise LLMError(f"unexpected response: {raw[:300]!r}") from e
        if tin + tout >= self.config.num_ctx:
            raise LLMError(f"context window full ({tin} prompt + {tout} answer tokens >= num_ctx "
                           f"{self.config.num_ctx}): Ollama cut the prompt; raise num_ctx")
        return text, tin, tout


# --------------------------------------------------------------------------
# Fake backend
# --------------------------------------------------------------------------
# A script entry is the answer text, an exception to raise (LLMTimeout("..."), LLMError("...")),
# or a function of the messages that returns either.
ScriptEntry = Union[str, Exception, Callable[[Sequence[Message]], Union[str, Exception]]]


@dataclass
class FakeClient:
    """Scripted answers, in order; the last entry repeats. `calls` records every request."""
    script: list = field(default_factory=lambda: ['{"kind": "discussion", "text": "fake agent"}'])
    model_name: str = "fake"
    calls: list = field(default_factory=list)

    schemas: list = field(default_factory=list)     # the schema of every request (None if none)
    temperatures: list = field(default_factory=list)

    def complete(self, messages: Sequence[Message], *, temperature: float, json_mode: bool = True,
                 schema: Optional[dict] = None) -> Completion:
        self.calls.append(list(messages))
        self.schemas.append(schema)
        self.temperatures.append(temperature)
        entry = self.script[min(len(self.calls), len(self.script)) - 1]
        if callable(entry) and not isinstance(entry, Exception):
            entry = entry(messages)
        if isinstance(entry, Exception):
            raise entry
        return Completion(text=entry, model=self.model_name, latency_ms=0.0,
                          tokens_in=sum(len(m.content) for m in messages) // 4, tokens_out=len(entry) // 4)


def make_client(config: LLMConfig, *, transport: Transport = urllib_transport,
                sleep: Callable[[float], None] = time.sleep, api_key: Optional[str] = None,
                fake_script: Optional[list] = None) -> LLMClient:
    if config.backend == "fake":
        return FakeClient(script=fake_script) if fake_script else FakeClient()
    cls = {"anthropic": AnthropicClient, "ollama": OllamaClient}.get(config.backend, OpenAIChatClient)
    return cls(config, transport=transport, sleep=sleep, api_key=api_key)


def load_secrets(path: Optional[Union[str, Path]] = None) -> list[str]:
    """Read KEY=VALUE lines into os.environ (existing variables win). Returns the keys set.
    path defaults to $MRS_SECRETS, else .env; a missing file sets nothing."""
    p = Path(path or os.environ.get("MRS_SECRETS", ".env")).expanduser()
    if not p.is_file():
        return []
    set_keys = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            set_keys.append(key)
    return set_keys
