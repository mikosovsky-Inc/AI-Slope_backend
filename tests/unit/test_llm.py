import ast
import json
from pathlib import Path

import httpx
import pytest
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from app.integrations.llm.factory import create_llm_provider
from app.integrations.llm.mock import MockLLMProvider
from app.integrations.llm.openai import OpenAIProvider
from app.shared.llm import (
    LLMIncomplete,
    LLMInvalidOutput,
    LLMRefusal,
    LLMRequest,
    LLMUnavailable,
)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)


REQUEST = LLMRequest(instructions="Return a title", prompt="History")


def payload(content=None, status="completed"):
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 123,
        "model": "test-model",
        "status": status,
        "error": None,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": [
            {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": content
                if content is not None
                else [{"type": "output_text", "text": '{"title":"History"}', "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def provider(handler, retries=0):
    client = OpenAI(
        api_key="secret-test-key",
        max_retries=retries,
        timeout=1,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return OpenAIProvider(client, "test-model")


def test_structured_output_and_usage():
    def handle(request):
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["model"] == "test-model"
        assert body["max_output_tokens"] == 2000
        assert body["text"]["format"]["strict"] is True
        assert body["text"]["format"]["schema"]["additionalProperties"] is False
        return httpx.Response(200, json=payload())

    llm = provider(handle)
    try:
        result = llm.generate(REQUEST, Answer)
        assert result.data == Answer(title="History")
        assert result.usage.total_tokens == 15
        assert result.response_id == "resp_test"
        assert result.provider == "openai"
    finally:
        llm.close()
    assert llm._client.is_closed()


@pytest.mark.parametrize(
    ("content", "status", "error"),
    [
        ([{"type": "refusal", "refusal": "private text"}], "completed", LLMRefusal),
        ([], "incomplete", LLMIncomplete),
        ([], "completed", LLMInvalidOutput),
        ([], "failed", LLMUnavailable),
        (
            [{"type": "output_text", "text": '{"title":""}', "annotations": []}],
            "completed",
            LLMInvalidOutput,
        ),
        (
            [{"type": "output_text", "text": "not json private text", "annotations": []}],
            "completed",
            LLMInvalidOutput,
        ),
    ],
)
def test_bad_responses(content, status, error):
    llm = provider(lambda _: httpx.Response(200, json=payload(content, status)))
    try:
        with pytest.raises(error) as exc:
            llm.generate(REQUEST, Answer)
        assert "private text" not in str(exc.value)
    finally:
        llm.close()


@pytest.mark.parametrize(("status", "attempts"), [(401, 1), (400, 1), (429, 3), (500, 3)])
def test_retry_limits_and_safe_errors(status, attempts, monkeypatch):
    monkeypatch.setattr("openai._base_client.time.sleep", lambda _: None)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "secret-test-key private"}})

    llm = provider(handle, retries=2)
    try:
        with pytest.raises(LLMUnavailable) as exc:
            llm.generate(REQUEST, Answer)
        assert len(calls) == attempts
        assert str(exc.value) == "LLM provider request failed"
    finally:
        llm.close()


def test_timeout_and_retry_recovery(monkeypatch):
    monkeypatch.setattr("openai._base_client.time.sleep", lambda _: None)
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("private timeout", request=request)
        return httpx.Response(200, json=payload())

    llm = provider(handle, retries=1)
    try:
        assert llm.generate(REQUEST, Answer).data.title == "History"
        assert len(calls) == 2
    finally:
        llm.close()


def test_mock_validates_and_returns_independent_results():
    llm = MockLLMProvider({Answer: {"title": "Offline"}})
    first = llm.generate(REQUEST, Answer)
    first.data.title = "changed"
    assert llm.generate(REQUEST, Answer).data.title == "Offline"
    assert first.usage.total_tokens == 0
    with pytest.raises(LLMInvalidOutput):
        MockLLMProvider().generate(REQUEST, Answer)
    with pytest.raises(LLMInvalidOutput):
        MockLLMProvider({Answer: {"title": ""}}).generate(REQUEST, Answer)


def test_factory_configuration(settings):
    assert isinstance(create_llm_provider(settings), MockLLMProvider)
    live = settings.model_copy(update={"external_providers_mode": "live", "openai_api_key": None})
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_llm_provider(live)
    from pydantic import SecretStr

    live.openai_api_key = SecretStr("secret-test-key")
    live.openai_model = ""
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        create_llm_provider(live)
    live.openai_model = "test-model"
    llm = create_llm_provider(live)
    try:
        assert isinstance(llm, OpenAIProvider)
        assert llm._client.max_retries == 2
        assert llm._client.timeout == 30
        assert "secret-test-key" not in repr(live)
    finally:
        llm.close()


def test_sdk_import_boundary():
    for path in Path("app").rglob("*.py"):
        if path == Path("app/integrations/llm/openai.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert not any(name.split(".")[0] == "openai" for name in names), path


def test_app_provider_lifecycle(client):
    assert isinstance(client.app.state.llm, MockLLMProvider)
