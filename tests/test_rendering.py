from io import StringIO
import re

import pytest
from rich.console import Console
from rich.markdown import Heading, Markdown, Paragraph, TableElement

from gamescoach.rendering import MAX_COACH_WIDTH, render_coach


@pytest.fixture(autouse=True)
def normal_terminal_environment(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)


def capture(content, *, width=120, color=False, **kwargs):
    output = StringIO()
    console = Console(
        file=output,
        width=width,
        force_terminal=color,
        color_system="standard" if color else None,
    )
    render_coach(console, content, **kwargs)
    return output.getvalue()


def test_headings_have_distinct_semantic_colors_and_keep_their_labels():
    output = capture(
        "## Warm-up\n\nMove easily.\n\n## Strength\n\nBuild smoothly.\n\n"
        "## Aerobic conditioning\n\nStay conversational.\n\n## Nutrition and recovery\n\n"
        "Eat and sleep.\n\n## Scaling notes\n\nAdjust volume.",
        color=True,
    )
    for label, color in (
        ("Warm-up", "36"),
        ("Strength", "35"),
        ("Aerobic conditioning", "32"),
        ("Nutrition and recovery", "33"),
        ("Scaling notes", "33"),
    ):
        assert re.search(r"\x1b\[[0-9;]*" + color + "m" + re.escape(label), output)


def test_existing_bold_section_labels_are_colored_without_rewording():
    output = capture(
        "- **Warm-up — 8 min:** Easy bike.\n- **Strength:** 5 x 3 front squat.\n"
        "- **Wrist scaling:** Use a neutral grip.",
        color=True,
    )
    assert re.search(r"\x1b\[[0-9;]*36mWarm-up — 8 min:", output)
    assert re.search(r"\x1b\[[0-9;]*35mStrength:", output)
    assert re.search(r"\x1b\[[0-9;]*33mWrist scaling:", output)
    assert "5 x 3 front squat." in output


def test_no_color_environment_retains_readable_labels_without_color_codes(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    output = capture("## Warm-up\n\nEasy bike.\n\n## Strength\n\nFront squat.", color=True)
    # Rich may retain emphasis (bold) under NO_COLOR, but never color escapes.
    assert not re.search(r"\x1b\[(?:[0-9;]*;)?(?:3[0-7]|9[0-7]|38|48)[;m]", output)
    assert "Warm-up" in output and "Strength" in output


def test_wide_terminals_keep_a_readable_line_length_and_body_text():
    content = " ".join(f"prescription{number}" for number in range(80))
    output = capture(content, width=180)
    assert max(map(len, output.splitlines())) <= MAX_COACH_WIDTH
    for word in content.split():
        assert word in output


def test_narrow_terminal_wraps_complete_body_without_truncating():
    words = [f"interval{number}" for number in range(120)]
    output = capture("## Aerobic\n\n" + " ".join(words), width=38)
    assert max(map(len, output.splitlines())) <= 38
    for word in words:
        assert word in output
    assert "…" not in output


def test_preserves_lists_tables_code_citations_and_literal_markup():
    content = """## Session

1. Easy row for 8 minutes.
2. Rest for 2 minutes.

| Movement | Prescription |
| --- | --- |
| Front squat | 5 x 3 at RPE 7 |
| Bike | 4 x 6 minutes |

```text
Every 2:00 x 6
8 calories, then rest
```

[CrossFit methodology.pdf, PDF p. 42]

Use [red] to label a zone, if helpful.
"""
    output = capture(content)
    for text in (
        "Easy row for 8 minutes.",
        "Rest for 2 minutes.",
        "Movement",
        "Prescription",
        "Front squat",
        "5 x 3 at RPE 7",
        "4 x 6 minutes",
        "Every 2:00 x 6",
        "8 calories, then rest",
        "[CrossFit methodology.pdf, PDF p. 42]",
        "[red]",
    ):
        assert text in output


@pytest.mark.parametrize("width", [38, 80])
def test_wide_training_tables_keep_every_header_and_prescription(width):
    headers = ["Day", "Warm-up", "Strength", "Gymnastics", "Conditioning", "Recovery", "Scaling notes"]
    first_row = ["Monday", "row-8min", "squat-5x3-at-RPE7", "pull-up-4x6", "bike-4x6min-rest2min", "walk-10min", "neutral-grip"]
    second_row = ["Tuesday", "bike-7min", "press-4x5-at-RPE6", "ring-row-3x10", "row-5x4min-rest1min", "sleep-8h", "reduce-load-10percent"]
    content = "\n".join(
        "| " + " | ".join(row) + " |"
        for row in (headers, ["---"] * len(headers), first_row, second_row)
    )
    output = capture(content, width=width)
    for value in [*headers, *first_row, *second_row]:
        assert value in output
    assert "Row 1" in output and "Row 2" in output
    assert "…" not in output
    assert max(map(len, output.splitlines())) <= width


def test_simple_tables_keep_columns_at_normal_terminal_width():
    output = capture("| Movement | Prescription |\n| --- | --- |\n| Front squat | 5 x 3 at RPE 7 |", width=80)
    assert any("Movement" in line and "Prescription" in line for line in output.splitlines())
    assert "Row 1" not in output
    assert "5 x 3 at RPE 7" in output


def test_titles_are_literal_and_discussion_providers_are_distinct():
    chatgpt = capture("Ready.", title="ChatGPT · draft [x]", discussion=True, provider="OpenAI", color=True)
    gemini = capture("Reviewed.", title="Gemini · review", discussion=True, provider="Gemini", color=True)
    assert "ChatGPT · draft [x]" in chatgpt
    assert "Gemini · review" in gemini
    assert re.search(r"\x1b\[[0-9;]*35m", chatgpt)
    assert re.search(r"\x1b\[[0-9;]*33m", gemini)


def test_renderer_does_not_change_global_markdown_behavior_or_source():
    content = "## Warm-up\n\n**Strength:** 5 x 3."
    render_coach(Console(file=StringIO()), content)
    assert content == "## Warm-up\n\n**Strength:** 5 x 3."
    assert Markdown.elements["heading_open"] is Heading
    assert Markdown.elements["paragraph_open"] is Paragraph
    assert Markdown.elements["table_open"] is TableElement
