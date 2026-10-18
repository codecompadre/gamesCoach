import io
import json

import httpx
import pytest
from rich.console import Console

from gamescoach import cli
from gamescoach.config import DEFAULT_GEMINI_MODEL, DEFAULT_OPENAI_MODEL
from gamescoach.providers import GeminiProvider, ProviderError


KEY_NAMES = ("OPENAI_API_KEY", "GAMESCOACH_OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GAMESCOACH_GEMINI_API_KEY")


@pytest.fixture(autouse=True)
def isolate_keys(monkeypatch):
    for name in (*KEY_NAMES, "OPENAI_MODEL", "GEMINI_MODEL", "GAMESCOACH_COACH_NOTES_FILE"):
        monkeypatch.delenv(name, raising=False)


def diagnostic_coaches(monkeypatch, failures=()):
    calls = []
    for classname, label in (("OpenAIProvider", "ChatGPT"), ("GeminiProvider", "Gemini")):
        class Coach:
            def __init__(self, key, model, name=label):
                self.name = name
                self.model = model

            def generate(self, *, instructions, prompt):
                calls.append((self.name, self.model, instructions, prompt))
                if self.name in failures:
                    raise ProviderError(f"{self.name} model unavailable.")
                return "Pace sustainably."
        monkeypatch.setattr(cli, classname, Coach)
    return calls


def test_plain_doctor_is_still_offline_and_uses_shared_default(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-openai-test")
    monkeypatch.setenv("GEMINI_API_KEY", "private-gemini-test")
    calls = diagnostic_coaches(monkeypatch)
    output = io.StringIO()
    assert cli._doctor(Console(file=output, width=160)) == 0
    assert not calls
    assert DEFAULT_GEMINI_MODEL == "gemini-3.1-pro-preview"
    assert DEFAULT_GEMINI_MODEL in output.getvalue()
    assert "private-openai-test" not in output.getvalue()
    assert "private-gemini-test" not in output.getvalue()


def test_live_doctor_checks_both_actual_generators_without_athlete_data(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-openai-test")
    monkeypatch.setenv("GAMESCOACH_GEMINI_API_KEY", "private-gemini-test")
    calls = diagnostic_coaches(monkeypatch)
    output = io.StringIO()
    assert cli._doctor(Console(file=output, width=160), check_api=True) == 0
    assert [(name, model) for name, model, _, _ in calls] == [("ChatGPT", DEFAULT_OPENAI_MODEL), ("Gemini", DEFAULT_GEMINI_MODEL)]
    assert all(prompt == "Give one short tip for pacing a CrossFit workout." for _, _, _, prompt in calls)
    assert output.getvalue().count("live text generation passed") == 2
    assert "API charges" in output.getvalue()
    assert "private-openai-test" not in output.getvalue()
    assert "private-gemini-test" not in output.getvalue()


def test_live_doctor_reports_failure_but_checks_the_other_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-openai-test")
    monkeypatch.setenv("GEMINI_API_KEY", "private-gemini-test")
    monkeypatch.setenv("GEMINI_MODEL", "configured-custom-model")
    calls = diagnostic_coaches(monkeypatch, failures=("ChatGPT",))
    output = io.StringIO()
    assert cli._doctor(Console(file=output, width=160), check_api=True) == 1
    assert len(calls) == 2
    assert calls[-1][1] == "configured-custom-model"
    assert "ChatGPT model unavailable" in output.getvalue()
    assert "Gemini: live text generation passed" in output.getvalue()


def test_missing_live_keys_skip_calls_and_return_failure(monkeypatch):
    calls = diagnostic_coaches(monkeypatch)
    output = io.StringIO()
    assert cli._doctor(Console(file=output), check_api=True) == 1
    assert not calls
    assert output.getvalue().count("skipped") == 2


def test_doctor_flag_uses_live_checks_from_real_command_dispatch(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-openai-test")
    monkeypatch.setenv("GEMINI_API_KEY", "private-gemini-test")
    calls = diagnostic_coaches(monkeypatch)
    assert cli.main(["doctor", "--check-api"]) == 0
    assert len(calls) == 2


def test_explicit_gemini_model_is_preserved_and_404_has_safe_fix():
    calls = []
    def reply(request):
        calls.append(request)
        assert request.url.path.endswith("/gemini-2.5-flash:generateContent")
        return httpx.Response(404, json={"error": {"message": "private-gemini-test"}})
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        provider = GeminiProvider("private-gemini-test", "gemini-2.5-flash", client=client)
        with pytest.raises(ProviderError, match="gemini-3.1-pro-preview") as caught:
            provider.generate(instructions="CrossFit coach.", prompt="Pacing tip.")
    assert "doctor --check-api" in str(caught.value)
    assert "No fallback model was used" in str(caught.value)
    assert "gemini-flash-latest" not in str(caught.value)
    assert "private-gemini-test" not in str(caught.value)
    assert len(calls) == 1  # No silent fallback or duplicate generation charge.
