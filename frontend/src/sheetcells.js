/**
 * Turning a click on a contact sheet into a timestamp.
 *
 * The sheet is one composed JPEG: `cols × rows` tiles with `pad` pixels of
 * gutter around and between them, and the canvas is always the full grid even
 * when the last sheet has fewer frames in it. So the tile size is not
 * something the browser has to be told — it can be solved for from the
 * image's own width:
 *
 *     tileW = (naturalWidth - (cols + 1) * pad) / cols
 *
 * `cols`, `rows` and `pad` come from the server (`/api/sheet/info`) rather
 * than being written down here. They are defined in Python, and a frontend
 * copy would not fail loudly if the layout ever changed — it would quietly
 * point at the wrong cell, which is the kind of wrong that gets noticed a
 * month later by someone wondering why their clips start early.
 *
 * This lives in its own module because `mount.test.mjs` renders every
 * component but never calls a click handler, so arithmetic left inside one is
 * untested by construction.
 */

/**
 * Which cell a click landed on, or null for the gutter / outside.
 *
 * `fx`/`fy` are fractions of the displayed image (0–1), so the caller does not
 * have to care whether the sheet is shown fitted or at native size.
 */
export function cellAt(fx, fy, grid) {
  const { cols, rows, pad, width, height } = grid
  if (!(cols > 0 && rows > 0 && width > 0 && height > 0)) return null
  if (!(fx >= 0 && fx <= 1 && fy >= 0 && fy <= 1)) return null

  const tileW = (width - (cols + 1) * pad) / cols
  const tileH = (height - (rows + 1) * pad) / rows
  if (tileW <= 0 || tileH <= 0) return null

  const col = axis(fx * width, pad, tileW, cols)
  const row = axis(fy * height, pad, tileH, rows)
  if (col == null || row == null) return null
  return row * cols + col
}

/** Index along one axis, or null when the point is in a gutter. */
function axis(px, pad, tile, n) {
  const step = tile + pad
  const i = Math.floor((px - pad) / step)
  if (i < 0 || i >= n) return null
  const within = px - pad - i * step
  return within >= 0 && within < tile ? i : null      // gutter
}

/**
 * The time the frame in `cell` of `sheet` was grabbed at.
 *
 * Frame `n` of the video is grabbed at exactly `n * interval`, so the first
 * cell of the first sheet is 0 and there is no rounding anywhere: the cell
 * *is* an instant, not a bucket. Which is also why the end of a segment is
 * this same value and nothing is added to it — the user's rule: cell 8 at a
 * 30 s interval is 00:03:30, and a segment ending there ends at 00:03:30.
 */
export function cellTime(sheet, cell, perSheet, interval) {
  return (sheet * perSheet + cell) * interval
}

/**
 * How many cells of this sheet actually hold a frame.
 *
 * `frames` is the server's count for the whole set (`/api/sheet/info`), not
 * worked out here from the duration. This used `ceil(duration / interval)`
 * while the server used `floor + 1`, and they disagreed on every exact
 * multiple; the server also keeps the last cell a second clear of the end,
 * because the container's length runs past the last picture (see
 * contactsheet.END_GUARD). One count, published, cannot drift.
 */
export function framesOn(sheet, perSheet, frames) {
  if (!(frames > 0)) return 0
  return Math.max(0, Math.min(perSheet, frames - sheet * perSheet))
}

/**
 * Whether a start/end pair may be added as a segment.
 *
 * Both ends are instants on the same timeline, so the only rule is that the
 * end is after the start — `clip.MIN_SECONDS` on the server is 1.0, and the
 * smallest gap two different cells can produce is one interval.
 */
export function canAdd(start, end) {
  return start != null && end != null && end > start
}

/** Segments in the shape ClipDialog holds them, ordered and merged-free. */
export function sortSegments(segs) {
  return [...segs].sort((a, b) => a.start - b.start || a.end - b.end)
}

/**
 * Where the image is actually painted inside its element.
 *
 * The sheet is shown with `object-fit: contain`, so the element box and the
 * picture inside it are not the same rectangle — there are bars on two sides
 * whenever the aspect ratios differ. `getBoundingClientRect()` returns the
 * element, which is why a click has to be converted through this first: using
 * the element box directly puts every cell boundary a few percent off, and at
 * 30 s per cell a few percent is a clip that starts in the wrong shot.
 *
 * When the viewer is zoomed the element is sized to the image and the two
 * scales come out equal, so the same formula covers both states.
 */
export function containBox(elW, elH, natW, natH) {
  if (!(elW > 0 && elH > 0 && natW > 0 && natH > 0)) return null
  const scale = Math.min(elW / natW, elH / natH)
  const w = natW * scale, h = natH * scale
  return { x: (elW - w) / 2, y: (elH - h) / 2, w, h, scale }
}

/** A cell's rectangle in the sheet's own pixels, for drawing a marker on it. */
export function cellBox(cell, grid) {
  const { cols, rows, pad, width, height } = grid
  if (cell == null || cell < 0 || cell >= cols * rows) return null
  const tileW = (width - (cols + 1) * pad) / cols
  const tileH = (height - (rows + 1) * pad) / rows
  return {
    x: pad + (cell % cols) * (tileW + pad),
    y: pad + Math.floor(cell / cols) * (tileH + pad),
    w: tileW, h: tileH,
  }
}
