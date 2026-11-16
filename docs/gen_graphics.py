"""Regenerate the animated README graphics (docs/hero.svg, docs/terminal.svg).

Run:  python docs/gen_graphics.py   (from an activated venv)

Both files are plain, self-contained SVG — no external assets, no JS. GitHub
renders the CSS animations in place. Every animation is layered ON TOP of a
fully-visible base state, so a renderer that samples the first frame (or
ignores animation entirely) still shows the complete graphic.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lanthorn.ui.banner import FONT, FONT_HEIGHT

OUT = Path(__file__).resolve().parent
# The plain palette: the colours the shell itself uses by default. One
# foreground, one grey, and nothing that competes with the text — the neon
# set these were before made the graphics look like a different product from
# the one that launches.
BG, GRID, GREEN, DIM, ACCENT, AMBER = (
    "#000000", "#141414", "#d4d4d4", "#6b6b6b", "#d4d4d4", "#d7ba7d")

W, H = 1280, 420
word, GW, SP, cell = "LANTHORN", 6, 1, 19
# Glyphs advance by ``GW + SP`` cells and each is ``GW`` wide, so the last
# one's ink ends one column short of the pitch. Centre the ink, not the pitch.
# The NUDGE is measured, not derived: this block font's letters are not all
# six columns of solid fill (the "A" and "O" open with a blank column), so
# centring the six-column cell leaves the visible word about one column right
# of centre. One cell left of that is what a rendered frame measures even.
pitch = len(word) * (GW + SP) - SP
ink_cells = pitch - SP
NUDGE = cell
x0 = (W - ink_cells * cell) / 2 - NUDGE
y0 = 126

glyphs = []
for gi, ch in enumerate(word):
    gx = x0 + gi * (GW + SP) * cell
    rects = []
    for r in range(FONT_HEIGHT):
        row, c = FONT[ch][r], 0
        while c < len(row):
            if row[c] == "█":
                run = 0
                while c + run < len(row) and row[c + run] == "█":
                    run += 1
                rects.append(f'<rect x="{gx + c*cell:.0f}" y="{y0 + r*cell}" '
                             f'width="{run*cell}" height="{cell}"/>')
                c += run
            else:
                c += 1
    # base opacity is 1 — the animation only adds a glitch flicker on top.
    glyphs.append(f'<g class="gl" style="animation-delay:{gi*0.11:.2f}s">'
                  + "".join(rects) + "</g>")
banner = "\n      ".join(glyphs)

sub_y = 288
(OUT / "hero.svg").write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="LANTHORN">
  <defs>
    <clipPath id="frame"><rect x="0" y="0" width="{W}" height="{H}" rx="10"/></clipPath>
  </defs>

  <style>
    .gl rect {{ fill: {GREEN}; }}

    .tag, .meta {{ paint-order: stroke; stroke: {BG}; stroke-width: 5;
                   stroke-linejoin: round; }}
    .tag {{ font: 600 15px ui-monospace, Menlo, Consolas, monospace;
            letter-spacing: 2px; fill: {DIM}; }}
    .meta {{ font: 13px ui-monospace, Menlo, Consolas, monospace;
             letter-spacing: 1.6px; fill: {DIM}; }}

  </style>

  <g clip-path="url(#frame)">
    <rect width="{W}" height="{H}" fill="{BG}"/>
    <g>{banner}</g>

    <text class="tag" x="{W/2:.0f}" y="{sub_y}" text-anchor="middle">reads the repo, runs the work</text>

    <text class="meta" x="{W/2:.0f}" y="360" text-anchor="middle" opacity="0.8">by Mr-Destroyer</text>

    <rect x="0.5" y="0.5" width="{W-1}" height="{H-1}" rx="10" fill="none" stroke="{DIM}" stroke-opacity="0.55"/>
  </g>
</svg>
''', encoding="utf-8")


# ------------------------------------------------------------ terminal ------
LINES = [
    ("prompt",  "add a rate limiter to the API client"),
    ("dim",     "reading src/api/client.py"),
    ("tool",    "bash  pytest tests/test_client.py -q"),
    ("ok",      "9 passed in 0.31s"),
    ("tool",    "write_file  src/util/limiter.py"),
    ("ok",      "wrote src/util/limiter.py (42 lines)"),
]

def terminal() -> str:
    W, H = 1100, 430
    pad, lh, top = 34, 34, 104
    rows = []
    for i, (kind, text) in enumerate(LINES):
        delay = 0.55 + i * 0.62
        color = {"prompt": GREEN, "dim": DIM, "tool": DIM,
                 "ok": GREEN, "info": AMBER}[kind]
        prefix = "❯ " if kind == "prompt" else ("  " if kind in ("ok", "dim", "info") else "  ")
        esc = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        rows.append(
            f'<text class="ln" x="{pad}" y="{top + i*lh}" fill="{color}" '
            f'style="animation-delay:{delay:.2f}s">{prefix}{esc}</text>'
        )
    body = "\n      ".join(rows)
    last_y = top + (len(LINES) - 1) * lh
    cur_delay = 0.55 + len(LINES) * 0.62

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="LANTHORN session">
  <defs>
    <clipPath id="tframe"><rect x="0" y="0" width="{W}" height="{H}" rx="12"/></clipPath>
    <filter id="tglow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="3.5" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <style>
    /* Opacity is never animated: a staggered fade would leave every line
       invisible during its own delay, which is exactly what a renderer that
       ignores animation (or samples frame zero) would show. Only the slide
       moves, so the text is legible in every renderer and still animates in. */
    .ln {{ font: 16px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
           animation: rise .5s ease-out backwards; }}
    @keyframes rise {{ from {{ transform: translateY(8px); }}
                       to   {{ transform: translateY(0); }} }}
    .cur {{ animation: blink 1.05s steps(1,end) infinite; }}
    @keyframes blink {{ 0%,49% {{ opacity: 1; }} 50%,100% {{ opacity: 0; }} }}
    .title {{ font: 400 13px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
              fill: {DIM}; }}
    @media (prefers-reduced-motion: reduce) {{
      .ln {{ animation: none; opacity: 1; }} .cur {{ animation: none; }}
    }}
  </style>

  <g clip-path="url(#tframe)">
    <rect width="{W}" height="{H}" fill="{BG}"/>
    <rect width="{W}" height="46" fill="#111111"/>
    <circle cx="28" cy="23" r="6.5" fill="#ff5f57"/>
    <circle cx="50" cy="23" r="6.5" fill="#febc2e"/>
    <circle cx="72" cy="23" r="6.5" fill="#28c840"/>
    <text class="title" x="{W/2:.0f}" y="28" text-anchor="middle">Lanthorn</text>
    <line x1="0" y1="46" x2="{W}" y2="46" stroke="{DIM}" stroke-opacity="0.6"/>

    <text x="{pad}" y="76" fill="{ACCENT}" style="font:13px ui-monospace,Menlo,monospace">
      <tspan font-weight="600">✳ Lanthorn</tspan><tspan fill="{DIM}">  v1.0.0</tspan>
    </text>
    <text x="{pad}" y="96" fill="{ACCENT}" style="font:13px ui-monospace,Menlo,monospace">deepseek-v4.1-flash</text>

      {body}

    <rect class="cur" x="{pad}" y="{H-17}" width="9" height="14" fill="{GREEN}"/>

    <text x="{pad}" y="{H-46}" fill="{DIM}" style="font:13px ui-monospace,Menlo,monospace">
      deepseek-v4.1-flash   ·   api   ·   ~/projects/api   ·   <tspan fill="{ACCENT}">auto</tspan>
    </text>
    <text x="{pad}" y="{H-28}" fill="{DIM}" style="font:13px ui-monospace,Menlo,monospace">
      ● idle   ·   auto mode
    </text>
    <rect x="{pad}" y="{H-20}" width="{W-2*pad}" height="0.5" fill="{DIM}"/>
    <text x="{pad}" y="{H-6}" fill="{DIM}" style="font:13px ui-monospace,Menlo,monospace">❯ </text>
    <rect x="0.5" y="0.5" width="{W-1}" height="{H-1}" rx="12" fill="none" stroke="{DIM}" stroke-opacity="0.55"/>
    <rect x="0.5" y="0.5" width="{W-1}" height="{H-1}" rx="12" fill="none" stroke="{DIM}" stroke-opacity="0.55"/>
  </g>
</svg>
'''


(OUT / "terminal.svg").write_text(terminal(), encoding="utf-8")
print("wrote docs/hero.svg and docs/terminal.svg")
