"""
scoreboard_image.py
===================
The Dashboard's scoreboard as a PNG picture, to post in a team chat or on Discord: the score, the
map, and each team's table with the same 12 columns as the Dashboard, in your Team Theme's color.

It's drawn with Pillow (which Streamlit already needs), with a font found on the computer: Segoe
UI on Windows, DejaVu Sans or Liberation Sans on Linux, and Pillow's own font if there's none.
"""

from __future__ import annotations

import io
import os
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from metrics_engine import PRO_LEAGUE_COLUMNS

BACKGROUND = "#0c1624"
PANEL = "#111e30"
STRIPE = "#16263c"
TEXT = "#e6edf3"
DIM = "#8b9bb0"
GOOD = "#3fb950"
BAD = "#f85149"
DEFAULT_ACCENT = "#52d5f2"

PAD = 32           # around the picture
ROW = 38           # a player's row
CELL_PAD = 14      # on each side of a column
MAX_NAME = 260     # a longer username is cut short with "…"
HEADER_WRAP = 92   # a column heading longer than this goes onto two lines
FOOTER = "Made with R6 Match Stats · an unofficial fan project, not endorsed by Ubisoft"

# (regular, bold) font files to try, first found wins
_FONT_FILES = [
    ("segoeui.ttf", "segoeuib.ttf"),
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
    ("Arial.ttf", "Arial Bold.ttf"),
]
_FONT_FOLDERS = [
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
    Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"), Path("/usr/share/fonts/liberation"),
    Path("/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
]


@lru_cache(maxsize=None)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A font of `size` pixels from the computer's fonts, else Pillow's built-in one."""
    for files in _FONT_FILES:
        for folder in _FONT_FOLDERS:
            path = folder / files[bold]
            if path.is_file():
                try:
                    return ImageFont.truetype(str(path), size)
                except OSError:  # a damaged font file: try the next
                    continue
    try:
        return ImageFont.load_default(size=size)
    except OSError:  # FreeType can't load any font: Pillow's small bitmap one
        return ImageFont.load_default_imagefont()


def _color(value: str | None) -> str:
    """A "#rrggbb" color, or the default accent for anything else (a theme from an older version)."""
    return value if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else DEFAULT_ACCENT


def _width(draw: ImageDraw.ImageDraw, text: str, typeface) -> float:
    return draw.textlength(text, font=typeface)


def _fit(draw: ImageDraw.ImageDraw, text: str, typeface, width: float) -> str:
    """`text`, cut short with "…" to fit in `width` pixels."""
    if _width(draw, text, typeface) <= width:
        return text
    while text and _width(draw, text + "…", typeface) > width:
        text = text[:-1]
    return text + "…"


def _heading_lines(draw: ImageDraw.ImageDraw, label: str, typeface) -> list[str]:
    """A column heading on one line, or two if it's long ("Dead for / trade kill")."""
    if _width(draw, label, typeface) <= HEADER_WRAP or " " not in label:
        return [label]
    words = label.split()
    best = min(range(1, len(words)), key=lambda i: max(_width(draw, " ".join(words[:i]), typeface),
                                                       _width(draw, " ".join(words[i:]), typeface)))
    return [" ".join(words[:best]), " ".join(words[best:])]


def scoreboard_png(match: dict, rows: list[dict], accent: str | None = None, when: str = "") -> bytes:
    """The scoreboard of `match` (rows: metrics_engine.pro_league_rows) as PNG bytes, one table per
    team, the winner marked. `accent` is a "#rrggbb" color; `when`, how to say when it was played."""
    accent = _color(accent)
    names = [str(n) for n in list(match.get("team_names") or [])[:2]] + ["Team 1", "Team 2"][len(match.get("team_names") or []):]
    score = list(match.get("final_score") or [0, 0]) + [0, 0]
    rounds = len(match.get("rounds") or [])
    columns = ("Player",) + PRO_LEAGUE_COLUMNS
    regular, bold, small, heading = font(17), font(17, True), font(14), font(13, True)
    big, title = font(44, True), font(24, True)
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    # each column as wide as its widest heading line or value
    headings = {c: _heading_lines(measure, c.upper(), heading) for c in columns}
    widths = {}
    for c in columns:
        values = [_width(measure, str(r.get(c, "")), bold if c in ("Player", "EPS") else regular) for r in rows]
        widths[c] = max([_width(measure, line, heading) for line in headings[c]] + values) + 2 * CELL_PAD
    widths["Player"] = min(max(widths["Player"], 200), MAX_NAME + 2 * CELL_PAD)
    table_width = int(sum(widths.values()))
    width = max(table_width + 2 * PAD, 900)
    head_height = 18 * max(len(lines) for lines in headings.values()) + 14
    teams = [[r for r in rows if r.get("Team") == side] for side in (0, 1)]
    blocks = [(side, team) for side, team in enumerate(teams) if team]
    height = PAD + 150 + sum(52 + head_height + ROW * len(team) + 24 for _, team in blocks) + 40 + PAD

    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 6), fill=accent)

    # the score: Team A  6 : 3  Team B, then the map, rounds, match type and date
    middle = width / 2
    y = PAD + 20
    score_text = f"{score[0]} : {score[1]}"
    score_width = _width(draw, score_text, big)
    draw.text((middle, y), score_text, font=big, fill=TEXT, anchor="mt")
    side_room = middle - score_width / 2 - PAD - 24
    for side, align in ((0, "rt"), (1, "lt")):
        x = middle - score_width / 2 - 24 if side == 0 else middle + score_width / 2 + 24
        won = score[side] > score[1 - side]
        draw.text((x, y + 10), _fit(draw, names[side], title, side_room), font=title, fill=accent if won else TEXT,
                  anchor=align)
    details = " · ".join(bit for bit in (str(match.get("map") or ""), f"{rounds} round{'s' if rounds != 1 else ''}",
                                         str(match.get("match_type") or ""), when) if bit)
    draw.text((middle, y + 70), details, font=regular, fill=DIM, anchor="mt")

    y = PAD + 150
    left = (width - table_width) / 2
    for side, team in blocks:
        won = score[side] > score[1 - side]
        draw.rectangle((left, y, left + table_width, y + 42), fill=PANEL)
        draw.rectangle((left, y, left + 5, y + 42), fill=accent if won else DIM)
        draw.text((left + CELL_PAD + 4, y + 21), _fit(draw, names[side], title, table_width - 120), font=title,
                  fill=TEXT, anchor="lm")
        if won:
            draw.text((left + table_width - CELL_PAD, y + 21), "WIN", font=bold, fill=accent, anchor="rm")
        y += 52
        x = left
        for c in columns:  # headings, the player's to the left and the numbers centered
            for i, line in enumerate(headings[c]):
                if c == "Player":
                    draw.text((x + CELL_PAD, y + 4 + 18 * i), line, font=heading, fill=DIM, anchor="lt")
                else:
                    draw.text((x + widths[c] / 2, y + 4 + 18 * i), line, font=heading, fill=DIM, anchor="mt")
            x += widths[c]
        y += head_height
        for n, r in enumerate(team):
            if n % 2 == 0:
                draw.rectangle((left, y, left + table_width, y + ROW), fill=STRIPE)
            x = left
            for c in columns:
                value = str(r.get(c, ""))
                middle_y = y + ROW / 2
                if c == "Player":
                    draw.text((x + CELL_PAD, middle_y), _fit(draw, value, bold, widths[c] - 2 * CELL_PAD), font=bold,
                              fill=TEXT, anchor="lm")
                elif c in ("KD (+/-)", "Entry") and "(" in value:  # "8-2 (+6)": the difference in green or red
                    main, diff = value.split("(", 1)
                    diff = "(" + diff
                    total = _width(draw, main, regular) + _width(draw, diff, regular)
                    start = x + (widths[c] - total) / 2
                    draw.text((start, middle_y), main, font=regular, fill=TEXT, anchor="lm")
                    draw.text((start + _width(draw, main, regular), middle_y), diff, font=regular,
                              fill=GOOD if diff.startswith("(+") else BAD if diff.startswith("(-") else DIM, anchor="lm")
                else:
                    draw.text((x + widths[c] / 2, middle_y), value, font=bold if c == "EPS" else regular,
                              fill=accent if c == "EPS" else TEXT, anchor="mm")
                x += widths[c]
            y += ROW
        y += 24

    draw.text((middle, height - PAD), FOOTER, font=small, fill=DIM, anchor="mb")
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()
