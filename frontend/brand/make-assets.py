"""Turn the logo artwork into the files the app actually serves.

    python brand/make-assets.py          (from frontend/)

Sources are `brand/logo.png` (full lockup) and `brand/logo-mark.png` (the VA
monogram alone). Everything in `public/` named `brand-*` or `favicon-*` is
generated from them — do not hand-edit those.

Two things this does that are worth knowing:

**De-keying, not "delete the black".** The artwork is flat two-colour on a
solid black background. Removing black by matching it leaves the anti-aliased
edges blended toward black, i.e. a dark fringe that shows up against the
app's #1e1e2e panels. Every edge pixel here is really `colour * t` for some
0..1, so recovering `t` gives the alpha the artwork should have had and the
edges come out clean.

**Sized assets, not one big PNG.** The header shows the mark at 20px; handing
the browser the 620px original to downscale on every page load wastes both
bandwidth and quality. Each output is rendered at the size it is used at
(x2 for HiDPI).

The logo colour is read from the image rather than hard-coded, so a recolour
needs no edit here — it was already recoloured once, from #AE72F9 to the
app's accent #7C7CF0.
"""
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
PUBLIC = HERE.parent / "public"


def logo_colour(im: Image.Image) -> tuple:
    """The most common colour that is not the black background."""
    # getcolors over getdata: the latter is deprecated in Pillow 14, and this
    # counts in C instead of building a list of half a million tuples
    counts = im.convert("RGB").getcolors(maxcolors=1 << 24) or []
    for _n, (r, g, b) in sorted(counts, reverse=True):
        if r + g + b > 60:            # not the background
            return (r, g, b)
    raise SystemExit("找不到 LOGO 的顏色 —— 圖是全黑的嗎？")


def dekey(src: Path) -> Image.Image:
    im = Image.open(src)
    colour = logo_colour(im)
    a = np.asarray(im.convert("RGB"), dtype=np.float32)
    t = np.clip(np.max(a / np.array(colour, dtype=np.float32), axis=2), 0.0, 1.0)
    out = np.zeros((*t.shape, 4), dtype=np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = colour
    out[..., 3] = (t * 255).round().astype(np.uint8)
    im = Image.fromarray(out, "RGBA")
    print(f"  {src.name}: 顏色 #{'%02X%02X%02X' % colour}")
    return im.crop(im.getbbox())       # trim the now-transparent margins


def save(im: Image.Image, name: str, *, width=None, height=None, square=False):
    im = im.copy()
    if square:
        # favicons must be square or the browser stretches them
        s = max(im.size)
        pad = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        pad.paste(im, ((s - im.size[0]) // 2, (s - im.size[1]) // 2))
        im = pad
    if width:
        im = im.resize((width, round(im.size[1] * width / im.size[0])),
                       Image.LANCZOS)
    elif height:
        im = im.resize((round(im.size[0] * height / im.size[1]), height),
                       Image.LANCZOS)
    dst = PUBLIC / name
    im.save(dst, optimize=True)
    print(f"  {name:<20}{im.size[0]:>4}x{im.size[1]:<4} "
          f"{dst.stat().st_size / 1024:>6.1f} KB")


def main():
    for f in ("logo.png", "logo-mark.png"):
        if not (HERE / f).is_file():
            raise SystemExit(f"缺少 brand/{f}")
    print("去背：")
    lockup = dekey(HERE / "logo.png")
    mark = dekey(HERE / "logo-mark.png")
    print("產生：")
    save(lockup, "brand-lockup.png", width=560)      # login screen
    # header, shown at 32px: 96 covers a 3x phone screen without being
    # a file worth worrying about (a few KB)
    save(mark, "brand-mark.png", height=96)
    save(mark, "favicon-32.png", width=32, square=True)
    save(mark, "favicon-180.png", width=180, square=True)


if __name__ == "__main__":
    main()
