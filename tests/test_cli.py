"""Exercise the actual console entry point without making live API calls."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


KEY_VARIABLES = (
    "OPENAI_API_KEY",
    "GAMESCOACH_OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GAMESCOACH_GEMINI_API_KEY",
)


@pytest.fixture
def console_env(tmp_path):
    env = os.environ.copy()
    for name in (*KEY_VARIABLES, "OPENAI_MODEL", "GEMINI_MODEL", "GAMESCOACH_COACH_NOTES_FILE"):
        env.pop(name, None)
    env["GAMESCOACH_DATA_DIR"] = str(tmp_path / "data")
    source = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def run_cli(env, *args, input_text=None):
    return subprocess.run(
        [sys.executable, "-m", "gamescoach", *args],
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )


def saved_sessions(env):
    folder = Path(env["GAMESCOACH_DATA_DIR"]) / "sessions"
    return [json.loads(path.read_text(encoding="utf-8")) for path in folder.glob("*.json")]


def test_demo_plan_history_show_and_export(console_env, tmp_path):
    result = run_cli(
        console_env, "plan", "--kind", "workout", "--request", "Improve CrossFit aerobic repeatability", "--rounds", "1", "--demo", "--show-debate"
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "ChatGPT" in result.stdout and "Gemini" in result.stdout
    assert "static" in result.stdout.lower()
    session, = saved_sessions(console_env)
    assert session["demo"] is True
    assert len(session["turns"]) == 3
    assert session["id"] in result.stdout

    history = run_cli(console_env, "history")
    assert history.returncode == 0
    assert session["id"] in history.stdout
    assert "Demo" in history.stdout

    shown = run_cli(console_env, "show", session["id"], "--transcript")
    assert shown.returncode == 0
    assert "ChatGPT · Draft" in shown.stdout and "Gemini · Review" in shown.stdout
    assert "ChatGPT" in shown.stdout and "Gemini" in shown.stdout
    assert "CrossFit" in shown.stdout

    output = tmp_path / "plan.md"
    exported = run_cli(console_env, "export", session["id"], "--output", str(output))
    assert exported.returncode == 0, exported.stdout
    contents = output.read_text(encoding="utf-8")
    assert session["final_plan"] in contents
    assert session["id"] in contents
    assert "not personally customized" in contents


def test_live_plan_without_keys_is_actionable_without_traceback(console_env):
    result = run_cli(console_env, "plan", "--request", "Create a CrossFit workout")
    assert result.returncode == 1
    assert "OPENAI_API_KEY" in result.stdout
    assert "GEMINI_API_KEY" in result.stdout
    assert "--demo" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr
    assert not saved_sessions(console_env)


def test_generated_plan_is_still_visible_when_local_storage_fails(console_env, tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("existing file", encoding="utf-8")
    console_env["GAMESCOACH_DATA_DIR"] = str(blocked)
    result = run_cli(console_env, "plan", "--demo", "--request", "CrossFit workout")
    assert result.returncode == 1
    assert "Example CrossFit session" in result.stdout
    assert "could not be saved" in result.stdout
    assert "Saved session:" not in result.stdout
    assert blocked.read_text(encoding="utf-8") == "existing file"
    assert "Traceback" not in result.stdout + result.stderr


def test_invalid_profile_import_preserves_existing_profile(console_env, tmp_path):
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps({"name": "Original athlete", "training_days_per_week": 4}), encoding="utf-8")
    imported = run_cli(console_env, "profile", "--file", str(valid))
    assert imported.returncode == 0, imported.stdout
    saved = Path(console_env["GAMESCOACH_DATA_DIR"]) / "profile.json"
    original = saved.read_bytes()

    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"name": "Replacement", "training_days_per_week": 12}), encoding="utf-8")
    result = run_cli(console_env, "profile", "--file", str(invalid))
    assert result.returncode == 1
    assert "training_days_per_week" in result.stdout
    assert saved.read_bytes() == original
    assert "Traceback" not in result.stdout + result.stderr


def test_export_requires_explicit_force_to_replace_file(console_env, tmp_path):
    plan = run_cli(console_env, "--demo", "plan", "--request", "CrossFit training plan")
    assert plan.returncode == 0, plan.stdout
    session, = saved_sessions(console_env)
    output = tmp_path / "existing.md"
    output.write_text("Keep this existing file", encoding="utf-8")

    refused = run_cli(console_env, "export", session["id"], "--output", str(output))
    assert refused.returncode == 1
    assert "--force" in refused.stdout
    assert output.read_text(encoding="utf-8") == "Keep this existing file"
    overwritten = run_cli(console_env, "export", session["id"], "--output", str(output), "--force")
    assert overwritten.returncode == 0
    assert session["final_plan"] in output.read_text(encoding="utf-8")


def test_interactive_demo_chat_then_quit(console_env):
    result = run_cli(console_env, "--demo", input_text="1\nCreate a CrossFit aerobic workout\n/quit\nq\n")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "personal CrossFit athlete coach" in result.stdout
    assert "ChatGPT" in result.stdout and "Gemini" in result.stdout
    session, = saved_sessions(console_env)
    assert session["kind"] == "chat"
    assert session["request"] == "Create a CrossFit aerobic workout"
    assert session["demo"] is True


def test_profile_wizard_persists_input_and_keeps_optional_stats_blank(console_env):
    answers = [
        "Console Athlete", "Experienced CrossFit athlete", "Aerobic repeatability",
        "4", "60", "Rower and barbell", "", "", "", "", "",
    ]
    edited = run_cli(console_env, "profile", input_text="\n".join(answers) + "\n")
    assert edited.returncode == 0, edited.stderr + edited.stdout
    path = Path(console_env["GAMESCOACH_DATA_DIR"]) / "profile.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    assert profile["name"] == "Console Athlete"
    assert profile["experience"] == "Experienced CrossFit athlete"
    assert profile["training_days_per_week"] == 4
    assert profile["session_minutes"] == 60
    assert profile["equipment"] == "Rower and barbell"
    assert profile["age"] is None and profile["bodyweight_kg"] is None
    assert profile["competition_date"] == ""

    kept = run_cli(console_env, "profile", input_text="\n" * 11)
    assert kept.returncode == 0, kept.stderr + kept.stdout
    assert json.loads(path.read_text(encoding="utf-8")) == profile
    shown = run_cli(console_env, "profile", "show")
    assert shown.returncode == 0
    assert "Console Athlete" in shown.stdout


def test_doctor_uses_engine_key_precedence_without_disclosing_keys(console_env):
    sentinels = {name: f"private-test-value-{index}" for index, name in enumerate(KEY_VARIABLES)}
    console_env.update(sentinels)
    result = run_cli(console_env, "doctor")
    assert result.returncode == 0
    assert "Configured: OPENAI_API_KEY" in result.stdout
    assert "Configured: GEMINI_API_KEY" in result.stdout
    assert "Configured: GAMESCOACH_OPENAI_API_KEY" not in result.stdout
    assert "Configured: GOOGLE_API_KEY" not in result.stdout
    assert all(secret not in result.stdout + result.stderr for secret in sentinels.values())


def test_doctor_uses_nonempty_fallback_key(console_env):
    console_env.update({"OPENAI_API_KEY": "   ", "GAMESCOACH_OPENAI_API_KEY": "alias-key", "GEMINI_API_KEY": "gemini-key"})
    result = run_cli(console_env, "doctor")
    assert result.returncode == 0
    assert "Configured: GAMESCOACH_OPENAI_API_KEY" in result.stdout
    assert "alias-key" not in result.stdout
