"""Deflate Slack avatars: one LED-dot glyph per agent, 512x512 PNG.

    UV_CACHE_DIR=$HOME/.cache/uv-qs uv run --no-project --with pillow python assets/avatars/make_avatars.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent
SIZE, SS = 512, 4                      # final size, supersampling factor
BG = (0x1A, 0x1F, 0x6B)
LAV = (0xC9, 0xCD, 0xFB)
WHITE = (0xFF, 0xFF, 0xFF)
UNLIT = 0.15                           # unlit dot opacity
DOT = 0.74                             # dot diameter / grid pitch

# 13x13 grids, X = lit. Row 0/12 and col 0/12 are breathing room.
GLYPHS = {
    "orchestrator": """
.............
..X...X...X..
..X...X...X..
.XXXXXXXXXXX.
.XXXXXXXXXXX.
..X...X...X..
..X...X...X..
..X...X...X..
..X...X...X..
..X...X...X..
..X...X...X..
..X...X...X..
.............""",
    "reader": """
.............
..XXXXXXX....
..X.....XX...
..X.....XXX..
..X.......X..
..X.XXXXX.X..
..X.......X..
..X.XXXXX.X..
..X.......X..
..X.XXX...X..
..X.......X..
..XXXXXXXXX..
.............""",
    "proposer-card": """
.............
.............
.XXXXXXXX....
.XXXXXXXXX...
.XX.....XXX..
.XX.....XXXX.
.XX.......XX.
.XX.......XX.
.XX.......XX.
.XX.......XX.
.XXXXXXXXXXX.
.XXXXXXXXXXX.
.............""",
    "proposer-lead": """
.............
.............
.......X.....
.......XX....
........XX...
.........XX..
.XXXXXXXXXXX.
.XXXXXXXXXXX.
.........XX..
........XX...
.......XX....
.......X.....
.............""",
    "judge": """
.............
......X......
.XXXXXXXXXXX.
..X...X...X..
.X.X..X..X.X.
X...X.X.X...X
XXXXX.X.XXXXX
......X......
......X......
......X......
......X......
...XXXXXXX...
.............""",
    "coder": """
.............
...XX...XX...
..XX.....XX..
..X.......X..
..X.......X..
..X.......X..
XX.........XX
..X.......X..
..X.......X..
..X.......X..
..XX.....XX..
...XX...XX...
.............""",
    "verifier": """
.............
.............
...........X.
..........XX.
.........XX..
........XX...
.X.....XX....
.XX...XX.....
..XX.XX......
...XXX.......
....X........
.............
.............""",
    "validator": """
.............
.............
.............
...XXXXXXXXX.
..XXXXXXXXXX.
.XX.....XX...
.X.......X...
.X.......X...
.X.......X...
.XX.....XX...
..XXXXXXXX...
...XXXXXX....
.............""",
}
# The contrarian is the lead, reversed.
GLYPHS["proposer-contrarian"] = "\n".join(r[::-1] for r in GLYPHS["proposer-lead"].splitlines())


def mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def render(rows: list[str], lit_color) -> Image.Image:
    n = len(rows)
    assert n == 13 and all(len(r) == 13 for r in rows), rows
    big = SIZE * SS
    img = Image.new("RGB", (big, big), BG)
    d = ImageDraw.Draw(img)
    pitch = big / n
    r = pitch * DOT / 2
    dim = mix(BG, LAV, UNLIT)
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            cx, cy = (x + 0.5) * pitch, (y + 0.5) * pitch
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=lit_color if c == "X" else dim)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    for name, art in GLYPHS.items():
        rows = [r for r in art.splitlines() if r]
        render(rows, WHITE if name == "orchestrator" else LAV).save(OUT / f"{name}.png", optimize=True)
        print(OUT / f"{name}.png")


if __name__ == "__main__":
    main()
