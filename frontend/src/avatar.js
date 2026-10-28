/**
 * Avatar framing shared by the profile header, the home grid and the crop editor.
 *
 * The stored position is a focal point: {x, y} are percentages of the *image*
 * that should sit at the centre of the circle, plus a zoom factor. The image is
 * laid out manually rather than with object-fit/object-position, because with
 * `cover` a portrait photo exactly fills the width and there is no horizontal
 * slack to pan into.
 *
 * The maths is expressed in percentages of the container, so the same framing
 * renders identically at any avatar size (170px header, 130px home tile, 300px
 * editor preview).
 */

/**
 * Image size needed to cover the box, in % of the box, after zoom.
 *
 * `boxAr` is the container's own width/height. It defaults to 1 because this
 * started life as circle-only maths and every existing caller — the profile
 * header, the crop editor — is still a circle. The home grid can now be
 * 16:9 / 4:5 / 9:16, and with the old hard-coded square the picture came out
 * the wrong size in every one of those.
 *
 * The comparison is against `boxAr`, not against 1: "landscape" here means
 * "wider than the box", which is the thing that decides which edge fits.
 */
export function coverPercent(natW, natH, zoom = 1, boxAr = 1) {
  const z = Math.max(1, zoom || 1)
  const b = boxAr > 0 ? boxAr : 1
  if (!natW || !natH) return { w: 100 * z, h: 100 * z }
  const ar = natW / natH
  return ar >= b
    ? { w: 100 * (ar / b) * z, h: 100 * z }   // wider than the box: height fits
    : { w: 100 * z, h: 100 * (b / ar) * z }   // taller: width fits
}

/** Keep the focal point inside the range that still covers the whole box. */
export function clampFocal(natW, natH, pos, boxAr = 1) {
  const zoom = Math.max(1, Math.min(5, pos?.zoom || 1))
  const { w, h } = coverPercent(natW, natH, zoom, boxAr)
  const lx = w > 100 ? 50 / w : 0.5
  const ly = h > 100 ? 50 / h : 0.5
  return {
    x: Math.min(1 - lx, Math.max(lx, (pos?.x ?? 50) / 100)) * 100,
    y: Math.min(1 - ly, Math.max(ly, (pos?.y ?? 50) / 100)) * 100,
    zoom,
  }
}

/** Inline style placing the image so its focal point is centred in the box. */
export function avatarImgStyle(natW, natH, pos, boxAr = 1) {
  if (!natW || !natH) {
    return { width: '100%', height: '100%', objectFit: 'cover' }
  }
  const c = clampFocal(natW, natH, pos, boxAr)
  const { w, h } = coverPercent(natW, natH, c.zoom, boxAr)
  const left = Math.min(0, Math.max(100 - w, 50 - (c.x / 100) * w))
  const top = Math.min(0, Math.max(100 - h, 50 - (c.y / 100) * h))
  return {
    position: 'absolute',
    width: `${w}%`, height: `${h}%`,
    left: `${left}%`, top: `${top}%`,
    maxWidth: 'none', objectFit: 'fill',
  }
}

/** Pixel size of the laid-out image — the drag handler needs real pixels.
 *
 * Square-box only, deliberately: its one caller is the crop editor, which is a
 * circle. If a rectangular cropper ever appears, this needs the same `boxAr`
 * treatment `coverPercent` got. */
export function coverSizePx(box, natW, natH, zoom = 1) {
  const { w, h } = coverPercent(natW, natH, zoom)
  return { w: box * w / 100, h: box * h / 100 }
}
