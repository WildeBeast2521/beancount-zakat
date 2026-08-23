"""Terminal table rendering that degrades gracefully on narrow terminals.

No 130-column assumption: when a table cannot fit, it is rendered as stacked
label/value blocks instead of being truncated or wrapped into nonsense.
"""

from __future__ import annotations

import shutil
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass

Row = Sequence[str]


def terminal_width(default: int = 100) -> int:
    try:
        return max(40, shutil.get_terminal_size((default, 24)).columns)
    except OSError:  # pragma: no cover - very unusual environments
        return default


@dataclass(frozen=True, slots=True)
class Column:
    title: str
    align: str = "left"
    #: Columns with a lower priority number are dropped last when space is tight.
    priority: int = 1

    def pad(self, text: str, width: int) -> str:
        if self.align == "right":
            return text.rjust(width)
        if self.align == "center":
            return text.center(width)
        return text.ljust(width)


def _widths(columns: Sequence[Column], rows: Sequence[Row]) -> list[int]:
    widths = [len(column.title) for column in columns]
    for row in rows:
        for index, cell in enumerate(row):
            if index < len(widths):
                widths[index] = max(widths[index], len(cell))
    return widths


def render_table(
    columns: Sequence[Column],
    rows: Sequence[Row],
    *,
    width: int | None = None,
    gap: str = "  ",
    footer: Row | None = None,
    empty: str = "(no rows)",
) -> str:
    """Render a table, falling back to stacked blocks when it will not fit."""
    if not rows and footer is None:
        return f"  {empty}"

    all_rows = list(rows) + ([list(footer)] if footer is not None else [])
    available = width if width is not None else terminal_width()
    widths = _widths(columns, all_rows)
    total = sum(widths) + len(gap) * (len(columns) - 1)

    if total > available:
        return _render_stacked(columns, rows, footer=footer, available=available)

    lines: list[str] = []
    header = gap.join(
        column.pad(column.title, widths[index]) for index, column in enumerate(columns)
    )
    rule = "-" * len(header)
    lines.append(header)
    lines.append(rule)
    for row in rows:
        lines.append(
            gap.join(
                columns[index].pad(cell, widths[index])
                for index, cell in enumerate(row)
            )
        )
    if footer is not None:
        lines.append(rule)
        lines.append(
            gap.join(
                columns[index].pad(cell, widths[index])
                for index, cell in enumerate(footer)
            )
        )
    return "\n".join(lines)


def _render_stacked(
    columns: Sequence[Column],
    rows: Sequence[Row],
    *,
    footer: Row | None,
    available: int | None = None,
) -> str:
    available = available if available is not None else terminal_width()
    # An untitled first column is a row label, so use its value as the heading
    # of each block rather than emitting a blank "  : value" line.
    heading_first = bool(columns) and not columns[0].title.strip()
    body = columns[1:] if heading_first else columns
    label_width = max((len(column.title) for column in body), default=0)
    blocks: list[str] = []

    def block(row: Row, marker: str = "") -> str:
        lines = []
        if marker:
            lines.extend(wrap(marker, available, indent="  "))
        if heading_first and row:
            lines.extend(wrap(row[0], available, indent="  "))
        for offset, column in enumerate(body):
            index = offset + 1 if heading_first else offset
            if index >= len(row):
                continue
            lines.extend(
                field(
                    column.title,
                    row[index],
                    available,
                    indent="    ",
                    label_width=label_width,
                )
            )
        return "\n".join(lines)

    for row in rows:
        blocks.append(block(row))
    if footer is not None:
        blocks.append(block(footer, marker="-- TOTAL --"))
    return "\n\n".join(blocks)


def heading(text: str, width: int | None = None, char: str = "=") -> str:
    available = width if width is not None else terminal_width()
    return f"{text}\n{char * min(len(text), available)}"


def rule(width: int | None = None, char: str = "-") -> str:
    available = width if width is not None else terminal_width()
    return char * available


def wrap(text: str, width: int | None = None, *, indent: str = "") -> list[str]:
    """Wrap prose to the available width, preserving explicit line breaks."""
    available = max(20, (width if width is not None else terminal_width()))
    body = available - len(indent)
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        if not paragraph.strip():
            lines.append("")
            continue
        lines.extend(
            f"{indent}{line}"
            for line in textwrap.wrap(
                paragraph,
                width=max(20, body),
                break_long_words=False,
                break_on_hyphens=False,
            )
            or [indent.rstrip()]
        )
    return lines


def elide(text: str, width: int) -> str:
    """Shorten *text* from the left, keeping its tail, e.g. a file path."""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[-width:]
    return "..." + text[-(width - 3) :]


def field(
    label: str,
    value: str,
    width: int | None = None,
    *,
    indent: str = "  ",
    label_width: int = 20,
) -> list[str]:
    """Render one ``label : value`` line, wrapping the value if it is long."""
    available = width if width is not None else terminal_width()
    prefix = f"{indent}{label.ljust(label_width)} : "
    room = available - len(prefix)
    if len(value) <= room:
        return [f"{prefix}{value}"]
    if room < 24:
        # Too narrow to align: put the label on its own line and indent the
        # value beneath it.
        body_indent = indent + "  "
        return [f"{indent}{label}:", *wrap(value, available, indent=body_indent)]
    wrapped = wrap(value, room)
    continuation = " " * len(prefix)
    return [f"{prefix}{wrapped[0]}"] + [f"{continuation}{line}" for line in wrapped[1:]]
