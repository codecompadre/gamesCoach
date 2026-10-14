"""Small, synchronous clients for the two coaches' official APIs."""

from __future__ import annotations

from contextlib import nullcontext
import re
from typing import Any

import httpx


class ProviderError(Exception):
    """An actionable provider failure, safe to display without credentials."""


_TIMEOUT = 90.0
_MAX_OUTPUT_TOKENS = 6000
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _api_key(value: str, variable: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderError(f"Set {variable} before contacting this coach.")
    value = value.strip()
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise ProviderError(f"Check {variable}; the key contains invalid characters.")
    return value


def _model(value: str) -> str:
    if not isinstance(value, str) or not _MODEL_NAME.fullmatch(value):
        raise ProviderError("Use a model name containing only letters, numbers, dots, underscores, or hyphens.")
    return value


def _request(
    *,
    client: httpx.Client | None,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    name: str,
    key_variable: str,
) -> dict[str, Any]:
    # httpx preserves TLS verification and performs no automatic request retries.
    context = nullcontext(client) if client is not None else httpx.Client(timeout=_TIMEOUT)
    try:
        with context as active_client:
            response = active_client.post(
                url, headers=headers, json=payload, timeout=_TIMEOUT, follow_redirects=False
            )
    except httpx.TimeoutException:
        raise ProviderError(f"{name} timed out. Try a smaller request or try again later.") from None
    except httpx.HTTPError:
        raise ProviderError(f"Could not reach {name}. Check network access and try again.") from None

    if response.status_code in (401, 403):
        raise ProviderError(f"{name} denied access. Check {key_variable} and model access.")
    if response.status_code == 429:
        raise ProviderError(f"{name} reached a rate or quota limit. Check billing and quota, then try again later.")
    if response.status_code == 404:
        raise ProviderError(f"{name} could not find the model. Check the configured model name and access.")
    if response.status_code >= 500:
        raise ProviderError(f"{name} is unavailable. Try again later.")
    if response.status_code < 200 or response.status_code >= 300:
        raise ProviderError(f"{name} rejected the request (HTTP {response.status_code}). Check the model configuration.")
    try:
        data = response.json()
    except ValueError:
        raise ProviderError(f"{name} returned an invalid response. Try again later.") from None
    if not isinstance(data, dict):
        raise ProviderError(f"{name} returned an invalid response. Try again later.")
    return data


class OpenAIProvider:
    name = "ChatGPT"

    def __init__(self, api_key: str, model: str = "gpt-5-mini", *, client: httpx.Client | None = None):
        self._api_key = _api_key(api_key, "OPENAI_API_KEY")
        self.model = _model(model)
        self._client = client

    def generate(self, *, instructions: str, prompt: str) -> str:
        data = _request(
            client=self._client,
            url="https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {self._api_key}"},
            payload={
                "model": self.model,
                "instructions": instructions,
                "input": prompt,
                "max_output_tokens": _MAX_OUTPUT_TOKENS,
                "store": False,
            },
            name=self.name,
            key_variable="OPENAI_API_KEY",
        )
        if data.get("status") == "incomplete" or data.get("incomplete_details"):
            raise ProviderError("ChatGPT returned an incomplete response. Request a smaller plan.")
        if data.get("status") != "completed" or data.get("error"):
            raise ProviderError("ChatGPT did not complete its response. Try again later.")
        output = data.get("output")
        if not isinstance(output, list):
            raise ProviderError("ChatGPT returned no workout text. Try a clearer request.")
        texts: list[str] = []
        for message in output:
            if not isinstance(message, dict) or message.get("type") != "message":
                continue
            if message.get("status") not in (None, "completed"):
                raise ProviderError("ChatGPT returned an incomplete response. Request a smaller plan.")
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "refusal":
                    raise ProviderError("ChatGPT declined the request. Rephrase it with your training constraints.")
                if block.get("type") == "output_text" and isinstance(block.get("text"), str):
                    texts.append(block["text"].strip())
        text = "\n\n".join(part for part in texts if part)
        if not text:
            raise ProviderError("ChatGPT returned no workout text. Try a clearer request.")
        return text


class GeminiProvider:
    name = "Gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash", *, client: httpx.Client | None = None):
        self._api_key = _api_key(api_key, "GEMINI_API_KEY")
        self.model = _model(model)
        self._client = client

    def generate(self, *, instructions: str, prompt: str) -> str:
        data = _request(
            client=self._client,
            url=f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self._api_key},
            payload={
                "systemInstruction": {"parts": [{"text": instructions}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": _MAX_OUTPUT_TOKENS, "candidateCount": 1},
            },
            name=self.name,
            key_variable="GEMINI_API_KEY",
        )
        feedback = data.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise ProviderError("Gemini blocked the request. Rephrase it with your training constraints.")
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise ProviderError("Gemini returned no workout text. Try a clearer request.")
        candidate = candidates[0]
        reason = candidate.get("finishReason")
        if reason == "MAX_TOKENS":
            raise ProviderError("Gemini returned a truncated response. Request a smaller plan.")
        ratings = candidate.get("safetyRatings", [])
        blocked = isinstance(ratings, list) and any(
            isinstance(rating, dict) and rating.get("blocked") is True for rating in ratings
        )
        if blocked or (isinstance(reason, str) and reason in {
            "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"
        }):
            raise ProviderError("Gemini blocked its response. Rephrase it with your training constraints.")
        if reason != "STOP":
            raise ProviderError("Gemini did not complete its response. Try a clearer request.")
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise ProviderError("Gemini returned no workout text. Try a clearer request.")
        texts = [
            part["text"].strip()
            for part in parts
            if isinstance(part, dict) and not part.get("thought") and isinstance(part.get("text"), str)
        ]
        text = "\n\n".join(part for part in texts if part)
        if not text:
            raise ProviderError("Gemini returned no workout text. Try a clearer request.")
        return text
