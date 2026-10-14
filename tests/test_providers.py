import json

import httpx
import pytest

from gamescoach.providers import GeminiProvider, OpenAIProvider, ProviderError


KEY = "test-key-never-display"


def openai_response(text="Warm up, then train."):
    return {
        "status": "completed",
        "output": [{"type": "message", "status": "completed", "content": [{"type": "output_text", "text": text}]}],
    }


def gemini_response(text="Scale the workout.", reason="STOP"):
    return {"candidates": [{"finishReason": reason, "content": {"parts": [{"text": text}]}}]}


def client_for(body, status=200):
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)))


def test_openai_official_endpoint_auth_body_and_multiple_text_parts():
    calls = []

    def respond(request):
        calls.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.extensions["timeout"]["read"] == 90.0
        assert json.loads(request.content) == {
            "model": "gpt-5-mini", "instructions": "Coach safely.", "input": "Make a plan.",
            "max_output_tokens": 6000, "store": False,
        }
        data = openai_response("  Warm up.  ")
        data["output"].insert(0, {"type": "reasoning", "summary": [{"text": "Hidden reasoning"}]})
        data["output"][1]["content"].append({"type": "output_text", "text": " Train."})
        return httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider = OpenAIProvider(KEY, client=client)
        assert provider.name == "ChatGPT"
        assert provider.generate(instructions="Coach safely.", prompt="Make a plan.") == "Warm up.\n\nTrain."
        assert not client.is_closed
    assert len(calls) == 1


def test_gemini_official_endpoint_header_body_and_ignores_thought_parts():
    def respond(request):
        assert request.method == "POST"
        assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
        assert not request.url.query
        assert request.headers["x-goog-api-key"] == KEY
        assert json.loads(request.content) == {
            "systemInstruction": {"parts": [{"text": "Coach safely."}]},
            "contents": [{"role": "user", "parts": [{"text": "Make a plan."}]}],
            "generationConfig": {"maxOutputTokens": 6000, "candidateCount": 1},
        }
        data = gemini_response("  Scale the workout.  ")
        data["candidates"][0]["content"]["parts"].insert(0, {"thought": True, "text": "Hidden reasoning"})
        return httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider = GeminiProvider(KEY, client=client)
        assert provider.name == "Gemini"
        assert provider.generate(instructions="Coach safely.", prompt="Make a plan.") == "Scale the workout."


@pytest.mark.parametrize("provider_class", [OpenAIProvider, GeminiProvider])
@pytest.mark.parametrize("model", ["../models/evil", "model?key=leak", "model:generateContent", "model/other", "", "has space"])
def test_model_path_injection_is_rejected(provider_class, model):
    with pytest.raises(ProviderError, match="model name"):
        provider_class(KEY, model)


@pytest.mark.parametrize("provider_class", [OpenAIProvider, GeminiProvider])
@pytest.mark.parametrize("key", ["", "   ", "key\r\nleak", "key with space"])
def test_missing_and_invalid_credentials_are_rejected(provider_class, key):
    with pytest.raises(ProviderError):
        provider_class(key)


@pytest.mark.parametrize("provider_class", [OpenAIProvider, GeminiProvider])
@pytest.mark.parametrize("status,match", [(401, "denied access"), (403, "denied access"), (429, "quota"), (404, "model"), (500, "unavailable"), (400, "HTTP 400"), (302, "HTTP 302")])
def test_http_failures_are_actionable_redacted_and_never_retried(provider_class, status, match):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": f"Sensitive response: {KEY}"}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderError, match=match) as caught:
            provider_class(KEY, client=client).generate(instructions="Coach.", prompt="Plan.")
    assert KEY not in str(caught.value)
    assert "Sensitive response" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("provider_class", [OpenAIProvider, GeminiProvider])
@pytest.mark.parametrize("error_class,match", [(httpx.ReadTimeout, "timed out"), (httpx.ConnectError, "network access")])
def test_network_errors_hide_request_details(provider_class, error_class, match):
    def respond(request):
        raise error_class(f"Sensitive {KEY}", request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderError, match=match) as caught:
            provider_class(KEY, client=client).generate(instructions="Coach.", prompt="Plan.")
    assert KEY not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("provider_class", [OpenAIProvider, GeminiProvider])
@pytest.mark.parametrize("body", [None, [], "Sensitive response"])
def test_non_object_response_is_rejected(provider_class, body):
    with client_for(body) as client:
        with pytest.raises(ProviderError, match="invalid response"):
            provider_class(KEY, client=client).generate(instructions="Coach.", prompt="Plan.")


@pytest.mark.parametrize("provider_class", [OpenAIProvider, GeminiProvider])
def test_invalid_json_is_rejected(provider_class):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="not json"))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ProviderError, match="invalid response"):
            provider_class(KEY, client=client).generate(instructions="Coach.", prompt="Plan.")


@pytest.mark.parametrize("body,match", [
    ({"status": "incomplete", "output": openai_response()["output"]}, "incomplete"),
    ({"status": "failed", "error": {"message": KEY}}, "did not complete"),
    ({"status": "completed", "output": []}, "no workout text"),
    (openai_response("  "), "no workout text"),
    ({"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": KEY}]}]}, "declined"),
    ({"status": "completed", "output": [{"type": "message", "status": "incomplete", "content": [{"type": "output_text", "text": "Partial"}]}]}, "incomplete"),
])
def test_openai_rejects_incomplete_empty_and_refused_output(body, match):
    with client_for(body) as client:
        with pytest.raises(ProviderError, match=match) as caught:
            OpenAIProvider(KEY, client=client).generate(instructions="Coach.", prompt="Plan.")
    assert KEY not in str(caught.value)


@pytest.mark.parametrize("body,match", [
    (gemini_response(reason="MAX_TOKENS"), "truncated"),
    (gemini_response(reason="SAFETY"), "blocked"),
    (gemini_response(reason="RECITATION"), "blocked"),
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "blocked"),
    ({"candidates": []}, "no workout text"),
    (gemini_response("  "), "no workout text"),
    (gemini_response(reason="OTHER"), "did not complete"),
    ({"candidates": [{"content": {"parts": [{"text": "Partial"}]}}]}, "did not complete"),
])
def test_gemini_rejects_truncated_blocked_empty_and_unfinished_output(body, match):
    with client_for(body) as client:
        with pytest.raises(ProviderError, match=match):
            GeminiProvider(KEY, client=client).generate(instructions="Coach.", prompt="Plan.")


def test_gemini_checks_candidate_safety_flags_even_with_stop_reason():
    data = gemini_response()
    data["candidates"][0]["safetyRatings"] = [{"blocked": True}]
    with client_for(data) as client:
        with pytest.raises(ProviderError, match="blocked"):
            GeminiProvider(KEY, client=client).generate(instructions="Coach.", prompt="Plan.")


@pytest.mark.parametrize("provider_class,model,response", [
    (OpenAIProvider, "gpt-4.1-mini", openai_response()),
    (GeminiProvider, "gemini-3.1-flash-preview", gemini_response()),
])
def test_model_can_be_configured(provider_class, model, response):
    with client_for(response) as client:
        provider = provider_class(KEY, model=model, client=client)
        assert provider.model == model
        assert provider.generate(instructions="Coach.", prompt="Plan.")
