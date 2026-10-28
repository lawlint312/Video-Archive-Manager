// Which layout the screen gets: phone or desktop, and phone held sideways.
//
// Every mobile rule used to be a width-only 760px media query. A phone turned
// sideways is 844–932px wide (iPhone 13 onwards), so in landscape the whole
// site fell back to the desktop layout: the 260px sidebar stayed pinned, the
// header and status bar took 80px of a 390px-tall screen, the player's close
// button was pushed 5px off the top edge, and the photo viewer's swipe was
// switched off because it was gated on the same width.
//
// So "phone" is now: narrow, OR a touch screen that is short. The height test
// is what separates a phone on its side from a tablet or a touch laptop, and
// it leaves every desktop rule exactly as it was (a mouse is `pointer: fine`).
//
// CSS cannot import a constant, so the same text is repeated in every
// `@media` block; `tests/mobile.test.mjs` checks that none of them drifted.
export const MOBILE_MQ = '(max-width: 760px), (pointer: coarse) and (max-height: 500px)'
export const LANDSCAPE_MQ = '(pointer: coarse) and (max-height: 500px) and (orientation: landscape)'

import { reactive } from 'vue'

/** Live answers, for behaviour that CSS cannot express (gestures, markup). */
export const screen = reactive({ mobile: false, landscape: false })

if (typeof window !== 'undefined' && window.matchMedia) {
  const m = window.matchMedia(MOBILE_MQ)
  const l = window.matchMedia(LANDSCAPE_MQ)
  const sync = () => { screen.mobile = m.matches; screen.landscape = l.matches }
  sync()
  // watched rather than read once: turning the phone crosses these without
  // anything being reopened
  m.addEventListener?.('change', sync)
  l.addEventListener?.('change', sync)
}
