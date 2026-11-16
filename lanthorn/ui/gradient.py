"""Gradient text: the one thing in the shell that is allowed to move in colour.

Three places use it, and only these three:

* the special-mode label in the footer ("auto mode") — a fixed purple sweep;
* text the agent is writing *right now* — the in-flight answer and reasoning
  lines, whose newest characters are lit while the older ones settle to grey;
* the live tool card, while a tool is actually running.

Everything else is plain. A shell that tints its headings cannot use tint to
say "this is happening now", which is the only thing this module exists to
say.

The rule the operator set is that glow belongs to gradients and nothing else:
no halo, no shadow, no pulse around static text. So a gradient here is a
*colour ramp across characters*, never a background, and the animation is the
ramp sliding — a moving highlight reads as activity without anything else on
screen moving.

Colours are interpolated in sRGB, which is what a terminal blends anyway; there
is no colour management to be faithful to, and a perceptual space would make
the mid-tones muddy at the small number of steps a 24-bit terminal shows.
"""

from __future__ import annotations

from rich.color import Color
from rich.text import Text

#: The purple sweep for special modes. Dark violet to lavender and back, so the
#: label reads as one word rather than a rainbow, and every stop is bright
#: enough to sit legibly on the black background. Kept as literal RGB because
#: it is a *mode* colour, not a palette colour: it means "this mode is
#: special", the same meaning in every theme, so the theme must not recolour it.
AUTO_GRADIENT: tuple[tuple[int, int, int], ...] = (
    (124, 92, 255),
    (176, 140, 255),
    (214, 190, 255),
    (176, 140, 255),
    (124, 92, 255),
)

#: The ramp for live agent text. Muted on purpose: the answer is the thing
#: being read, and the gradient is a highlight travelling through it, not a
#: neon sign. It runs from the palette's accent through white and back, so it
#: works on every theme (accent is the theme's "live" colour).
LIVE_GRADIENT: tuple[tuple[int, int, int], ...] = (
    (110, 110, 110),
    (210, 210, 210),
    (255, 255, 255),
    (210, 210, 210),
    (110, 110, 110),
)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def sample(stops: tuple[tuple[int, int, int], ...], t: float) -> tuple[int, int, int]:
    """The colour at position *t* (0..1) along *stops*, wrapping at the ends."""
    if not stops:
        return (255, 255, 255)
    if len(stops) == 1:
        return stops[0]
    t = t % 1.0
    pos = t * (len(stops) - 1)
    i = int(pos)
    if i >= len(stops) - 1:
        return stops[-1]
    return _lerp(stops[i], stops[i + 1], pos - i)


def ramp(stops: tuple[tuple[int, int, int], ...], width: int, phase: float = 0.0,
         period: float = 0.0) -> list[str]:
    """*width* hex colours: one full sweep of *stops* across the span, shifted
    by *phase* (0..1) and repeated every *period* characters.

    ``period`` is in characters, not cells, because callers pass the length of
    a short label. A period of 0 or less means "one sweep over the whole span".
    """
    if width <= 0:
        return []
    span = period if period > 0 else float(width)
    out = []
    for i in range(width):
        # One full sweep of *stops* every ``span`` characters: a position of 1
        # would wrap to the first stop, so the sweep is divided by the step
        # count (one fewer than the character count) to land on the last stop
        # at the end of each period and start clean on the next.
        pos = phase + (i % max(1.0, span)) / max(1.0, span - 1)
        out.append(_hex(sample(stops, pos)))
    return out


def paint(text: str, stops: tuple[tuple[int, int, int]], phase: float = 0.0,
          period: float = 0.0, style: str = "") -> Text:
    """*text* with each character coloured by the ramp, phase-shifted.

    A trailing ``style`` is applied to every character alongside the colour, so
    a caller can ask for bold on top of the sweep.
    """
    t = Text(no_wrap=True)
    colours = ramp(stops, len(text), phase, period)
    for ch, col in zip(text, colours):
        s = f"{col} {style}".strip() if style else col
        t.append(ch, style=s)
    return t


def paint_span(text: Text, start: int, end: int,
               stops: tuple[tuple[int, int, int], ...], phase: float = 0.0,
               period: float = 0.0) -> None:
    """Recolour characters ``start..end`` of *text* in place.

    Rich's ``Text.stylize`` applies one style over a range, which cannot express
    a ramp; this rebuilds the span character by character instead. It is only
    ever called on short labels and on the tail of a streaming answer, so the
    per-character cost is bounded by what is on screen.
    """
    if end <= start:
        return
    plain = text.plain
    start = max(0, start)
    end = min(len(plain), end)
    for i in range(start, end):
        span = period if period > 0 else float(end - start)
        pos = phase + (i - start) / max(1.0, span - 1)
        col = _hex(sample(stops, pos))
        text.stylize(col, i, i + 1)


def tail_colours(length: int, window: int, phase: float,
                 stops: tuple[tuple[int, int, int], ...]) -> list[str]:
    """Colours for a streaming answer of *length* characters.

    The first ``length - window`` characters are unlit (the caller styles them
    normally); the last ``window`` carry the moving highlight, brightening
    toward the newest character. That direction is deliberate: the cursor sits
    at the end, so the light should be where the writing is.
    """
    out = []
    for i in range(max(0, length - window), length):
        # 0 at the start of the window, 1 at the newest character.
        pos = (i - (length - window)) / max(1, window - 1)
        out.append(_hex(sample(stops, phase + pos * 0.5)))
    return out


def gradient_colour(t: float, stops: tuple[tuple[int, int, int], ...]) -> str:
    """A single hex colour at *t* — for a spinner glyph or a caret."""
    return _hex(sample(stops, t))


def to_rich(hex_colour: str) -> Color:
    return Color.parse(hex_colour)
