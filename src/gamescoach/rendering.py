"""Readable, terminal-aware presentation of the coaches' original Markdown."""

from __future__ import annotations

import re

from rich import box
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Heading, Markdown, Paragraph, TableElement
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


MAX_COACH_WIDTH = 100

# Labels remain visible in monochrome terminals; color only adds a second cue.
_SECTION_STYLES = (
    (re.compile(r"\b(?:scal(?:e|ing)|notes?|cautions?|limitations?|modifications?)\b", re.I), "bold yellow"),
    (re.compile(r"\b(?:warm[\s‐‑‒–—-]*up|cool[\s‐‑‒–—-]*down|mobility)\b", re.I), "bold cyan"),
    (re.compile(r"\b(?:nutrition|fuel(?:ing)?|hydration|recovery|rest|sleep)\b", re.I), "bold yellow"),
    (re.compile(r"\b(?:strength|skills?|gymnastics|weightlifting|technique)\b", re.I), "bold magenta"),
    (re.compile(r"\b(?:conditioning|aerobic|metcon|wod|emom|amrap|intervals?|pacing|competition)\b", re.I), "bold green"),
)


def _section_style(label: str) -> str | None:
    for pattern, style in _SECTION_STYLES:
        if pattern.search(label):
            return style
    return None


class _CoachHeading(Heading):
    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        text = self.text.copy()
        text.justify = "left"
        text.stylize(_section_style(text.plain) or "bold blue")
        yield text


class _CoachParagraph(Paragraph):
    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        text = self.text.copy()
        text.justify = self.justify
        # Strong labels are common in existing saved replies. Style their spans
        # without rewriting their wording or changing ordinary body paragraphs.
        for span in tuple(text.spans):
            style = console.get_style(span.style)
            if style.bold:
                label = text.plain[span.start : span.end]
                section_style = _section_style(label)
                if section_style:
                    text.stylize(section_style, span.start, span.end)
        yield text


class _CoachTable(TableElement):
    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        headers = self.header.row.cells if self.header is not None and self.header.row is not None else []
        rows = self.body.rows if self.body is not None else []
        column_count = max([len(headers), *(len(row.cells) for row in rows)])
        # Wide weekly schedules become cramped even before columns run out of
        # space. Stacking each row keeps every prescription and label readable.
        stacked = column_count > 4 or (column_count > 2 and options.max_width < 60)
        if stacked and rows:
            for row_number, row in enumerate(rows, 1):
                if row_number > 1:
                    yield Text()
                yield Text(f"Row {row_number}", style="bold blue")
                for index, cell in enumerate(row.cells):
                    label = headers[index].content.copy() if index < len(headers) else Text(f"Column {index + 1}")
                    label.stylize(_section_style(label.plain) or "bold blue")
                    field = Text(overflow="fold")
                    field.append_text(label)
                    field.append(": ")
                    field.append_text(cell.content)
                    yield field
            return

        table = Table(
            box=box.SIMPLE,
            pad_edge=False,
            style="markdown.table.border",
            show_edge=True,
            collapse_padding=True,
        )
        for index in range(column_count):
            heading = headers[index].content.copy() if index < len(headers) else Text()
            heading.stylize("markdown.table.header")
            heading.overflow = "fold"
            # Rich's default column overflow is ellipsis. A prescription must
            # wrap instead of silently losing its final repetitions or units.
            table.add_column(heading, overflow="fold", no_wrap=False, min_width=1)
        for row in rows:
            cells = []
            for cell in row.cells:
                content = cell.content.copy()
                content.overflow = "fold"
                cells.append(content)
            table.add_row(*cells)
        yield table


class _CoachMarkdown(Markdown):
    # Copy the registry: other Markdown rendering and other consoles keep their
    # normal behavior, including after a coaching response has been printed.
    elements = {
        **Markdown.elements,
        "heading_open": _CoachHeading,
        "paragraph_open": _CoachParagraph,
        "table_open": _CoachTable,
    }


def render_coach(
    console: Console,
    content: str,
    *,
    title: str = "Coach",
    discussion: bool = False,
    provider: str | None = None,
) -> None:
    """Print a complete coach reply, respecting terminal width and color policy.

    Markdown remains the source of truth for saved chats and exports. This only
    controls its display; it does not summarize, truncate, or edit that content.
    """
    border_style = "green"
    if discussion:
        name = (provider or "").casefold()
        border_style = "yellow" if "gemini" in name else "magenta" if any(
            word in name for word in ("openai", "chatgpt")
        ) else "blue"

    width = min(MAX_COACH_WIDTH, console.width)
    console.print()
    console.print(
        Panel(
            _CoachMarkdown(content, code_theme="ansi_dark"),
            title=Text(title, style=f"bold {border_style}"),
            title_align="left",
            border_style=border_style,
            box=box.ROUNDED,
            padding=(1, 2 if width >= 50 else 1),
            width=width,
        )
    )
