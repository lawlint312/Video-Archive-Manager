// Deciding whether a drag was a "next photo" gesture.
//
// Split out from MediaViewer so the thresholds can be tested without a phone:
// every one of them is a judgement call that looks arbitrary six months later,
// and getting them wrong is felt rather than seen (the viewer either misses
// deliberate flicks or changes photo when you meant to hold still).
//
// Only used on narrow screens, and only while the image is not zoomed in —
// once zoomed, the same drag pans the picture instead.

// Movement below this is treated as a tap, not a drag: fingers wobble.
export const DEADZONE = 12
// Past this much vertical travel the swipe counts however slowly it was made.
// Proportional to the screen, with a floor: the first version used a flat
// 90px, which is 11% of a phone screen — light enough that simply moving your
// thumb while looking at a picture kept changing it. 20% has to be deliberate.
export const DISTANCE_RATIO = 0.20
export const MIN_DISTANCE = 120
// ...or a flick: a fast, short gesture is unambiguous too, and demanding the
// full distance would make the viewer feel heavy. px per millisecond.
export const VELOCITY = 0.9
// But a flick still has to travel. Without this floor the velocity test let a
// 15px twitch through — a quick nudge covers 15px in 15ms, which is 1 px/ms
// and sailed past VELOCITY. Raising the distance threshold did nothing to fix
// it, because the flick path skipped that check entirely: reported as "手指
// 往上一點點就會觸發換頁".
export const FLICK_MIN = 60
// A gesture has to be clearly vertical. Without this, dragging across a photo
// keeps flicking to the next one.
export const VERTICAL_BIAS = 1.5

export function distanceFor(height = 800) {
  return Math.max(MIN_DISTANCE, height * DISTANCE_RATIO)
}

/**
 * @param {{dx:number, dy:number, dt:number, height:number, axis:'y'|'x'}} g
 *   travel, duration, the screen's extent along the axis, and the axis
 * @returns {'next'|'prev'|'cancel'}
 *
 * Portrait swipes vertically: up is "next", matching the way a list scrolls.
 * Landscape swipes horizontally (the user's choice — a phone on its side is
 * held like a photo album): left is "next", content moving left to reveal
 * what comes after it. Same thresholds either way; `height` is simply the
 * extent along whichever axis is in use.
 */
export function swipeOutcome({ dx = 0, dy = 0, dt = 0, height = 800, axis = 'y' }) {
  const main = axis === 'x' ? dx : dy
  const cross = axis === 'x' ? dy : dx
  const am = Math.abs(main), ac = Math.abs(cross)
  if (am < DEADZONE) return 'cancel'
  if (am < ac * VERTICAL_BIAS) return 'cancel'
  const fast = dt > 0 && am >= FLICK_MIN && am / dt >= VELOCITY
  if (am < distanceFor(height) && !fast) return 'cancel'
  return main < 0 ? 'next' : 'prev'
}

/**
 * How far the picture follows the finger.
 *
 * One-to-one up to a point, then increasingly reluctant. The rubber-banding
 * matters at the edges of the gesture: without it a long drag carries the
 * photo right off the screen and there is nothing left to spring back.
 */
export function follow(dy, height = 800) {
  const limit = height * 0.5
  const a = Math.abs(dy)
  if (a <= limit) return dy
  const over = a - limit
  const eased = limit + over / (1 + over / limit)
  return dy < 0 ? -eased : eased
}

export const MIN_OPACITY = 0.45

/** Fades the outgoing picture as it travels, so the swap is not abrupt. */
export function fade(dy, height = 800) {
  const t = Math.min(1, Math.abs(dy) / (height * 0.5))
  // an explicit floor rather than trusting `1 - t * 0.55` to land on it:
  // that expression bottoms out at 0.44999999999999996
  return Math.max(MIN_OPACITY, 1 - t * (1 - MIN_OPACITY))
}
