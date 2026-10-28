// Presenting the javdatabase profile block.
//
// Shared by the profile tab and the 補個資 preview so both render a date the
// same way. javdatabase records plenty of birthdays as year-only
// ("2000-??-??"); printing that verbatim is noise, so a year-only date shows
// as just the year and its age is marked approximate.

export const FIELD_LABELS = {
  dob: '生日',
  height: '身高',
  measurements: '三圍',
  cup: '罩杯',
  birthplace: '出生地',
}

export const FIELD_ORDER = ['dob', 'height', 'measurements', 'cup', 'birthplace']

const DOB_RE = /^(\d{4})-(\?\?|\d{2})-(\?\?|\d{2})$/

export function parseDob(dob) {
  const m = DOB_RE.exec((dob || '').trim())
  if (!m) return null
  const y = +m[1]
  if (y < 1900 || y > new Date().getFullYear()) return null
  const mo = m[2] === '??' ? null : +m[2]
  const d = m[3] === '??' ? null : +m[3]
  if (mo !== null && d !== null) {
    const probe = new Date(y, mo - 1, d)
    // rejects 2000-13-40 and 29 Feb in a common year
    if (probe.getFullYear() !== y || probe.getMonth() !== mo - 1
        || probe.getDate() !== d) {
      return { y, mo: null, d: null }
    }
  } else if (mo !== null && (mo < 1 || mo > 12)) {
    return { y, mo: null, d: null }
  }
  return { y, mo, d }
}

/** "1995-03-29" stays; "2000-??-??" becomes "2000"; junk becomes ''. */
export function formatDob(dob) {
  const p = parseDob(dob)
  if (!p) return ''
  if (p.mo === null || p.d === null) return String(p.y)
  return `${p.y}-${String(p.mo).padStart(2, '0')}-${String(p.d).padStart(2, '0')}`
}

/**
 * Age is always derived — storing it would be wrong within a year. Year-only
 * dates give an answer that is off by at most one, so it is labelled 約.
 */
export function formatAge(dob, now = new Date()) {
  const p = parseDob(dob)
  if (!p) return ''
  if (p.mo === null || p.d === null) return `約 ${now.getFullYear() - p.y} 歲`
  let age = now.getFullYear() - p.y
  const had = (now.getMonth() + 1) > p.mo
           || ((now.getMonth() + 1) === p.mo && now.getDate() >= p.d)
  if (!had) age--
  return `${age} 歲`
}

/**
 * Cells for the stat row: [{key, label, value, note?}], empty fields omitted.
 *
 * Age rides along inside the birthday cell rather than owning one of its own.
 * It is derived from the birthday, so presenting them as two equally-weighted
 * boxes said the same thing twice — and the second box cost width the row did
 * not have.
 *
 * It travels as `note` rather than being pasted into `value`, because the two
 * are styled differently: the date carries the cell's weight and the age is a
 * quiet annotation beside it. Bracketing it into the value made the age as
 * loud as the date.
 */
export function profileCells(profile, now = new Date()) {
  if (!profile) return []
  const out = []
  for (const k of FIELD_ORDER) {
    const value = k === 'dob' ? formatDob(profile.dob) : profile[k]
    if (!value) continue
    const cell = { key: k, label: FIELD_LABELS[k], value }
    // both sides come from the same parseDob, so a formattable date always
    // has an age to go with it
    if (k === 'dob') {
      const age = formatAge(profile.dob, now)
      if (age) cell.note = age
    }
    out.push(cell)
  }
  return out
}
