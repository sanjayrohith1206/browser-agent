"""Crop the logo to its artwork, make the outer white background transparent
(the window's white interior stays opaque), and export extension icons.

Usage (from extension/):
    uv run --with pillow python scripts/make_icons.py "<logo.png>" public/icons assets/logo.png
"""
import sys
from collections import deque
from PIL import Image

src, out_dir, master = sys.argv[1], sys.argv[2], sys.argv[3]
img = Image.open(src).convert("RGBA")
w, h = img.size
px = img.load()

# Flood-fill the background from the borders over near-white pixels.
def near_white(p, t=238): return p[0] >= t and p[1] >= t and p[2] >= t
bg = bytearray(w * h)
q = deque((x, y) for x in range(w) for y in (0, h - 1))
q.extend((x, y) for y in range(h) for x in (0, w - 1))
while q:
    x, y = q.popleft()
    i = y * w + x
    if bg[i] or not near_white(px[x, y]): continue
    bg[i] = 1
    for nx, ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
        if 0 <= nx < w and 0 <= ny < h and not bg[ny*w+nx]: q.append((nx, ny))

# Background -> transparent; its anti-aliased rim gets "white to alpha" so
# edges blend on dark toolbars instead of showing a white halo.
for y in range(h):
    for x in range(w):
        i = y * w + x
        r, g, b, _ = px[x, y]
        edge = not bg[i] and any(
            0 <= x+dx < w and 0 <= y+dy < h and bg[(y+dy)*w + x+dx]
            for dx, dy in ((1,0),(-1,0),(0,1),(0,-1),(2,0),(-2,0),(0,2),(0,-2)))
        if bg[i]:
            px[x, y] = (255, 255, 255, 0)
        elif edge:
            a = max(255 - r, 255 - g, 255 - b) / 255
            if a <= 0: px[x, y] = (255, 255, 255, 0); continue
            un = lambda c: max(0, min(255, round((c - 255 * (1 - a)) / a)))
            px[x, y] = (un(r), un(g), un(b), round(a * 255))

# The frame has a gap where the cursor breaks it, so the fill above also
# cleared the window's interior. Restore it: a white panel under the artwork,
# matching the frame's inner edges (measured on the source image).
from PIL import ImageDraw
under = Image.new("RGBA", img.size, (0, 0, 0, 0))
ImageDraw.Draw(under).rounded_rectangle(
    (302, 457, 914, 831), radius=44, fill=(255, 255, 255, 255),
    corners=(False, False, False, True))
under.alpha_composite(img)
img = under

box = img.getbbox()
art = img.crop(box)
side = round(max(art.size) * 1.06)
square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
square.paste(art, ((side - art.width) // 2, (side - art.height) // 2))
square.resize((512, 512), Image.LANCZOS).save(master, optimize=True)
for size in (16, 32, 48, 128):
    square.resize((size, size), Image.LANCZOS).save(f"{out_dir}/icon-{size}.png", optimize=True)
print("bbox", box, "->", side, "px square")
