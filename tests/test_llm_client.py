"""llm_client error handling, with a fake OpenAI client (no network)."""

import httpx
import openai
import pytest

from app import config, llm_client

REQUEST = httpx.Request("POST", "https://llm.example/v1/chat/completions")


def _settings(**overrides):
    base = dict(
        base_url="https://llm.example/v1", api_key="sk-test-SECRET", model_name="m",
        max_file_mb=10, max_pdf_pages=5, totals_tolerance=0.02, llm_timeout=60,
        batch_concurrency=2,
    )
    base.update(overrides)
    return config.Settings(**base)


def _ok_response(text="{}"):
    message = type("Msg", (), {"content": text})()
    choice = type("Choice", (), {"message": message})()
    return type("Resp", (), {"choices": [choice]})()


class FakeCompletions:
    """Raises/returns the queued outcomes in order (last one repeats)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, *outcomes):
        self.completions = FakeCompletions(*outcomes)
        self.chat = type("Chat", (), {"completions": self.completions})()


@pytest.fixture
def sleeps(monkeypatch):
    waited: list[float] = []
    monkeypatch.setattr(llm_client.time, "sleep", waited.append)
    return waited


@pytest.fixture
def use_error(monkeypatch, sleeps):
    def _install(*outcomes, **settings):
        client = FakeClient(*outcomes)
        monkeypatch.setattr(llm_client, "get_settings", lambda: _settings(**settings))
        monkeypatch.setattr(llm_client, "_get_client", lambda: client)
        monkeypatch.setattr(llm_client, "_json_mode_supported", False)
        return client
    return _install


def _status_error(cls, code, message="boom", headers=None):
    response = httpx.Response(code, request=REQUEST, headers=headers or {})
    return cls(message, response=response, body=None)


def test_not_configured_raises(monkeypatch):
    monkeypatch.setattr(llm_client, "get_settings", lambda: _settings(api_key=""))
    with pytest.raises(llm_client.LLMNotConfiguredError):
        llm_client.call_llm([])


@pytest.mark.parametrize(
    "error, expected",
    [
        (openai.APITimeoutError(request=REQUEST), "took too long"),
        (openai.APIConnectionError(request=REQUEST), "Could not connect"),
        (_status_error(openai.AuthenticationError, 401), "rejected the API key"),
        (_status_error(openai.NotFoundError, 404), "Model or endpoint not found"),
        (_status_error(openai.RateLimitError, 429), "rate limit"),
        (_status_error(openai.InternalServerError, 503), "temporarily unavailable"),
    ],
)
def test_errors_get_friendly_messages(use_error, error, expected):
    use_error(error)
    with pytest.raises(llm_client.LLMError, match=expected) as info:
        llm_client.call_llm([{"role": "user", "content": "hi"}])
    assert "SECRET" not in str(info.value)


GEMINI_429 = ("You exceeded your current quota... limit: 5 ... "
              "Please retry in 12.5s.")


def test_rate_limit_waits_and_retries_then_succeeds(use_error, sleeps):
    client = use_error(
        _status_error(openai.RateLimitError, 429, GEMINI_429), _ok_response('{"a": 1}')
    )
    assert llm_client.call_llm([{"role": "user", "content": "hi"}]) == '{"a": 1}'
    assert client.completions.calls == 2
    assert sleeps == [13.5]  # provider's 12.5s + 1s margin


def test_rate_limit_uses_retry_after_header(use_error, sleeps):
    err = _status_error(openai.RateLimitError, 429, headers={"retry-after": "7"})
    use_error(err, _ok_response())
    llm_client.call_llm([{"role": "user", "content": "hi"}])
    assert sleeps == [8.0]


def test_rate_limit_gives_up_after_max_retries(use_error, sleeps):
    client = use_error(_status_error(openai.RateLimitError, 429, GEMINI_429),
                       rate_limit_retries=2)
    with pytest.raises(llm_client.LLMError, match="rate limit"):
        llm_client.call_llm([{"role": "user", "content": "hi"}])
    assert client.completions.calls == 3  # first try + 2 retries
    assert len(sleeps) == 2


def test_daily_quota_fails_fast_with_clear_message(use_error, sleeps):
    # Real Gemini daily-quota reply: "retry in 4h51m17.9s" + retryDelay '17477s'
    body = [{"error": {"code": 429, "message": "Please retry in 4h51m17.9s.",
                       "details": [{"retryDelay": "17477s"}]}}]
    err = openai.RateLimitError(
        "Error code: 429", response=httpx.Response(429, request=REQUEST), body=body
    )
    client = use_error(err, rate_limit_max_wait=65)
    with pytest.raises(llm_client.LLMError, match=r"quota .* used up .* 4h 51m"):
        llm_client.call_llm([{"role": "user", "content": "hi"}])
    assert client.completions.calls == 1  # no pointless waiting
    assert sleeps == []


def test_api_key_never_logged(use_error, caplog):
    use_error(_status_error(openai.AuthenticationError, 401))
    with pytest.raises(llm_client.LLMError):
        llm_client.call_llm([{"role": "user", "content": "hi"}])
    assert "SECRET" not in caplog.text
