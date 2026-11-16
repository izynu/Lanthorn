"""The gradient engine: colour ramps, phases, and the spans that use them."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.text import Text  # noqa: E402

from lanthorn.ui import gradient as G  # noqa: E402

_failures = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
        print(f"  FAIL  {name}" + (f"  — {extra}" if extra else ""))
    else:
        print(f"  ok    {name}")


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def test_sample_endpoints() -> None:
    stops = ((0, 0, 0), (100, 100, 100), (0, 0, 0))
    check("sample: t=0 is the first stop", G.sample(stops, 0.0) == (0, 0, 0))
    check("sample: t=0.5 is the middle stop", G.sample(stops, 0.5) == (100, 100, 100))
    check("sample: t=1 wraps to the first stop", G.sample(stops, 1.0) == (0, 0, 0))
    check("sample: t=0.25 is half way up",
          G.sample(stops, 0.25) == (50, 50, 50), str(G.sample(stops, 0.25)))
    check("sample: negative t wraps", G.sample(stops, -0.5) == (100, 100, 100))
    check("sample: one stop is constant", G.sample(((9, 9, 9),), 0.7) == (9, 9, 9))


def test_ramp_length_and_phase() -> None:
    stops = ((0, 0, 0), (255, 255, 255))
    r = G.ramp(stops, 10)
    check("ramp: one colour per character", len(r) == 10, str(len(r)))
    check("ramp: every entry is a hex colour",
          all(c.startswith("#") and len(c) == 7 for c in r), str(r[:3]))
    shifted = G.ramp(stops, 10, phase=0.5)
    check("ramp: the phase shifts the colours", shifted != r, f"{r[:2]} {shifted[:2]}")
    check("ramp: a full-period shift is identity",
          G.ramp(stops, 10, phase=1.0) == r, f"{r[:3]} {G.ramp(stops, 10, phase=1.0)[:3]}")
    check("ramp: width 0 is empty", G.ramp(stops, 0) == [])


def test_ramp_period_repeats() -> None:
    stops = ((0, 0, 0), (255, 255, 255))
    r = G.ramp(stops, 12, period=6.0)
    check("ramp: a period of 6 repeats twice",
          r[:6] == r[6:], str(r))


def test_paint_keeps_text_and_colours_each_char() -> None:
    t = G.paint("auto mode", G.AUTO_GRADIENT, phase=0.2)
    check("paint: the text is unchanged", t.plain == "auto mode", repr(t.plain))
    spans = [s for s in t.spans if s.style and "#" in str(s.style)]
    check("paint: every character carries a colour", len(spans) == len("auto mode"),
          str(len(spans)))
    cols = [str(s.style) for s in spans]
    check("paint: the ramp runs across the label", len(set(cols)) >= 4, str(cols))
    bold = G.paint("x", G.AUTO_GRADIENT, style="bold")
    check("paint: the extra style rides along", "bold" in str(bold.spans[0].style))


def test_auto_gradient_is_purple() -> None:
    for stop in G.AUTO_GRADIENT:
        r, g, b = stop
        check(f"auto gradient stop {stop} is violet", b > r > g and b > 150,
              f"{stop}")


def test_paint_span_only_touches_the_range() -> None:
    t = Text("hello world", style="white")
    G.paint_span(t, 6, 11, G.LIVE_GRADIENT, phase=0.3)
    lit = [s for s in t.spans if "#" in str(s.style)]
    check("paint_span: only the requested range is coloured",
          all(s.start >= 6 and s.end <= 11 for s in lit), str([(s.start, s.end) for s in lit]))
    check("paint_span: the whole range is coloured",
          sum(s.end - s.start for s in lit) == 5, str([(s.start, s.end) for s in lit]))
    check("paint_span: out-of-range indices are clamped",
          _safe(lambda: G.paint_span(t, -5, 999, G.LIVE_GRADIENT)))
    check("paint_span: an empty range is a no-op",
          _safe(lambda: G.paint_span(t, 3, 3, G.LIVE_GRADIENT)))


def _safe(fn) -> bool:
    try:
        fn()
        return True
    except Exception:
        return False


def test_tail_colours() -> None:
    cols = G.tail_colours(100, 10, 0.0, G.LIVE_GRADIENT)
    check("tail: one colour per window character", len(cols) == 10, str(len(cols)))
    check("tail: brightens toward the newest character",
          _rgb(cols[-1])[0] > _rgb(cols[0])[0], f"{cols[0]} -> {cols[-1]}")
    check("tail: a short answer gets a short tail",
          len(G.tail_colours(3, 10, 0.0, G.LIVE_GRADIENT)) == 3)


def test_phase_is_pure() -> None:
    """The phase is a parameter, never read from a clock — the same idiom the
    boot spin and the web spinner follow, and the reason they are testable."""
    import inspect
    src = inspect.getsource(G)
    check("gradient: no clock is read inside the module",
          "time.monotonic" not in src and "time.time" not in src)


def main() -> int:
    test_sample_endpoints()
    test_ramp_length_and_phase()
    test_ramp_period_repeats()
    test_paint_keeps_text_and_colours_each_char()
    test_auto_gradient_is_purple()
    test_paint_span_only_touches_the_range()
    test_tail_colours()
    test_phase_is_pure()
    print(f"\n{'FAILED' if _failures else 'all gradient checks passed'}"
          + (f" ({_failures})" if _failures else ""))
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
