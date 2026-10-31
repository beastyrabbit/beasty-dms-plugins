"""Draw chat messages with emotes into an image for mpv's `overlay-add`.

ASS text (the fallback renderer) cannot show pictures, so this lays out names,
text, emoji and emotes itself with Pillow. Static parts of each message are
drawn once; animated emotes are pasted per frame.
"""

from __future__ import annotations

import functools
import re
import subprocess
from dataclasses import dataclass, field

from emotes import Emote, EmoteCatalog, EmoteImage, EmoteImages, twitch_emote
from PIL import Image, ImageDraw, ImageFont

# Geometry in a 720-line frame, scaled to the real OSD height (matches the ASS layout).
BASE_HEIGHT = 720
LEFT = 16
BOTTOM = 640
MAX_WIDTH = 470
MAX_PANEL_HEIGHT = 380
FONT_SIZE = 20
EMOTE_SCALE = 1.6
BORDER = 2
# Color emoji fonts only come in this bitmap size; glyphs are scaled from it.
COLOR_EMOJI_SIZE = 109
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)
# Joiners and modifiers belong to the character before them.
COMBINING = re.compile("[‍︎️⃣\U0001f3fb-\U0001f3ff\U000e0020-\U000e007f]")
REGIONAL_INDICATOR = re.compile("[\U0001f1e6-\U0001f1ff]")


def graphemes(text: str) -> list[str]:
    """Split emoji text into what shows as single pictures (enough for emoji)."""
    clusters: list[str] = []
    for char in text:
        joins = clusters and (
            COMBINING.match(char)
            or clusters[-1].endswith("\u200d")
            or (
                len(clusters[-1]) == 1
                and REGIONAL_INDICATOR.match(clusters[-1])
                and REGIONAL_INDICATOR.match(char)
            )
        )
        if joins:
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


def hex_rgba(hex_color: str) -> tuple[int, int, int, int]:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return WHITE
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), 255)


@functools.cache
def font_file(pattern: str) -> str:
    result = subprocess.run(
        ["fc-match", "-f", "%{file}", pattern],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return result.stdout.strip()


@functools.cache
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def is_color_font(path: str) -> bool:
    return "emoji" in path.lower()


def glyph_size(path: str) -> int:
    return COLOR_EMOJI_SIZE if is_color_font(path) else FONT_SIZE


@functools.cache
def notdef_mask(path: str) -> tuple:
    mask = load_font(path, glyph_size(path)).getmask("\U0010ffff")
    return mask.size, bytes(mask)


@functools.cache
def has_glyph(path: str, char: str) -> bool:
    mask = load_font(path, glyph_size(path)).getmask(char)
    return (mask.size, bytes(mask)) != notdef_mask(path)


@functools.cache
def fallback_file(char: str) -> str:
    # Prefer the color emoji font; plain fontconfig fallback often picks a
    # black-and-white emoji glyph from a text font such as DejaVu Sans.
    # fontconfig answers with its closest font even when that font lacks the
    # character, so check: kaomoji such as ╯ must not become empty emoji.
    emoji = font_file(f"emoji:charset={ord(char):x}")
    if is_color_font(emoji) and has_glyph(emoji, char):
        return emoji
    path = font_file(f":charset={ord(char):x}")
    return path if path and has_glyph(path, char) else ""


@dataclass
class Piece:
    """One drawable run: text in one font, or a picture (emoji or emote)."""

    width: int
    height: int
    text: str = ""
    font: ImageFont.FreeTypeFont | None = None
    color: tuple[int, int, int, int] = WHITE
    image: Image.Image | None = None
    emote: EmoteImage | None = None
    zero_width: bool = False


@dataclass
class Line:
    image: Image.Image
    animated: list[tuple[int, int, EmoteImage, int]] = field(default_factory=list)


@dataclass
class Panel:
    """Laid-out lines, bottom-aligned; `draw` gives the image for one moment."""

    x: int
    y: int
    width: int
    height: int
    lines: list[tuple[int, Line]]

    @property
    def animated(self) -> bool:
        return any(
            emote.animated for _, line in self.lines for _, _, emote, _ in line.animated
        )

    def draw(self, now_ms: float) -> Image.Image:
        canvas = Image.new("RGBA", (self.width, self.height))
        for top, line in self.lines:
            canvas.alpha_composite(line.image, (0, top))
            for x, y, emote, height in line.animated:
                canvas.alpha_composite(emote.frame(height, now_ms), (x, top + y))
        return canvas


class ChatRenderer:
    def __init__(self, catalog: EmoteCatalog, images: EmoteImages) -> None:
        self.catalog = catalog
        self.images = images
        self.regular = font_file("sans-serif")
        self.bold = font_file("sans-serif:bold")
        self.italic = font_file("sans-serif:italic")
        # Messages keep their layout until the size changes or an emote arrives.
        self.cache: dict[int, tuple[object, tuple[int, int, int], list[Line]]] = {}
        self.version = 0

    def invalidate(self) -> None:
        self.version += 1

    def layout(self, messages: list, osd_width: int, osd_height: int) -> Panel | None:
        scale = osd_height / BASE_HEIGHT if osd_height > 0 else 1.0
        size = max(8, round(FONT_SIZE * scale))
        left = round(LEFT * scale)
        width = max(1, min(round(MAX_WIDTH * scale), osd_width - 2 * left))
        budget = round(MAX_PANEL_HEIGHT * scale)
        lines: list[Line] = []
        alive = set()
        for message in reversed(messages):
            alive.add(id(message))
            lines[:0] = self._message_lines(message, size, width)
            if sum(line.image.height for line in lines) >= budget:
                break
        for key in list(self.cache):
            if key not in alive:
                del self.cache[key]
        placed: list[tuple[int, Line]] = []
        height = 0
        for line in reversed(lines):
            if height + line.image.height > budget:
                break
            height += line.image.height
            placed.append((-height, line))
        if not placed:
            return None
        panel_width = max(line.image.width for _, line in placed)
        placed = [(top + height, line) for top, line in placed]
        bottom = round(BOTTOM * osd_height / BASE_HEIGHT) if osd_height else BOTTOM
        return Panel(left, max(0, bottom - height), panel_width, height, placed)

    def _message_lines(self, message, size: int, width: int) -> list[Line]:
        key = (size, width, self.version)
        cached = self.cache.get(id(message))
        if cached and cached[0] is message and cached[1] == key:
            return cached[2]
        lines = self._build(message, size, width)
        self.cache[id(message)] = (message, key, lines)
        return lines

    def _build(self, message, size: int, width: int) -> list[Line]:
        color = hex_rgba(message.color)
        body_font = self.italic if message.action else self.regular
        body_color = color if message.action else WHITE
        words = [self._text_word(message.name, self.bold, size, color)]
        if not message.action:
            words[0].extend(self._text_word(":", self.bold, size, color))
        for token in self._tokens(message):
            if isinstance(token, Emote):
                piece = self._emote_piece(token, size)
                words.append(
                    [piece]
                    if piece
                    else self._text_word(token.name, body_font, size, body_color)
                )
            else:
                words.append(self._text_word(token, body_font, size, body_color))
        return self._wrap(words, size, width)

    def _tokens(self, message) -> list[str | Emote]:
        """Words of the message, with known emotes replaced by Emote objects."""
        text = message.text
        native = {start: (end, emote_id) for start, end, emote_id in message.emotes}
        tokens: list[str | Emote] = []
        word_start = 0
        index = 0
        while index <= len(text):
            if index in native and index == word_start:
                end, emote_id = native[index]
                tokens.append(twitch_emote(emote_id, text[index : end + 1]))
                index = word_start = end + 1
                continue
            if index == len(text) or text[index] == " ":
                word = text[word_start:index]
                if word:
                    tokens.append(self.catalog.lookup(word) or word)
                word_start = index + 1
            index += 1
        return tokens

    def _emote_piece(self, emote: Emote, size: int) -> Piece | None:
        image = self.images.get(emote.url)
        if image is None:
            return None
        height = round(size * EMOTE_SCALE)
        width = image.width_for(height)
        # Zero-width emotes are drawn in the per-frame layer too, so they stay on
        # top of an animated emote they decorate.
        if image.animated or emote.zero_width:
            return Piece(width, height, emote=image, zero_width=emote.zero_width)
        return Piece(
            width, height, image=image.frame(height, 0), zero_width=emote.zero_width
        )

    def _text_word(self, text: str, style: str, size: int, color) -> list[Piece]:
        pieces = []
        for path, run in self._runs(text, style):
            if is_color_font(path):
                # One picture per emoji, so a long run can wrap.
                pieces.extend(self._emoji_piece(path, g, size) for g in graphemes(run))
                continue
            font = load_font(path, size)
            ascent, descent = font.getmetrics()
            pieces.append(
                Piece(round(font.getlength(run)), ascent + descent, run, font, color)
            )
        return pieces

    def _runs(self, text: str, style: str) -> list[tuple[str, str]]:
        """Split text into runs that one font can draw."""
        runs: list[tuple[str, str]] = []
        for char in text:
            if runs and COMBINING.match(char):
                runs[-1] = (runs[-1][0], runs[-1][1] + char)
                continue
            path = style if has_glyph(style, char) else fallback_file(char) or style
            if runs and runs[-1][0] == path:
                runs[-1] = (path, runs[-1][1] + char)
            else:
                runs.append((path, char))
        return runs

    def _emoji_piece(self, path: str, text: str, size: int) -> Piece:
        font = load_font(path, COLOR_EMOJI_SIZE)
        left, top, right, bottom = font.getbbox(text)
        glyphs = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)))
        ImageDraw.Draw(glyphs).text((-left, -top), text, font=font, embedded_color=True)
        height = round(size * 1.15)
        width = max(1, round(glyphs.width * height / glyphs.height))
        return Piece(
            width,
            height,
            image=glyphs.resize((width, height), Image.Resampling.LANCZOS),
        )

    def _wrap(self, words: list[list[Piece]], size: int, width: int) -> list[Line]:
        space = round(load_font(self.regular, size).getlength(" "))
        rows: list[list[tuple[int, Piece]]] = [[]]
        x = 0
        for word in words:
            if len(word) == 1 and word[0].zero_width and rows[-1]:
                below_x, below = rows[-1][-1]
                rows[-1].append((below_x + (below.width - word[0].width) // 2, word[0]))
                continue
            gap = space if rows[-1] else 0
            word_width = sum(piece.width for piece in word)
            if rows[-1] and x + gap + word_width > width:
                rows.append([])
                x, gap = 0, 0
            pieces = self._split(word, size) if word_width > width - x - gap else word
            x += gap
            for piece in pieces:
                if rows[-1] and x + piece.width > width:
                    rows.append([])
                    x = 0
                rows[-1].append((x, piece))
                x += piece.width
        return [self._draw_line(row, size) for row in rows if row]

    def _split(self, word: list[Piece], size: int) -> list[Piece]:
        """A word too long for one line, broken into single characters."""
        pieces = []
        for piece in word:
            if piece.font is None:
                pieces.append(piece)
                continue
            for char in piece.text:
                pieces.append(
                    Piece(
                        round(piece.font.getlength(char)),
                        piece.height,
                        char,
                        piece.font,
                        piece.color,
                    )
                )
        return pieces

    def _draw_line(self, row: list[tuple[int, Piece]], size: int) -> Line:
        border = max(1, round(BORDER * size / FONT_SIZE))
        text_font = load_font(self.regular, size)
        ascent, descent = text_font.getmetrics()
        height = (
            max([ascent + descent] + [piece.height for _, piece in row]) + 2 * border
        )
        width = max(x + piece.width for x, piece in row) + 2 * border
        image = Image.new("RGBA", (width, height))
        draw = ImageDraw.Draw(image)
        baseline = (height - ascent - descent) // 2 + ascent
        line = Line(image)
        for x, piece in row:
            x += border
            y = (height - piece.height) // 2
            if piece.font is not None:
                draw.text(
                    (x, baseline),
                    piece.text,
                    font=piece.font,
                    fill=piece.color,
                    anchor="ls",
                    stroke_width=border,
                    stroke_fill=BLACK,
                )
            elif piece.image is not None:
                image.alpha_composite(piece.image, (x, y))
            elif piece.emote is not None:
                line.animated.append((x, y, piece.emote, piece.height))
        return line


def to_bgra(image: Image.Image) -> bytes:
    """Premultiplied BGRA, the only pixel format `overlay-add` takes."""
    return image.convert("RGBa").tobytes("raw", "BGRa")
