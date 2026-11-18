"""LLM clients against a fake HTTP transport: no network, no SDK."""
import json

import pytest

from mrs_common import reasons as R
from mrs_llm_planner.llm_client import (FakeClient, LLMConfig, LLMError, LLMTimeout, Message, load_secrets,
                                        make_client)

MSGS = [Message("system", "be brief"), Message("user", "hi")]


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, url, headers, body, timeout_s):
        self.requests.append((url, headers, json.loads(body), timeout_s))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        status, payload = r
        return status, json.dumps(payload).encode()


OPENAI_OK = (200, {"choices": [{"message": {"content": '{"kind": "discussion"}'}}],
                   "usage": {"prompt_tokens": 11, "completion_tokens": 5}})
ANTHROPIC_OK = (200, {"content": [{"type": "text", "text": '{"kind": "agreement"}'}],
                      "usage": {"input_tokens": 7, "output_tokens": 3}})


def test_config_checks():
    with pytest.raises(ValueError):
        LLMConfig(backend="nope")
    with pytest.raises(ValueError):
        LLMConfig(backend="openai")                      # no model
    with pytest.raises(ValueError):
        LLMConfig(backend="openai_compatible", model="m")   # no base_url
    assert LLMConfig().model_name == "fake"
    assert LLMConfig(backend="anthropic", model="m").model_name == "anthropic/m"


def test_openai_request_and_parse():
    t = Transport(OPENAI_OK)
    c = make_client(LLMConfig(backend="openai", model="gpt-x"), transport=t, api_key="k")
    out = c.complete(MSGS, temperature=0.7)
    assert out.text == '{"kind": "discussion"}' and (out.tokens_in, out.tokens_out) == (11, 5)
    url, headers, body, _ = t.requests[0]
    assert url.endswith("/chat/completions") and headers["Authorization"] == "Bearer k"
    assert body["temperature"] == 0.7 and body["response_format"] == {"type": "json_object"}
    assert body["messages"][0] == {"role": "system", "content": "be brief"}


def test_anthropic_puts_system_apart():
    t = Transport(ANTHROPIC_OK)
    c = make_client(LLMConfig(backend="anthropic", model="m"), transport=t, api_key="k")
    out = c.complete(MSGS, temperature=0.2)
    assert out.text == '{"kind": "agreement"}' and (out.tokens_in, out.tokens_out) == (7, 3)
    url, headers, body, _ = t.requests[0]
    assert url.endswith("/messages") and headers["x-api-key"] == "k"
    assert body["system"] == "be brief" and body["messages"] == [{"role": "user", "content": "hi"}]


def test_retries_then_succeeds():
    sleeps = []
    t = Transport((429, {}), OSError("reset"), OPENAI_OK)
    c = make_client(LLMConfig(backend="openai", model="m", backoff_s=1), transport=t, sleep=sleeps.append,
                    api_key="k")
    assert c.complete(MSGS, temperature=0).tokens_in == 11
    assert sleeps == [1, 2]


def test_gives_up_with_llm_error():
    t = Transport(*[(503, {})] * 3)
    c = make_client(LLMConfig(backend="openai", model="m", max_retries=2), transport=t, sleep=lambda s: None,
                    api_key="k")
    with pytest.raises(LLMError) as e:
        c.complete(MSGS, temperature=0)
    assert e.value.reason == R.LLM_ERROR and len(t.requests) == 3


def test_client_error_is_not_retried():
    t = Transport((400, {"error": "bad"}))
    c = make_client(LLMConfig(backend="openai", model="m"), transport=t, sleep=lambda s: None, api_key="k")
    with pytest.raises(LLMError):
        c.complete(MSGS, temperature=0)
    assert len(t.requests) == 1


def test_timeout_is_llm_timeout():
    t = Transport(TimeoutError())
    c = make_client(LLMConfig(backend="openai", model="m"), transport=t, api_key="k")
    with pytest.raises(LLMTimeout) as e:
        c.complete(MSGS, temperature=0)
    assert e.value.reason == R.LLM_TIMEOUT


def test_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMError):
        make_client(LLMConfig(backend="openai", model="m"), transport=Transport()).complete(MSGS, temperature=0)


def test_fake_client_script():
    c = FakeClient(script=["one", TimeoutError("x") and LLMTimeout("slow"), lambda m: m[-1].content])
    assert c.complete(MSGS, temperature=0).text == "one"
    with pytest.raises(LLMTimeout):
        c.complete(MSGS, temperature=0)
    assert c.complete(MSGS, temperature=0).text == "hi"
    assert c.complete(MSGS, temperature=0).text == "hi"   # the last entry repeats
    assert len(c.calls) == 4


def test_load_secrets(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# comment\nexport A_KEY='abc'\nB_KEY=def\n")
    monkeypatch.delenv("A_KEY", raising=False)
    monkeypatch.setenv("B_KEY", "kept")
    assert load_secrets(f) == ["A_KEY"]
    import os
    assert os.environ["A_KEY"] == "abc" and os.environ["B_KEY"] == "kept"
    monkeypatch.delenv("A_KEY")


OLLAMA_OK = (200, {"model": "phi3.5", "message": {"role": "assistant", "content": '{"kind": "discussion"}'},
                   "done": True, "prompt_eval_count": 1500, "eval_count": 40})


def test_ollama_native_chat_needs_no_key():
    t = Transport(OLLAMA_OK)
    c = make_client(LLMConfig(backend="ollama", model="phi3.5", num_ctx=8192, max_tokens=1000), transport=t)
    out = c.complete(MSGS, temperature=0.7)
    assert out.text == '{"kind": "discussion"}' and (out.tokens_in, out.tokens_out) == (1500, 40)
    assert c.model_name == "ollama/phi3.5"
    url, headers, body, _ = t.requests[0]
    assert url == "http://localhost:11434/api/chat" and "Authorization" not in headers
    assert body["stream"] is False and body["format"] == "json"
    assert body["options"] == {"temperature": 0.7, "num_ctx": 8192, "num_predict": 1000}
    assert body["messages"][0] == {"role": "system", "content": "be brief"}


def test_ollama_openai_style_url_is_corrected():
    t = Transport(OLLAMA_OK)
    make_client(LLMConfig(backend="ollama", model="phi3.5", base_url="http://gpu:11434/v1/"),
                transport=t).complete(MSGS, temperature=0)
    assert t.requests[0][0] == "http://gpu:11434/api/chat"


def test_ollama_full_context_is_an_error():
    full = (200, {"message": {"content": "{}"}, "prompt_eval_count": 4090, "eval_count": 6})
    c = make_client(LLMConfig(backend="ollama", model="phi3.5", num_ctx=4096, max_tokens=1000),
                    transport=Transport(full))
    with pytest.raises(LLMError, match="context window full"):
        c.complete(MSGS, temperature=0)


def test_ollama_not_running_is_llm_error():
    t = Transport(*[OSError("Connection refused")] * 4)
    c = make_client(LLMConfig(backend="ollama", model="phi3.5"), transport=t, sleep=lambda s: None)
    with pytest.raises(LLMError, match="connection error"):
        c.complete(MSGS, temperature=0)


def test_num_ctx_must_exceed_max_tokens():
    with pytest.raises(ValueError):
        LLMConfig(backend="ollama", model="phi3.5", num_ctx=2000, max_tokens=2000)
