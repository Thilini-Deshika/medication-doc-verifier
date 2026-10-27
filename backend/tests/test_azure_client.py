import json
from types import SimpleNamespace

import httpx2
import openai
import pytest

from app.config import get_settings
from app.ingest.embeddings import AzureEmbedder
from app.llm.azure_client import AzureOpenAILLM
from app.llm.base import LLMError, strict_json_schema
from app.llm.schemas import CriticalPointsOut, EntitiesOut, SummaryOut, VerificationOut


def chat_payload(content, finish="stop", refusal=None):
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-5.6-terra",
        "choices": [
            {"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content, "refusal": refusal}}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def client_with(handler):
    transport = httpx2.MockTransport(handler)
    return openai.OpenAI(
        base_url="https://example.services.ai.azure.com/openai/v1/",
        api_key="k",
        http_client=httpx2.Client(transport=transport),
        max_retries=0,
    )


def test_request_shape_targets_the_deployment_and_uses_strict_schema():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers.get("authorization")
        return httpx2.Response(200, json=chat_payload(json.dumps({"summary": "ok"})))

    llm = AzureOpenAILLM(client_with(handler))
    out = llm.generate(system="sys", user="doc", output=SummaryOut, purpose="t")
    assert out.summary == "ok"
    assert seen["url"] == "https://example.services.ai.azure.com/openai/v1/chat/completions"
    assert seen["key"] == "Bearer k"
    body = seen["body"]
    assert body["model"] == get_settings().azure_openai_chat_deployment
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "temperature" not in body and "top_p" not in body
    assert body["max_completion_tokens"] == get_settings().llm_max_tokens
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_invalid_json_gets_one_repair_attempt():
    replies = iter([chat_payload("not json"), chat_payload(json.dumps({"summary": "fixed"}))])
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json=next(replies))

    out = AzureOpenAILLM(client_with(handler)).generate(system="s", user="u", output=SummaryOut, purpose="t")
    assert out.summary == "fixed" and len(bodies) == 2
    assert "did not validate" in bodies[1]["messages"][-1]["content"]


def test_two_invalid_replies_raise_a_clear_error():
    def handler(request):
        return httpx2.Response(200, json=chat_payload("{}"))

    with pytest.raises(LLMError, match="invalid output twice"):
        AzureOpenAILLM(client_with(handler)).generate(system="s", user="u", output=SummaryOut, purpose="t")


@pytest.mark.parametrize(
    "payload,message",
    [
        (chat_payload("", finish="length"), "truncated"),
        (chat_payload("", finish="content_filter"), "content filter"),
        (chat_payload(None, refusal="I cannot help"), "declined"),
    ],
)
def test_unusable_completions_are_reported(payload, message):
    def handler(request):
        return httpx2.Response(200, json=payload)

    with pytest.raises(LLMError, match=message):
        AzureOpenAILLM(client_with(handler)).generate(system="s", user="u", output=SummaryOut, purpose="t")


def test_http_errors_become_llm_errors():
    def handler(request):
        return httpx2.Response(429, json={"error": {"message": "rate limited", "code": "429"}})

    with pytest.raises(LLMError, match="429"):
        AzureOpenAILLM(client_with(handler)).generate(system="s", user="u", output=SummaryOut, purpose="t")


def test_embedder_requests_the_configured_dimensions_and_keeps_order():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        n = len(seen["body"]["input"])
        data = [{"object": "embedding", "index": i, "embedding": [float(i)] * 1536} for i in reversed(range(n))]
        return httpx2.Response(200, json={"object": "list", "data": data, "model": "m", "usage": {"prompt_tokens": 1, "total_tokens": 1}})

    emb = AzureEmbedder.__new__(AzureEmbedder)
    emb.model_name, emb.dim, emb._client = "text-embedding-3-small", 1536, client_with(handler)
    vecs = emb.embed_documents(["a", "b", "c"])
    assert seen["url"].endswith("/openai/v1/embeddings")
    assert seen["body"]["model"] == "text-embedding-3-small"
    assert seen["body"]["dimensions"] == 1536
    assert [v[0] for v in vecs] == [0.0, 1.0, 2.0]
    assert len(vecs[0]) == get_settings().embedding_dim


@pytest.mark.parametrize("model", [SummaryOut, CriticalPointsOut, EntitiesOut, VerificationOut])
def test_schemas_satisfy_strict_mode_rules(model):
    def check(node):
        if isinstance(node, dict):
            assert "$ref" not in node and "default" not in node and "title" not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)

    schema = strict_json_schema(model)
    assert schema["type"] == "object"
    check(schema)


def test_missing_configuration_is_reported():
    from app.llm.azure_client import make_azure_client

    s = get_settings()
    saved = (s.azure_openai_endpoint, s.azure_openai_api_key)
    s.azure_openai_endpoint = s.azure_openai_api_key = None
    try:
        with pytest.raises(LLMError, match="AZURE_OPENAI_ENDPOINT"):
            make_azure_client()
    finally:
        s.azure_openai_endpoint, s.azure_openai_api_key = saved
