"""Watermark area of each picture in a group of similar images.

No detector model: the pictures in a Tier 2 group are already nearly the same
image, so they are compared with *each other*. Where two copies differ in only
a small patch, that patch is an overlay one of them carries — a site name, a
Telegram handle — and its size is the watermark's area. Which copy it belongs
to is decided by edge energy: overlay text is sharp strokes laid on top of the
picture, so the copy with clearly more edges inside the patch owns it.

Measured on `Misa_av` (52 similar groups, labelled by eye):

  * 42 groups were one clean copy plus one with `sexbank.tv` added. The
    watermarked copy is **2-3x larger in bytes** (re-encoded at a higher
    quality), so the old keeper ladder — same pixels, keep the bigger file —
    picked the watermarked one every time. 6 of 49 judgeable groups right
    before, 48 of 49 with this.
  * The changed area separates cleanly: overlay-only pairs <= **1.40%**,
    different shots of the same scene >= **6.32%**, nothing in between.
    `LOCAL_MAX` sits in that gap; above it the pair is "different pictures"
    and says nothing about watermarks.
  * The one miss: a white watermark over a white toilet, 0.01% changed. It
    falls through to the old rule — no worse than before.
  * About 0.15 s per group, nearly all of it reading the files.

Everything here only *suggests*; similar groups are never batch-deleted.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import List, Optional

W = 512               # comparison width; plenty for text tens of pixels tall
DIFF_T = 25           # grey-level difference (after a light blur) that counts
LOCAL_MAX = 0.03      # changed share above this = different shots, not overlay
EDGE_RATIO = 1.3      # owner needs this much more edge energy in the patch
MIN_BLOB = 20         # px at W; smaller patches are JPEG speckle
CLEAN_MAX = 0.001     # attributed share below this counts as "no watermark"
ASPECT_TOL = 1.02     # copies must share a shape to be overlaid pixel by pixel


def _load(path: Path):
    from PIL import Image, ImageOps
    im = Image.open(path)
    im.draft("L", (W * 2, W * 4))    # JPEG decodes straight to a fraction
    return ImageOps.exif_transpose(im).convert("L")


def _edges(a):
    import numpy as np
    gx = np.abs(np.diff(a, axis=1, append=a[:, -1:]))
    gy = np.abs(np.diff(a, axis=0, append=a[-1:, :]))
    return gx + gy


def _components(mask):
    """8-connected labels. The mask is a few percent of a 512px image at
    most (anything bigger was rejected as different shots), so a plain BFS
    is cheap and spares a scipy dependency."""
    import numpy as np
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    n = 0
    for y0, x0 in zip(*np.nonzero(mask)):
        if lab[y0, x0]:
            continue
        n += 1
        lab[y0, x0] = n
        q = deque([(y0, x0)])
        while q:
            y, x = q.popleft()
            for yy in (y - 1, y, y + 1):
                for xx in (x - 1, x, x + 1):
                    if (0 <= yy < h and 0 <= xx < w and mask[yy, xx]
                            and not lab[yy, xx]):
                        lab[yy, xx] = n
                        q.append((yy, xx))
    return lab, n


def pair_areas(a, b) -> Optional[tuple]:
    """(share owned by a, share owned by b), or None when the two are not the
    same picture with an overlay (too much of it differs)."""
    import numpy as np
    from PIL import Image, ImageFilter
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).astype(np.uint8)
    d = np.asarray(Image.fromarray(d).filter(ImageFilter.GaussianBlur(2)))
    mask = d > DIFF_T
    if mask.mean() > LOCAL_MAX:
        return None
    # grow a little so each patch covers whole strokes, not just their cores
    mask = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255)
                      .filter(ImageFilter.MaxFilter(5))) > 0
    lab, n = _components(mask)
    ea = _edges(a.astype(np.float32))
    eb = _edges(b.astype(np.float32))
    to_a = to_b = 0
    for k in range(1, n + 1):
        m = lab == k
        size = int(m.sum())
        if size < MIN_BLOB:
            continue
        sa, sb = float(ea[m].mean()), float(eb[m].mean())
        if sa > sb * EDGE_RATIO:
            to_a += size
        elif sb > sa * EDGE_RATIO:
            to_b += size
        # neither clearly sharper: nobody gets it, rather than a coin toss
    return to_a / a.size, to_b / a.size


def group_areas(paths: List[Path]) -> List[Optional[float]]:
    """Watermark share of each picture, or None where it cannot be told.

    A picture's area is the *largest* it is charged in any comparison that
    worked. Only the comparisons that are the same picture count: in a group
    of two clean shots and their two watermarked copies, each copy against
    its own clean twin tells, while against the other shot it is rejected as
    "different pictures". And it must be the largest, not the smallest: two
    byte-identical watermarked copies compared with each other show nothing,
    so the smallest would call them both clean. An overlay seen against any
    copy is real.
    """
    import numpy as np
    from PIL import Image
    try:
        ims = [_load(p) for p in paths]
    except Exception:
        return [None] * len(paths)
    ar = [im.width / im.height for im in ims]
    if max(ar) / min(ar) > ASPECT_TOL:
        return [None] * len(paths)
    h = max(1, round(W / ar[0]))
    arr = [np.asarray(im.resize((W, h), Image.BILINEAR)) for im in ims]
    out: List[Optional[float]] = [None] * len(arr)
    for i in range(len(arr)):
        for j in range(i + 1, len(arr)):
            r = pair_areas(arr[i], arr[j])
            if r is None:
                continue
            for k, v in ((i, r[0]), (j, r[1])):
                out[k] = v if out[k] is None else max(out[k], v)
    return out
