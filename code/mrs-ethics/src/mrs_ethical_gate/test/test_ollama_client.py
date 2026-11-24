"""OllamaClient over plain HTTP: the request it sends and how it reads answers and errors.
A fake transport stands in for Ollama, so no server is needed."""
import json
import sys

from mrs_common import reasons as R

from mrs_ethical_gate.llm_replanner.ollama_client import OllamaClient


class FakeTransport:
    def __init__(self, *answers):
        self.answers = list(answers)   # (status, dict | bytes) or an exception to raise
        self.calls = []

    def __call__(self, url, body, timeout_s):
        self.calls.append((url, json.loads(body), timeout_s))
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        status, data = a
        return status, data if isinstance(data, bytes) else json.dumps(data).encode()


def ok(content='{"a": 1}', tin=900, tout=120):
    return 200, {"message": {"role": "assistant", "content": content}, "prompt_eval_count": tin,
                 "eval_count": tout, "done": True}


def test_request_carries_num_ctx_schema_and_no_streaming():
    t = FakeTransport(ok())
    c = OllamaClient(model="phi3.5", host="http://gpu-node:11434/v1/", timeout=42.0, temperature=0.3,
                     num_ctx=8192, transport=t)
    r = c.chat([{"role": "user", "content": "fix the plan"}], json_schema={"type": "object"})
    url, payload, timeout = t.calls[0]
    assert url == "http://gpu-node:11434/api/chat" and timeout == 42.0
    assert payload["model"] == "phi3.5" and payload["stream"] is False and payload["format"] == {"type": "object"}
    assert payload["options"] == {"temperature": 0.3, "num_ctx": 8192} and payload["keep_alive"]
    assert r.success and r.content == '{"a": 1}' and (r.tokens_in, r.tokens_out) == (900, 120)
    assert r.model_name == "phi3.5"


def test_no_schema_means_no_format():
    t = FakeTransport(ok())
    OllamaClient(transport=t).chat([{"role": "user", "content": "hi"}])
    assert "format" not in t.calls[0][1]


def test_model_not_found_fails_at_once_without_retries():
    t = FakeTransport((404, {"error": "model 'fake' not found"}))
    r = OllamaClient(model="fake", max_retries=2, transport=t).chat([{"role": "user", "content": "x"}])
    assert not r.success and r.error_reason == R.LLM_ERROR
    assert "model 'fake' not found" in r.error_detail and "404" in r.error_detail
    assert len(t.calls) == 1


def test_server_error_is_retried():
    t = FakeTransport((500, b"boom"), ok())
    r = OllamaClient(max_retries=2, transport=t).chat([{"role": "user", "content": "x"}])
    assert r.success and len(t.calls) == 2


def test_connection_errors_exhaust_retries():
    t = FakeTransport(ConnectionError("refused"), ConnectionError("refused"))
    r = OllamaClient(max_retries=1, transport=t).chat([{"role": "user", "content": "x"}])
    assert not r.success and r.error_reason == R.LLM_ERROR and "after 2 attempts" in r.error_detail


def test_timeout_is_llm_timeout_and_not_retried():
    t = FakeTransport(TimeoutError("timed out"))
    r = OllamaClient(timeout=5.0, max_retries=2, transport=t).chat([{"role": "user", "content": "x"}])
    assert not r.success and r.error_reason == R.LLM_TIMEOUT and len(t.calls) == 1


def test_full_context_window_is_an_error_not_an_answer():
    t = FakeTransport(ok(content='{"assignments": [', tin=7900, tout=292))
    r = OllamaClient(num_ctx=8192, transport=t).chat([{"role": "user", "content": "x"}])
    assert not r.success and r.error_reason == R.LLM_ERROR and "raise num_ctx" in r.error_detail


def test_is_available():
    assert OllamaClient(transport=FakeTransport(ok())).is_available()
    assert not OllamaClient(transport=FakeTransport((404, {"error": "no model"}))).is_available()
    assert not OllamaClient(transport=FakeTransport(ConnectionError("down"))).is_available()


def test_needs_no_ollama_package():
    import mrs_ethical_gate.llm_replanner.ollama_client as mod
    assert "ollama" not in sys.modules
    assert "from ollama" not in open(mod.__file__).read()
