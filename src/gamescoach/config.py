"""Shared credential selection; values are never rendered or persisted."""

import os

OPENAI_KEY_NAMES = ("OPENAI_API_KEY", "GAMESCOACH_OPENAI_API_KEY")
GEMINI_KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GAMESCOACH_GEMINI_API_KEY")


def credential_binding(names: tuple[str, ...]) -> tuple[str, str] | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return name, value
    return None
